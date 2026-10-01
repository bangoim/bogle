"""Portfolio-level historical valuation (issue #67).

Combines the per-asset valuators from :meth:`PriceDispatcher.build_twr_valuator`
into a single portfolio :data:`~bogle.analytics.twr.Valuator`, so the TWR engine
and the patrimony series work over the whole portfolio at once.

Tickers without a historical source (TESOURO — see #17 — a variable-income ticker
whose history fetch failed, or one whose series starts after the position does)
are **excluded**, together with their transactions, and reported in ``excluded``
so every consumer can warn the user instead of silently distorting values.

A ticker whose series merely *starts late* is a special case: it cannot take part
in a walk over the window (the TWR engine needs a price at every step), but it is
perfectly priceable at a single date near the end of it. Dropping it from a
point-in-time patrimony hides money the provider does price, so the valuation
also carries a :class:`SpotUniverse` — the same portfolio as it can be valued
*at* ``end`` — for the consumers that report one date instead of a series.

The other end of the same series is a quieter problem. Every valuator here prices
a date from the latest bar on or before it, so a series that has not reached
``end`` yet answers with an older close and says nothing about it — the provider
publishes each session's bar on its own schedule, and one ticker having it while
another does not is routine. The number is then labelled with a date part of the
portfolio was never priced at, which is exactly how it comes to disagree with a
live-quote screen. ``series_end`` records how fresh each series really is (it
comes free from the same fetch) and :func:`stale_at_end` names the ones that fell
short, so the consumer can disclose it instead of implying a uniform close.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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

SHORT_SERIES = "a serie do provedor veio curta demais para o periodo da posicao"
"""Yahoo sometimes answers with just the last weeks; asking again often fixes it.

Only used when the provider's *whole* series could not be read — with it in hand
the shortfall is a fact about the series, and :func:`series_starts_at` says so."""

RETRIABLE = frozenset({NOTHING_RETURNED, SHORT_SERIES})
"""The reasons worth trying again — the provider's, not the portfolio's."""


def series_starts_at(series_start: date, position_start: date) -> str:
    """Why a late-starting series is a dead end, with both dates that prove it.

    Deliberately *not* in :data:`RETRIABLE`: the provider was already asked for
    everything it has (see ``PriceDispatcher.build_historical_pricing``), so the
    old invitation to press ``r`` would send the user in circles — which is
    exactly what it did before this reason existed.
    """
    return (
        f"o provedor so tem historico desde {series_start.isoformat()}"
        f", e a posicao comeca em {position_start.isoformat()}"
    )


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
class SpotUniverse:
    """The portfolio as it can be valued *at* one date — ``end`` of the window.

    A superset of the window universe: pricing a single date only needs a bar on
    or before it, so a series that starts late still counts here. Consumers that
    report a point in time (the Home summary's patrimony and variation) use this
    one; anything that walks the window (TWR, a patrimony series) cannot.
    """

    valuator: Valuator | None
    transactions: list[Transaction]
    excluded: list[str]
    reasons: dict[str, str]


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
    ``excluded``. "No price history" covers four different situations, and only
    two of them are worth the user trying again."""
    start: date
    end: date
    spot: SpotUniverse
    """The same portfolio valued at ``end`` alone, where a late series still works."""
    series_end: dict[str, date] = field(default_factory=dict)
    """Freshest bar of each ticker that can be valued at ``end``, keyed by ticker.

    Only quoted series appear: private fixed income is computed for any date and
    a ticker nothing can price is already in ``excluded``. See
    :func:`stale_at_end` for the question this exists to answer."""
    quote_time: datetime | None = None
    """Latest D-0 quote behind the series, when built ``live`` and brapi had one
    from today for at least one ticker."""
    quote_failed: list[str] = field(default_factory=list)
    """Tickers whose D-0 quote brapi could not give (``live`` only); they are
    valued at their last stored close instead."""


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
    conn: psycopg.Connection[DictRow], dispatcher: PriceDispatcher, *, start: date, end: date, live: bool = False
) -> PortfolioValuation:
    """Assemble the portfolio valuator for ``[start, end]`` from the active holdings.

    ``live`` prices today with brapi's D-0 quote (see
    ``PriceDispatcher.build_historical_pricing``); every other report stays on
    the stored closes.
    """
    holdings = HoldingRepository(conn).list()
    assets = AssetRepository(conn)
    transactions = TransactionRepository(conn).list()

    scoped: list[Valuator] = []
    included: set[str] = set()
    reasons: dict[str, str] = {}
    spot_scoped: list[Valuator] = []
    spot_included: set[str] = set()
    spot_reasons: dict[str, str] = {}
    series_end: dict[str, date] = {}
    quote_times: list[datetime] = []
    quote_failed: list[str] = []
    for holding in holdings:
        asset = assets.get(holding.ticker)
        if asset is None:  # a holding always has an asset row (FK); defensive
            continue
        quantity = holding.total_shares
        unit_principal = holding.total_invested / quantity if quantity != _ZERO else _ZERO
        since = _first_valued_date(transactions, holding.ticker, start)
        pricing = dispatcher.build_historical_pricing(
            asset, unit_principal=unit_principal, start=start - _HISTORY_PAD, end=end, covering=since, live=live
        )
        if pricing.quote_time is not None:
            quote_times.append(pricing.quote_time)
        if pricing.quote_failed:
            quote_failed.append(holding.ticker)
        valuator = pricing.valuator
        if valuator is None:
            reason = NO_SOURCE if asset.asset_type is AssetType.TESOURO else NOTHING_RETURNED
            reasons[holding.ticker] = spot_reasons[holding.ticker] = reason
            continue
        if not _can_value(valuator, holding.ticker, since=since):
            reasons[holding.ticker] = (
                series_starts_at(pricing.series_start, since) if pricing.series_start is not None else SHORT_SERIES
            )
            # Fora da janela, mas o fechamento de `end` pode existir: a posicao
            # vale algo hoje mesmo sem historico de janeiro.
            if _can_value(valuator, holding.ticker, since=end):
                spot_scoped.append(_scoped(valuator, holding.ticker))
                spot_included.add(holding.ticker)
                _record_series_end(series_end, holding.ticker, pricing.series_end)
            else:
                spot_reasons[holding.ticker] = reasons[holding.ticker]
            continue
        scoped.append(_scoped(valuator, holding.ticker))
        included.add(holding.ticker)
        # Quem cobre a janela cobre o fim dela: o preco de `end` e o ultimo em ou
        # antes dele, e a serie ja comeca antes.
        spot_scoped.append(_scoped(valuator, holding.ticker))
        spot_included.add(holding.ticker)
        _record_series_end(series_end, holding.ticker, pricing.series_end)

    return PortfolioValuation(
        valuator=_combined(scoped) if scoped else None,
        transactions=[t for t in transactions if t.ticker in included],
        excluded=sorted(reasons),
        reasons=reasons,
        start=start,
        end=end,
        spot=SpotUniverse(
            valuator=_combined(spot_scoped) if spot_scoped else None,
            transactions=[t for t in transactions if t.ticker in spot_included],
            excluded=sorted(spot_reasons),
            reasons=spot_reasons,
        ),
        series_end=series_end,
        quote_time=max(quote_times, default=None),
        quote_failed=sorted(quote_failed),
    )


