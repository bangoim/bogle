"""On-the-fly portfolio position (issue #19).

Joins the persisted holdings/transactions with live prices (:class:`PriceDispatcher`)
and the TWR engine to produce, per ticker: current price, quantity, market value,
weight vs target (drift), invested capital, nominal PnL (R$ and %), dividends
received and time-weighted return. Nothing is persisted — it is recomputed on
demand.

Degrades gracefully: if a price (or TWR input) cannot be fetched, that field is
reported as ``None`` and the ticker drops out of the totals, rather than failing
the whole portfolio.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import psycopg
from psycopg.rows import DictRow

from bogle.analytics.twr import compute_twr
from bogle.data.dispatcher import PriceDispatcher
from bogle.domain.assets import Asset, AssetType
from bogle.domain.errors import BogleError
from bogle.domain.holdings import Holding
from bogle.domain.transactions import Transaction, TransactionType
from bogle.repositories.assets import AssetRepository
from bogle.repositories.holdings import HoldingRepository
from bogle.repositories.transactions import TransactionRepository

_ZERO = Decimal("0")
_INCOME_TYPES = frozenset(
    {
        TransactionType.DIVIDEND,
        TransactionType.JCP,
        TransactionType.RENDIMENTO,
        TransactionType.INTEREST,
    }
)


@dataclass(frozen=True, slots=True)
class Position:
    """A ticker's live position. Market-dependent fields are ``None`` when the
    price could not be fetched."""

    ticker: str
    asset_type: AssetType
    quantity: Decimal
    total_invested: Decimal
    target_weight: Decimal
    dividends: Decimal
    price: Decimal | None = None
    market_value: Decimal | None = None
    current_weight: Decimal | None = None
    drift: Decimal | None = None
    pnl: Decimal | None = None
    pnl_percent: Decimal | None = None
    twr: Decimal | None = None


@dataclass(frozen=True, slots=True)
class PortfolioSummary:
    positions: list[Position]
    total_value: Decimal
    total_invested: Decimal
    total_pnl: Decimal
    total_dividends: Decimal


def _to_date(value: date) -> date:
    return value.date() if isinstance(value, datetime) else value


def _dividends(transactions: list[Transaction]) -> Decimal:
    return sum((t.total_investment for t in transactions if t.transaction_type in _INCOME_TYPES), _ZERO)


def _price_and_value(
    dispatcher: PriceDispatcher, asset: Asset, quantity: Decimal, unit_principal: Decimal, on_date: date
) -> tuple[Decimal | None, Decimal | None]:
    # Uniform: value = quantity * per-unit price. get_price ignores `principal`
    # for market-priced assets and uses it (per unit) for private fixed income.
    try:
        price = dispatcher.get_price(asset, principal=unit_principal, on_date=on_date)
    except (BogleError, ValueError):
        return None, None
    return price, quantity * price


def _twr(
    dispatcher: PriceDispatcher, asset: Asset, transactions: list[Transaction], unit_principal: Decimal, on_date: date
) -> Decimal | None:
    if not transactions:
        return None
    start = min(_to_date(t.date) for t in transactions)
    try:
        valuator = dispatcher.build_twr_valuator(asset, unit_principal=unit_principal, start=start, end=on_date)
        if valuator is None:
            return None
        return compute_twr(transactions, None, start, on_date, valuator=valuator)
    except (BogleError, ValueError):
        return None


def get_portfolio_summary(
    conn: psycopg.Connection[DictRow], dispatcher: PriceDispatcher, *, on_date: date | None = None
) -> PortfolioSummary:
    """Recompute every active position and the portfolio totals."""
    today = on_date if on_date is not None else date.today()
    holdings = HoldingRepository(conn).list()
    assets = AssetRepository(conn)
    transactions = TransactionRepository(conn)

    # First pass: static fields + market value (needed before weights).
    priced: list[tuple[Holding, Decimal | None, Decimal | None, Decimal, Decimal | None]] = []
    for holding in holdings:
        asset = assets.get(holding.ticker)
        if asset is None:  # a holding always has an asset row (FK); defensive
            continue
        txns = transactions.list(holding.ticker)
        quantity = holding.total_shares
        unit_principal = (holding.total_invested / quantity) if quantity != _ZERO else _ZERO
        price, value = _price_and_value(dispatcher, asset, quantity, unit_principal, today)
        twr = _twr(dispatcher, asset, txns, unit_principal, today)
        priced.append((holding, price, value, _dividends(txns), twr))

    total_value = sum((value for _, _, value, _, _ in priced if value is not None), _ZERO)

    positions: list[Position] = []
    total_invested = _ZERO
    total_pnl = _ZERO
    total_dividends = _ZERO
    for holding, price, value, dividends, twr in priced:
        total_invested += holding.total_invested
        total_dividends += dividends
        current_weight = value / total_value if value is not None and total_value > _ZERO else None
        drift = current_weight - holding.target_weight if current_weight is not None else None
        pnl = value - holding.total_invested if value is not None else None
        pnl_percent = pnl / holding.total_invested if pnl is not None and holding.total_invested > _ZERO else None
        if pnl is not None:
            total_pnl += pnl
        positions.append(
            Position(
                ticker=holding.ticker,
                asset_type=holding.asset_type,
                quantity=holding.total_shares,
                total_invested=holding.total_invested,
                target_weight=holding.target_weight,
                dividends=dividends,
                price=price,
                market_value=value,
                current_weight=current_weight,
                drift=drift,
                pnl=pnl,
                pnl_percent=pnl_percent,
                twr=twr,
            )
        )
    return PortfolioSummary(positions, total_value, total_invested, total_pnl, total_dividends)


def get_positions(
    conn: psycopg.Connection[DictRow], dispatcher: PriceDispatcher, *, on_date: date | None = None
) -> list[Position]:
    return get_portfolio_summary(conn, dispatcher, on_date=on_date).positions
