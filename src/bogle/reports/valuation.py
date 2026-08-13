"""Portfolio-level historical valuation (issue #67).

Combines the per-asset valuators from :meth:`PriceDispatcher.build_twr_valuator`
into a single portfolio :data:`~bogle.analytics.twr.Valuator`, so the TWR engine
and the patrimony series work over the whole portfolio at once.

Tickers without a historical source (TESOURO — see #17 — a variable-income ticker
whose history fetch failed, or one whose series starts after the position does)
are **excluded**, together with their transactions, and reported in ``excluded``
so every consumer can warn the user instead of silently distorting values.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

import psycopg
from psycopg.rows import DictRow

from bogle.analytics.twr import Valuator, compute_twr, shares_held
from bogle.data.dispatcher import PriceDispatcher
from bogle.domain.assets import AssetType
from bogle.domain.errors import BogleError
from bogle.domain.transactions import Transaction
from bogle.repositories.assets import AssetRepository
from bogle.repositories.holdings import HoldingRepository
from bogle.repositories.transactions import TransactionRepository

_ZERO = Decimal("0")
_HISTORY_PAD = timedelta(days=7)  # bar "on or before start" even on weekends/holidays

NO_SOURCE = "sem fonte de historico gratuita"
"""TESOURO: nothing is wired, and nothing the user does changes it (see #17)."""

NOTHING_RETURNED = "o provedor nao devolveu historico"
"""The fetch came back empty (unknown symbol, provider down, network)."""

SHORT_SERIES = "a serie do provedor nao cobre o periodo da posicao"
"""Yahoo sometimes answers with just the last weeks; asking again often fixes it."""

RETRIABLE = frozenset({NOTHING_RETURNED, SHORT_SERIES})
"""The reasons worth trying again — the provider's, not the portfolio's."""

GRANULARITY_BY_PERIOD = {
    "1m": "daily",
    "12m": "daily",
    "ytd": "daily",
    "2y": "weekly",
    "5y": "monthly",
    "10y": "monthly",
    "all": "monthly",
    "total": "monthly",
}
_STEP_DAYS = {"daily": 1, "weekly": 7}


@dataclass(frozen=True, slots=True)
class PortfolioValuation:
    """Everything needed to value the (valuable part of the) portfolio over time."""

    valuator: Valuator | None
    """``None`` when no position has a historical source."""
    transactions: list[Transaction]
    """Only the transactions of tickers with history (excluded ones would corrupt TWR)."""
    excluded: list[str]
    reasons: dict[str, str]
    """Why each excluded ticker is out, keyed by ticker — the same names as
    ``excluded``. "No price history" covers three different situations, and only
    one of them is worth the user trying again."""
    start: date
    end: date


@dataclass(frozen=True, slots=True)
class PatrimonyPoint:
    date: date
    value: Decimal


def _scoped(valuator: Valuator, ticker: str) -> Valuator:
    """Restrict a per-asset valuator to its own ticker (per-asset valuators raise
    on tickers they do not know)."""

    def valuate(holdings, on):
        return valuator({ticker: holdings.get(ticker, _ZERO)}, on)

    return valuate


def _combined(valuators: list[Valuator]) -> Valuator:
    def valuate(holdings, on):
        return sum((v(holdings, on) for v in valuators), _ZERO)

    return valuate


def _as_date(value: date) -> date:
    return value.date() if isinstance(value, datetime) else value


def _first_valued_date(transactions: list[Transaction], ticker: str, start: date) -> date:
    """The earliest date the window needs a price for ``ticker``.

    The position's own start when it was bought inside the window, the window's
    start when it was already held.
    """
    dates = [_as_date(t.date) for t in transactions if t.ticker == ticker]
    return max(start, min(dates)) if dates else start


def _can_value(valuator: Valuator, ticker: str, *, since: date) -> bool:
    """Whether the ticker can really be priced from the date it is first held.

    A provider's series can begin *after* the position does — a young listing, a
    thin symbol, a provider that only keeps a few weeks of a given ticker. The
    valuator only discovers it when asked, and unasked it blows up in the middle
    of the TWR walk with a ``ValueError`` no frontend expects: the command ends in
    a traceback and the interface dies with it (the worker takes the app down).

    Asking once, here, turns that into the exclusion the policy already has for a
    ticker with no history at all. It costs nothing: the series is already in
    memory by now, and the probe is a lookup in it.
    """
    try:
        valuator({ticker: Decimal("1")}, since)
    except (BogleError, ValueError):
        return False
    return True


def build_portfolio_valuation(
    conn: psycopg.Connection[DictRow], dispatcher: PriceDispatcher, *, start: date, end: date
) -> PortfolioValuation:
    """Assemble the portfolio valuator for ``[start, end]`` from the active holdings."""
    holdings = HoldingRepository(conn).list()
    assets = AssetRepository(conn)
    transactions = TransactionRepository(conn).list()

    scoped: list[Valuator] = []
    included: set[str] = set()
    reasons: dict[str, str] = {}
    for holding in holdings:
        asset = assets.get(holding.ticker)
        if asset is None:  # a holding always has an asset row (FK); defensive
            continue
        quantity = holding.total_shares
        unit_principal = holding.total_invested / quantity if quantity != _ZERO else _ZERO
        since = _first_valued_date(transactions, holding.ticker, start)
        valuator = dispatcher.build_twr_valuator(
            asset, unit_principal=unit_principal, start=start - _HISTORY_PAD, end=end, covering=since
        )
        if valuator is None:
            reasons[holding.ticker] = NO_SOURCE if asset.asset_type is AssetType.TESOURO else NOTHING_RETURNED
            continue
        if not _can_value(valuator, holding.ticker, since=since):
            reasons[holding.ticker] = SHORT_SERIES
            continue
        scoped.append(_scoped(valuator, holding.ticker))
        included.add(holding.ticker)

    return PortfolioValuation(
        valuator=_combined(scoped) if scoped else None,
        transactions=[t for t in transactions if t.ticker in included],
        excluded=sorted(reasons),
        reasons=reasons,
        start=start,
        end=end,
    )


def portfolio_twr(valuation: PortfolioValuation) -> Decimal | None:
    """Portfolio TWR over the valuation window, or ``None`` when nothing is valuable."""
    if valuation.valuator is None or not valuation.transactions:
        return None
    return compute_twr(valuation.transactions, None, valuation.start, valuation.end, valuator=valuation.valuator)


def patrimony_at(valuation: PortfolioValuation, on: date) -> Decimal | None:
    if valuation.valuator is None:
        return None
    return valuation.valuator(shares_held(valuation.transactions, on), on)


def date_grid(start: date, end: date, granularity: str) -> list[date]:
    """Dates from ``start`` to ``end``; ``end`` is always the last point."""
    if end < start:
        raise ValueError("end deve ser >= start.")
    if granularity == "monthly":
        from bogle.reports.periods import add_months

        grid = []
        step = 0
        while (point := add_months(end, -step)) > start:
            grid.append(point)
            step += 1
        grid.append(start)
        return sorted(set(grid))
    step_days = _STEP_DAYS[granularity]
    grid = []
    point = end
    while point > start:
        grid.append(point)
        point -= timedelta(days=step_days)
    grid.append(start)
    return sorted(set(grid))


def patrimony_series(valuation: PortfolioValuation, grid: list[date]) -> list[PatrimonyPoint]:
    """Portfolio value at each grid date (0 before the first purchase)."""
    if valuation.valuator is None:
        return []
    return [PatrimonyPoint(on, valuation.valuator(shares_held(valuation.transactions, on), on)) for on in grid]


def first_transaction_date(transactions: list[Transaction]) -> date | None:
    if not transactions:
        return None
    return min(t.date.date() if isinstance(t.date, datetime) else t.date for t in transactions)