def _record_series_end(series_end: dict[str, date], ticker: str, when: date | None) -> None:
    """Note how fresh a ticker's series is, when the source has a series at all."""
    if when is not None:
        series_end[ticker] = when


def stale_at_end(valuation: PortfolioValuation) -> dict[str, date]:
    """Tickers priced at an *older* close than ``end``, and the close really used.

    Empty in the ordinary case, where every series reaches the reference date.
    What lands here is a series the provider had not extended to ``end`` yet: the
    valuator answered with the previous bar (its documented rule), so the ticker
    contributes a stale price to a number labelled with ``end``. Naming it and the
    date it came from is the whole point — "the patrimony is a day old for these
    two" is checkable against a live quote, while a silent difference is not.
    """
    return {ticker: when for ticker, when in valuation.series_end.items() if when < valuation.end}


def portfolio_twr(valuation: PortfolioValuation) -> Decimal | None:
    """Portfolio TWR over the valuation window, or ``None`` when nothing is valuable."""
    if valuation.valuator is None or not valuation.transactions:
        return None
    return compute_twr(valuation.transactions, None, valuation.start, valuation.end, valuator=valuation.valuator)


def patrimony_at(valuation: PortfolioValuation, on: date) -> Decimal | None:
    if valuation.valuator is None:
        return None
    return valuation.valuator(shares_held(valuation.transactions, on), on)


def spot_patrimony(valuation: PortfolioValuation) -> Decimal | None:
    """Patrimony at ``valuation.end`` over the spot universe.

    Takes no date on purpose: the spot universe is only priceable at ``end`` (a
    ticker in it may have no bar at all earlier in the window), so letting a
    caller pass another date would be an invitation to a ``ValueError``.
    """
    spot = valuation.spot
    if spot.valuator is None:
        return None
    return spot.valuator(shares_held(spot.transactions, valuation.end), valuation.end)


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
