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

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import gettempdir

from bogle import charts
from bogle import format as fmt
from bogle.analytics.business_days import previous_business_day
from bogle.data import default_dispatcher
from bogle.db import get_connection
from bogle.domain.transactions import Transaction, TransactionType
from bogle.position import get_portfolio_summary
from bogle.rebalancing import overdue_notice
from bogle.reports.compare import CompareReport, compute_compare
from bogle.reports.dividends import (
    MonthlyIncome,
    TickerIncome,
    income_by_month,
    income_by_ticker,
    income_window_start,
)
from bogle.reports.history import HistoryReport, compute_history
from bogle.reports.overview import PortfolioOverview, compute_overview
from bogle.reports.profit import ProfitReport, compute_profit
from bogle.reports.returns import ReturnsReport, compute_returns
from bogle.reports.snapshot import PortfolioSnapshot, compute_snapshot
from bogle.repositories.assets import AssetRepository
from bogle.repositories.transactions import TransactionRepository
from bogle.settings import (
    DECIMAL_SEPARATOR,
    DEFAULT_COMPARE_INDICES,
    DEFAULT_THEME,
    HIDE_VALUES,
    LAST_REBALANCE_DATE,
    REBALANCE_PERIOD_MONTHS,
    THEME,
    get_setting,
    set_value,
)

_ZERO = Decimal("0")


def _today(today: date | None) -> date:
    return today if today is not None else date.today()


@dataclass(frozen=True, slots=True)
class Preferences:
    """How the interface should open, from ``user_settings``."""

    decimal_separator: str = fmt.CANONICAL_DECIMAL
    hide_amounts: bool = False
    theme: str = DEFAULT_THEME


def load_preferences() -> Preferences:
    """Read the display preferences, falling back to the defaults.

    Read synchronously before the app opens: it is a single local query, and
    doing it in a worker would race with the first screen's rendering — a screen
    that renders amounts before the privacy mode lands would show exactly what it
    was asked to hide. Best-effort, like the CLI's: a database that is down opens
    with the defaults, and the Home screen reports the failure anyway.
    """
    try:
        conn = get_connection()
        try:
            return Preferences(
                decimal_separator=get_setting(conn, DECIMAL_SEPARATOR),
                hide_amounts=get_setting(conn, HIDE_VALUES),
                theme=get_setting(conn, THEME),
            )
        finally:
            conn.close()
    except Exception:
        return Preferences()


def save_hide_amounts(hidden: bool) -> None:
    """Remember the privacy mode, so the next session opens the same way."""
    conn = get_connection()
    try:
        set_value(conn, HIDE_VALUES, hidden)
    finally:
        conn.close()


def save_theme(theme: str) -> None:
    """Remember the theme picked in the command palette."""
    conn = get_connection()
    try:
        set_value(conn, THEME, theme)
    finally:
        conn.close()


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


def list_tickers() -> list[str]:
    """Registered tickers, for the forms' autocomplete."""
    conn = get_connection()
    try:
        return [asset.ticker for asset in AssetRepository(conn).list()]
    finally:
        conn.close()


def load_transactions() -> list[Transaction]:
    """The whole ledger. The Transactions screen filters it client-side, so that
    typing in the filter never waits on a round trip."""
    conn = get_connection()
    try:
        return TransactionRepository(conn).list()
    finally:
        conn.close()


def delete_transaction(transaction_id: int) -> None:
    conn = get_connection()
    try:
        TransactionRepository(conn).delete(transaction_id)
    finally:
        conn.close()


def record_buy(*, ticker: str, when: datetime, shares: Decimal, unit_price: Decimal, fees: Decimal) -> Transaction:
    conn = get_connection()
    try:
        return TransactionRepository(conn).add_buy(ticker, when, shares=shares, unit_price=unit_price, fees=fees)
    finally:
        conn.close()


def record_sell(
    *,
    ticker: str,
    when: datetime,
    shares: Decimal,
    unit_price: Decimal,
    fees: Decimal,
    tax_withheld: Decimal,
) -> Transaction:
    conn = get_connection()
    try:
        return TransactionRepository(conn).add_sale(
            ticker, when, shares=shares, unit_price=unit_price, fees=fees, tax_withheld=tax_withheld
        )
    finally:
        conn.close()


