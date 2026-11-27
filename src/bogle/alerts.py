"""What is pending in the portfolio, for a scheduler to report (issue #108).

``bogle check`` runs from launchd or cron, with nobody at the terminal. What the
other commands print on stderr before running — the overdue evaluation cycle and
the pending annual rebalance — and what the Home screen notes about prices that
fell behind are gathered here as a list of lines, which the command prints and,
with ``--notify``, turns into a macOS notification.

Nothing is decided here: each line comes from the rule the frontends already
apply (:func:`~bogle.rebalancing.overdue_notice`,
:func:`~bogle.rebalancing.annual_rebalance_notice` and
:attr:`~bogle.reports.overview.PortfolioOverview.stale_prices`). The glide path
of 01/01 is not a pendency: it applies itself, and says so, on the next run.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

import psycopg
from psycopg.rows import DictRow

from bogle.annual_rebalance import load_annual_state
from bogle.rebalancing import annual_rebalance_notice, overdue_notice
from bogle.reports.overview import PortfolioOverview
from bogle.settings import LAST_REBALANCE_DATE, REBALANCE_PERIOD_MONTHS, get_setting

NOTHING_PENDING = 0
PENDING = 1
CHECK_FAILED = 3
"""Exit codes of ``bogle check``. 2 stays with Typer, for a bad command line."""


@dataclass(frozen=True, slots=True)
class CheckResult:
    alerts: list[str] = field(default_factory=list)
    """One line per pendency, in the order the user would deal with them."""
    errors: list[str] = field(default_factory=list)
    """What could not be checked. Alerts found before the failure stay."""

    @property
    def exit_code(self) -> int:
        """Failed beats pending: a partial check cannot say there is nothing else."""
        if self.errors:
            return CHECK_FAILED
        return PENDING if self.alerts else NOTHING_PENDING


def _sentence(notice: str) -> str:
    """A stderr notice (``aviso: ...``) as a line of its own."""
    # Sem ponto final: as linhas de uma lista numerada terminam todas igual.
    return (notice[:1].upper() + notice[1:]).removesuffix(".")


def settings_alerts(conn: psycopg.Connection[DictRow], today: date) -> list[str]:
    """The evaluation cycle, then the annual rebalance: the two reminders of the CLI."""
    notices = (
        overdue_notice(get_setting(conn, LAST_REBALANCE_DATE), get_setting(conn, REBALANCE_PERIOD_MONTHS), today=today),
        annual_rebalance_notice(load_annual_state(conn, today)),
    )
    return [_sentence(notice) for notice in notices if notice is not None]


def stale_prices_alert(overview: PortfolioOverview) -> str | None:
    """The tickers the Home summary prices at an older close, with that close."""
    if not overview.has_stale_prices:
        return None
    listed = ", ".join(f"{ticker} ({when.isoformat()})" for ticker, when in sorted(overview.stale_prices.items()))
    return f"Preços desatualizados: {listed}"


def listing(lines: Sequence[str]) -> list[str]:
    """One line as it is; several, numbered."""
    if len(lines) == 1:
        return list(lines)
    return [f"{index}. {line}" for index, line in enumerate(lines, start=1)]


def notification_text(result: CheckResult) -> str:
    """The body of the notification: the pendencies, then what failed."""
    return "\n".join(listing([*result.alerts, *(f"Erro: {error}" for error in result.errors)]))
