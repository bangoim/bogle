"""Blocking data access for the TUI (issue #73).

Every function here is synchronous and slow on purpose: it opens a connection,
talks to the database (and sometimes to the price APIs) and closes it again —
the same open-per-operation pattern the CLI commands use, which avoids an
App-held connection going stale after an idle period. The screens are
responsible for calling them inside a Textual worker thread.

This module is also the seam the tests use: screens call ``services.load_x()``
through the module, never through a direct import, so a fake can be patched in
and the interface tested without a database or a network.
"""

from __future__ import annotations

from datetime import date

from bogle.analytics.business_days import previous_business_day
from bogle.data import default_dispatcher
from bogle.db import get_connection
from bogle.rebalancing import overdue_notice
from bogle.reports.overview import PortfolioOverview, compute_overview
from bogle.reports.snapshot import PortfolioSnapshot, compute_snapshot
from bogle.settings import LAST_REBALANCE_DATE, REBALANCE_PERIOD_MONTHS, get_setting


def _today(today: date | None) -> date:
    return today if today is not None else date.today()


def overview_date(today: date | None = None) -> date:
    """Reference date of the Home summary: the close before ``today`` (D-1)."""
    return previous_business_day(_today(today))


def load_overview(*, today: date | None = None) -> PortfolioOverview:
    """The four headline numbers, measured at the previous close."""
    conn = get_connection()
    try:
        return compute_overview(conn, default_dispatcher(), as_of=overview_date(today))
    finally:
        conn.close()


def load_snapshot(*, with_prices: bool, today: date | None = None) -> PortfolioSnapshot:
    """The current position. ``with_prices=False`` is the ``--no-prices`` view."""
    conn = get_connection()
    try:
        return compute_snapshot(conn, default_dispatcher() if with_prices else None, today=_today(today))
    finally:
        conn.close()


def rebalance_notice(*, today: date | None = None) -> str | None:
    """The overdue evaluation-cycle reminder, or ``None`` when nothing is due.

    Best-effort, like the CLI's reminder: a failure here (database down,
    migrations pending) must never keep the interface from opening.
    """
    try:
        conn = get_connection()
        try:
            period = get_setting(conn, REBALANCE_PERIOD_MONTHS)
            last = get_setting(conn, LAST_REBALANCE_DATE)
        finally:
            conn.close()
    except Exception:
        return None
    return overdue_notice(last, period, today=_today(today))
