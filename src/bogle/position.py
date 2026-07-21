"""On-the-fly portfolio position (issue #19).

Joins the persisted holdings/transactions with live prices (:class:`PriceDispatcher`)
and the TWR engine to produce, per ticker: current price, quantity, market value,
weight vs target (drift), invested capital, nominal PnL (R$ and %), dividends
received, time-weighted return, and the price's source/timestamp. Nothing is
persisted — it is recomputed on demand.

Pass ``dispatcher=None`` for a base-data-only view (no API calls): the
market-dependent fields come back ``None``. Otherwise it degrades gracefully — a
ticker whose price cannot be fetched reports ``None`` and drops out of the totals,
rather than failing the whole portfolio.
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
    price could not be fetched (or in a no-prices view)."""

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
    price_source: str | None = None
    as_of: datetime | None = None


@dataclass(frozen=True, slots=True)
class PortfolioSummary:
    positions: list[Position]
    total_value: Decimal
    total_invested: Decimal
    total_pnl: Decimal
    total_dividends: Decimal

    @property
    def total_pnl_percent(self) -> Decimal | None:
        return self.total_pnl / self.total_invested if self.total_invested > _ZERO else None


@dataclass(frozen=True, slots=True)
class _Priced:
    holding: Holding
    dividends: Decimal
    price: Decimal | None = None
    value: Decimal | None = None
    source: str | None = None
    as_of: datetime | None = None
    twr: Decimal | None = None


def _to_date(value: date) -> date:
    return value.date() if isinstance(value, datetime) else value


def _dividends(transactions: list[Transaction]) -> Decimal:
    return sum((t.total_investment for t in transactions if t.transaction_type in _INCOME_TYPES), _ZERO)


def _price(
    dispatcher: PriceDispatcher, asset: Asset, quantity: Decimal, unit_principal: Decimal, on_date: date
) -> tuple[Decimal | None, Decimal | None, str | None, datetime | None]:
    try:
        info = dispatcher.get_price_info(asset, principal=unit_principal, on_date=on_date)
    except (BogleError, ValueError):
        return None, None, None, None
    return info.price, quantity * info.price, info.source, info.as_of


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
    conn: psycopg.Connection[DictRow], dispatcher: PriceDispatcher | None = None, *, on_date: date | None = None
) -> PortfolioSummary:
    """Recompute every active position and the portfolio totals.

    With ``dispatcher=None`` returns base data only (no prices, weights or PnL).
    """
    today = on_date if on_date is not None else date.today()
    holdings = HoldingRepository(conn).list()
    assets = AssetRepository(conn)
    transactions = TransactionRepository(conn)

    priced: list[_Priced] = []
    for holding in holdings:
        asset = assets.get(holding.ticker)
        if asset is None:  # a holding always has an asset row (FK); defensive
            continue
        txns = transactions.list(holding.ticker)
        dividends = _dividends(txns)
        if dispatcher is None:
            priced.append(_Priced(holding, dividends))
            continue
        quantity = holding.total_shares
        unit_principal = holding.total_invested / quantity if quantity != _ZERO else _ZERO
        price, value, source, as_of = _price(dispatcher, asset, quantity, unit_principal, today)
        twr = _twr(dispatcher, asset, txns, unit_principal, today)
        priced.append(_Priced(holding, dividends, price, value, source, as_of, twr))

    total_value = sum((p.value for p in priced if p.value is not None), _ZERO)

    positions: list[Position] = []
    total_invested = _ZERO
    total_pnl = _ZERO
    total_dividends = _ZERO
    for p in priced:
        holding = p.holding
        total_invested += holding.total_invested
        total_dividends += p.dividends
        current_weight = p.value / total_value if p.value is not None and total_value > _ZERO else None
        drift = current_weight - holding.target_weight if current_weight is not None else None
        pnl = p.value - holding.total_invested if p.value is not None else None
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
                dividends=p.dividends,
                price=p.price,
                market_value=p.value,
                current_weight=current_weight,
                drift=drift,
                pnl=pnl,
                pnl_percent=pnl_percent,
                twr=p.twr,
                price_source=p.source,
                as_of=p.as_of,
            )
        )
    return PortfolioSummary(positions, total_value, total_invested, total_pnl, total_dividends)


def get_positions(
    conn: psycopg.Connection[DictRow], dispatcher: PriceDispatcher | None = None, *, on_date: date | None = None
) -> list[Position]:
    return get_portfolio_summary(conn, dispatcher, on_date=on_date).positions