def record_income(
    *,
    ticker: str,
    income_type: TransactionType,
    when: datetime,
    amount: Decimal,
    tax_withheld: Decimal | None = None,
) -> Transaction:
    """Record an income event, routing to the repository method for its type.

    The rule the CLI enforces with flags (JCP always has tax withheld at source,
    RENDIMENTO is exempt for individuals) is enforced by the form, which enables
    or disables the field per type.
    """
    tax = tax_withheld if tax_withheld is not None else _ZERO
    conn = get_connection()
    try:
        repo = TransactionRepository(conn)
        if income_type is TransactionType.DIVIDEND:
            return repo.add_dividend(ticker, when, amount, tax_withheld=tax)
        if income_type is TransactionType.JCP:
            return repo.add_jcp(ticker, when, amount, tax)
        if income_type is TransactionType.RENDIMENTO:
            return repo.add_rendimento(ticker, when, amount)
        if income_type is TransactionType.INTEREST:
            return repo.add_interest(ticker, when, amount, tax_withheld=tax)
        raise ValueError(f"tipo de provento invalido: {income_type}")
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


# ------------------------------------------------------------------ relatorios


@dataclass(frozen=True, slots=True)
class IncomeReport:
    """Income over one window, in both groupings the screen switches between.

    One ledger read serves both: whether to look at income per month or per
    ticker is a display choice, and it must not cost a round trip.
    """

    start: date | None
    """``None`` = since inception."""
    end: date
    by_month: list[MonthlyIncome]
    by_ticker: list[TickerIncome]


def default_indices() -> tuple[str, ...]:
    """The indices ``bogle compare`` uses without ``--index``."""
    conn = get_connection()
    try:
        return tuple(get_setting(conn, DEFAULT_COMPARE_INDICES))
    finally:
        conn.close()


def load_returns(*, indices: tuple[str, ...] = (), today: date | None = None) -> ReturnsReport:
    """TWR over the three windows at once — the ``bogle return`` panel."""
    conn = get_connection()
    try:
        return compute_returns(conn, default_dispatcher(), indices=indices, today=_today(today))
    finally:
        conn.close()


def load_compare(*, period: str, indices: tuple[str, ...], today: date | None = None) -> CompareReport:
    """Portfolio vs indices, base 100 at the start of the window."""
    conn = get_connection()
    try:
        return compute_compare(conn, default_dispatcher(), period=period, indices=indices, today=_today(today))
    finally:
        conn.close()


def load_history(*, period: str, today: date | None = None) -> HistoryReport:
    """Patrimony sampled over the window's grid."""
    conn = get_connection()
    try:
        return compute_history(conn, default_dispatcher(), period=period, today=_today(today))
    finally:
        conn.close()


def load_profit(*, period: str, today: date | None = None) -> ProfitReport:
    """Capital gain (always since inception) plus income over ``period``."""
    now = _today(today)
    conn = get_connection()
    try:
        summary = get_portfolio_summary(conn, default_dispatcher())
        transactions = TransactionRepository(conn).list()
    finally:
        conn.close()
    return compute_profit(summary, transactions, income_start=income_window_start(period, now), income_end=now)


def load_income(*, period: str, today: date | None = None) -> IncomeReport:
    """Income received over ``period``, grouped both ways."""
    now = _today(today)
    start = income_window_start(period, now)
    conn = get_connection()
    try:
        transactions = TransactionRepository(conn).list()
    finally:
        conn.close()
    return IncomeReport(
        start=start,
        end=now,
        by_month=income_by_month(transactions, start=start, end=now),
        by_ticker=income_by_ticker(transactions, start=start, end=now),
    )


def chart_path(name: str) -> Path:
    """Where an exported chart is written: a stable name in the temp directory.

    A stable name (instead of a timestamped one) means asking twice overwrites
    the file rather than littering, and the toast can always say where it went.
    """
    return Path(gettempdir()) / f"bogle-{name}.html"


def export_chart(
    *,
    title: str,
    x_values: Sequence[object],
    series: charts.Series,
    path: Path,
    y_title: str = "",
    y_suffix: str = "",
) -> Path:
    """Write the interactive HTML chart and open it in the browser.

    Same output as ``--output`` on ``bogle compare``/``history``: it is the same
    function underneath. Blocking (a file write plus spawning a browser), hence
    a service and not something a screen does on the event loop.
    """
    charts.export_line_chart_html(title, list(x_values), series, str(path), y_title=y_title, y_suffix=y_suffix)
    charts.open_in_browser(str(path))
    return path
