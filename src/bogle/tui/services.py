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
from bogle.domain.assets import Asset, AssetType, Indexer
from bogle.domain.errors import AssetNotFoundError
from bogle.domain.transactions import Transaction, TransactionType
from bogle.domain.validation import validate_asset_metadata, validate_type_change
from bogle.position import get_portfolio_summary
from bogle.rebalancing import AporteSuggestion, next_evaluation_date, overdue_notice, suggest_allocation
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
    SettingEntry,
    get_setting,
    list_settings,
    set_setting,
    set_value,
    unset_setting,
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


# ---------------------------------------------------------------------- ativos


def list_assets() -> list[Asset]:
    """Every registered asset, ticker order — the rows of ``bogle list``."""
    conn = get_connection()
    try:
        return AssetRepository(conn).list()
    finally:
        conn.close()


def add_asset(
    *,
    ticker: str,
    target_weight: Decimal,
    asset_type: AssetType,
    issuer: str | None = None,
    indexer: Indexer | None = None,
    rate: Decimal | None = None,
    is_prefixed: bool | None = None,
    daily_liquidity: bool | None = None,
    purchase_date: datetime | None = None,
    maturity_date: datetime | None = None,
) -> Asset:
    """Register an asset, validating the field combination for its type first.

    The domain validator runs here, not in the form: it is the same last line of
    defense ``bogle add`` uses, and its aggregated message mentions CLI flags. The
    form aims to never reach it — it only shows the fields the type accepts and
    marks the required ones as it is typed.
    """
    metadata = validate_asset_metadata(
        asset_type,
        issuer=issuer,
        indexer=indexer,
        rate=rate,
        is_prefixed=is_prefixed,
        daily_liquidity=daily_liquidity,
        purchase_date=purchase_date,
        maturity_date=maturity_date,
    )
    conn = get_connection()
    try:
        return AssetRepository(conn).add(
            ticker,
            target_weight,
            asset_type=asset_type,
            issuer=metadata.issuer,
            indexer=metadata.indexer,
            rate=metadata.rate,
            is_prefixed=metadata.is_prefixed,
            daily_liquidity=metadata.daily_liquidity,
            purchase_date=metadata.purchase_date,
            maturity_date=metadata.maturity_date,
        )
    finally:
        conn.close()


def update_asset(*, ticker: str, target_weight: Decimal | None = None, asset_type: AssetType | None = None) -> Asset:
    """Change the target weight and/or the type, exactly as ``bogle update`` does.

    A type change is only sound between variable-income types (they carry no
    metadata); ``validate_type_change`` refuses anything that would leave
    metadata missing or orphaned.
    """
    conn = get_connection()
    try:
        repo = AssetRepository(conn)
        asset = repo.get(ticker)
        if asset is None:
            raise AssetNotFoundError(ticker.upper())
        if asset_type is not None and asset_type != asset.asset_type:
            validate_type_change(asset.ticker, asset.asset_type, asset_type)
            asset = repo.update_type(ticker, asset_type)
        if target_weight is not None:
            asset = repo.update_weight(ticker, target_weight)
        return asset
    finally:
        conn.close()


def remove_asset(ticker: str) -> None:
    conn = get_connection()
    try:
        AssetRepository(conn).remove(ticker)
    finally:
        conn.close()


# ---------------------------------------------------------------------- aporte


def load_suggestion(amount: Decimal, *, today: date | None = None) -> AporteSuggestion:
    """How to split ``amount`` to shrink drift, recording the evaluation.

    Suggesting a contribution *is* the rebalance cycle's evaluation (issue #24),
    so it stamps ``last_rebalance_date`` — the same side effect ``bogle suggest``
    has, which is what makes the overdue reminder stop nagging.
    """
    conn = get_connection()
    try:
        summary = get_portfolio_summary(conn, default_dispatcher())
        suggestion = suggest_allocation(summary, amount)
        set_value(conn, LAST_REBALANCE_DATE, _today(today))
        return suggestion
    finally:
        conn.close()


# ---------------------------------------------------------------------- status


@dataclass(frozen=True, slots=True)
class CycleStatus:
    """Where the rebalance evaluation cycle stands, as ``bogle status`` reports it."""

    period_months: int
    last_evaluation: date | None
    """``None`` = never evaluated; ``bogle suggest`` (or the Aporte screen) records the first."""
    next_evaluation: date | None
    days: int | None
    """Days until the next evaluation; negative means overdue."""


def load_cycle(*, today: date | None = None) -> CycleStatus:
    conn = get_connection()
    try:
        period = get_setting(conn, REBALANCE_PERIOD_MONTHS)
        last = get_setting(conn, LAST_REBALANCE_DATE)
    finally:
        conn.close()
    if last is None:
        return CycleStatus(period_months=period, last_evaluation=None, next_evaluation=None, days=None)
    next_evaluation = next_evaluation_date(last, period)
    return CycleStatus(
        period_months=period,
        last_evaluation=last,
        next_evaluation=next_evaluation,
        days=(next_evaluation - _today(today)).days,
    )


# --------------------------------------------------------------- configuracoes


def load_settings() -> list[SettingEntry]:
    """Every supported key with its current value — the rows of ``bogle config list``."""
    conn = get_connection()
    try:
        return list_settings(conn)
    finally:
        conn.close()


def save_setting(key: str, raw: str) -> object:
    """Parse and store one setting, returning the typed value it became."""
    conn = get_connection()
    try:
        return set_setting(conn, key, raw)
    finally:
        conn.close()


def reset_setting(key: str) -> object:
    """Drop the row so ``key`` goes back to its default, and return that default."""
    conn = get_connection()
    try:
        unset_setting(conn, key)
        return get_setting(conn, key)
    finally:
        conn.close()
