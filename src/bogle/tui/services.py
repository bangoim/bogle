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

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from bogle import format as fmt
from bogle.analytics.business_days import previous_business_day
from bogle.data import default_dispatcher
from bogle.db import get_connection
from bogle.domain.transactions import Transaction, TransactionType
from bogle.rebalancing import overdue_notice
from bogle.reports.overview import PortfolioOverview, compute_overview
from bogle.reports.snapshot import PortfolioSnapshot, compute_snapshot
from bogle.repositories.assets import AssetRepository
from bogle.repositories.transactions import TransactionRepository
from bogle.settings import (
    DECIMAL_SEPARATOR,
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
