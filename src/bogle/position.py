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

Two views over the same data, and the difference matters:
:func:`get_portfolio_summary` is what you *have* (the ``holdings`` view, which
only lists open positions), while :func:`get_allocation_summary` is what you
*want* — the same positions plus the assets that so far are only a target weight.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import DictRow

from bogle.analytics.twr import compute_twr
from bogle.data.dispatcher import PriceDispatcher
from bogle.db import DEFAULT_TIMEZONE
from bogle.domain.assets import PRIVATE_FIXED_INCOME_TYPES, Asset, AssetType
from bogle.domain.cost_basis import replay_cost_basis
from bogle.domain.errors import BogleError, ValidationError
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
    average_price: Decimal | None = None
    """Weighted-average cost of the units still held, fees included (the RFB's
    "preco medio"). It is *not* ``total_invested / quantity``: the holdings view
    nets sale proceeds out of the invested capital, so after a partial sale that
    division stops being the cost of what is left. ``None`` when the history is
    inconsistent enough that the replay refuses it."""
    price: Decimal | None = None
    market_value: Decimal | None = None
    current_weight: Decimal | None = None
    drift: Decimal | None = None
    pnl: Decimal | None = None
    pnl_percent: Decimal | None = None
    twr: Decimal | None = None
    price_source: str | None = None
    as_of: datetime | None = None


def local_time(value: datetime) -> datetime:
    """A quote's timestamp in the timezone the user reads it in.

    Providers stamp in UTC (brapi's ``regularMarketTime``, yfinance's fetch time),
    and printing that raw puts an afternoon quote three hours in the future —
    which defeats the one question a timestamp beside a price answers.
    """
    return value.astimezone(ZoneInfo(DEFAULT_TIMEZONE))


@dataclass(frozen=True, slots=True)
class Provenance:
    """Where the prices on a screen came from, and how old the freshest one is."""

    sources: list[str]
    latest: datetime | None
    """Freshest quote, already in local time; ``None`` when nothing carries a
    timestamp (a computed fixed-income value)."""


def price_provenance(rows: Iterable[tuple[str | None, datetime | None]]) -> Provenance:
    """Fold ``(source, as_of)`` pairs into what a footer shows.

    Shared by the position and contribution views, in both frontends: a price on
    screen without a source and a timestamp reads as "now", and none of them is
    (brapi's free plan is delayed, and quotes are cached for five minutes).
    """
    pairs = list(rows)
    latest = max((as_of for _, as_of in pairs if as_of is not None), default=None)
    return Provenance(
        sources=sorted({source for source, _ in pairs if source}),
        latest=local_time(latest) if latest is not None else None,
    )


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
    average_price: Decimal | None = None
    price: Decimal | None = None
    value: Decimal | None = None
    source: str | None = None
    as_of: datetime | None = None
    twr: Decimal | None = None


def _to_date(value: date) -> date:
    return value.date() if isinstance(value, datetime) else value


def _dividends(transactions: list[Transaction]) -> Decimal:
    return sum((t.total_investment for t in transactions if t.transaction_type in _INCOME_TYPES), _ZERO)


def _average_price(ticker: str, transactions: list[Transaction]) -> Decimal | None:
    """The RFB average cost of the units still held, from the sequential replay.

    Degrades to ``None`` instead of raising: a history the replay refuses (a sale
    larger than the position at the time) is a real problem, but it is
    ``bogle profit``'s job to say so — the position view exists to show the rest
    of the portfolio, and it already renders what it cannot compute as a dash.
    """
    try:
        states, _ = replay_cost_basis(transactions)
    except ValidationError:
        return None
    state = states.get(ticker)
    return state.average_cost if state is not None else None


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
        average = _average_price(holding.ticker, txns)
        if dispatcher is None:
            priced.append(_Priced(holding, dividends, average))
            continue
        quantity = holding.total_shares
        unit_principal = holding.total_invested / quantity if quantity != _ZERO else _ZERO
        price, value, source, as_of = _price(dispatcher, asset, quantity, unit_principal, today)
        twr = _twr(dispatcher, asset, txns, unit_principal, today)
        priced.append(_Priced(holding, dividends, average, price, value, source, as_of, twr))

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
                average_price=p.average_price,
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


def _target_price(
    dispatcher: PriceDispatcher, asset: Asset, on_date: date
) -> tuple[Decimal | None, str | None, datetime | None]:
    """The unit price of an asset nobody owns yet, or ``None`` when unquotable."""
    if asset.asset_type in PRIVATE_FIXED_INCOME_TYPES:
        # Renda fixa privada nao tem preco unitario: o dispatcher devolve o valor
        # presente de um principal, e o de um contrato que ainda nao existe e
        # zero. Perguntar isso ao BCB seria uma chamada de rede para chegar a 0.
        return _ZERO, None, None
    try:
        info = dispatcher.get_price_info(asset, principal=_ZERO, on_date=on_date)
    except (BogleError, ValueError):
        return None, None, None
    return info.price, info.source, info.as_of


def _pending_position(dispatcher: PriceDispatcher, asset: Asset, total_value: Decimal, *, on_date: date) -> Position:
    """A target with nothing behind it yet, shaped as a position worth nothing."""
    price, source, as_of = _target_price(dispatcher, asset, on_date)
    # Peso de zero sobre um patrimonio zero nao existe — a mesma regra que
    # get_portfolio_summary aplica quando nao ha valor de mercado nenhum.
    current_weight = _ZERO if total_value > _ZERO else None
    return Position(
        ticker=asset.ticker,
        asset_type=asset.asset_type,
        quantity=_ZERO,
        total_invested=_ZERO,
        target_weight=asset.target_weight,
        dividends=_ZERO,
        price=price,
        market_value=_ZERO,
        current_weight=current_weight,
        drift=-asset.target_weight if current_weight is not None else None,
        price_source=source,
        as_of=as_of,
    )


def get_allocation_summary(
    conn: psycopg.Connection[DictRow], dispatcher: PriceDispatcher, *, on_date: date | None = None
) -> PortfolioSummary:
    """Every position *plus* the assets that exist only as a target weight.

    An asset registered with a target and no open position — never bought, or
    sold down to zero — has no row in the ``holdings`` view, so
    :func:`get_portfolio_summary` never sees it. For the contribution engine that
    absence is the whole bug: a target of 10% that receives nothing is not a
    target, and the first purchase of a ticker would have to happen outside the
    tool. Here they come back as positions worth nothing (quantity 0, market
    value 0, ``current_weight`` 0), which is what makes
    :func:`~bogle.rebalancing.suggest_allocation` measure their need exactly like
    everyone else's — a zero is still a distance from the target.

    The totals are the portfolio's own, untouched: a position worth nothing adds
    nothing to the patrimony, to the invested capital, to the PnL or to the
    dividends. This view is for splitting a contribution, never for reporting
    what the portfolio *is* — that is what :func:`get_portfolio_summary` says,
    and it is what every screen and report keeps calling.
    """
    summary = get_portfolio_summary(conn, dispatcher, on_date=on_date)
    held = {position.ticker for position in summary.positions}
    pending = [
        _pending_position(dispatcher, asset, summary.total_value, on_date=on_date or date.today())
        for asset in AssetRepository(conn).list()
        if asset.ticker not in held and asset.target_weight > _ZERO
    ]
    if not pending:
        return summary
    return replace(summary, positions=sorted([*summary.positions, *pending], key=lambda p: p.ticker))
