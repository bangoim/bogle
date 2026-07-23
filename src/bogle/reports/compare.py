"""Portfolio vs market indices, base-100 series (issue #26).

The portfolio series is the cumulative TWR level (growth of 100 invested at the
window start, external flows removed) — comparing raw patrimony against an
index would read contributions as performance. Index levels come from
:meth:`PriceDispatcher.get_index_series` (rate indices are compounded factors,
market indices are closes) and every series is normalized to 100 at the start.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import psycopg
from psycopg.rows import DictRow

from bogle.analytics.twr import compute_twr
from bogle.data.dispatcher import PriceDispatcher
from bogle.domain.assets import VARIABLE_INCOME_TYPES
from bogle.domain.errors import MarketDataError, ValidationError
from bogle.reports.periods import period_start
from bogle.reports.valuation import (
    GRANULARITY_BY_PERIOD,
    build_portfolio_valuation,
    date_grid,
    first_transaction_date,
)
from bogle.repositories.assets import AssetRepository
from bogle.repositories.holdings import HoldingRepository
from bogle.repositories.transactions import TransactionRepository

_HUNDRED = Decimal("100")

PORTFOLIO_SERIES = "Carteira"


@dataclass(frozen=True, slots=True)
class CompareSeries:
    name: str
    levels: list[Decimal]
    """Base-100 levels, one per grid date."""

    @property
    def accumulated_return(self) -> Decimal:
        return self.levels[-1] / self.levels[0] - 1


@dataclass(frozen=True, slots=True)
class CompareReport:
    grid: list[date]
    series: list[CompareSeries]
    """Portfolio first, then each resolvable index."""
    excluded: list[str]
    index_errors: dict[str, str]
    data_as_of: date | None = None
    """Freshest real data date across all series. The last grid point is
    forward-filled from this when the market has no bar for ``grid[-1]`` yet,
    so it (not ``grid[-1]``) is what the numbers actually reflect."""


def _base_100(levels: list[Decimal]) -> list[Decimal]:
    first = levels[0]
    if first == 0:
        raise MarketDataError("Serie com valor inicial zero; impossivel normalizar.", provider="")
    return [level / first * _HUNDRED for level in levels]


def compute_compare(
    conn: psycopg.Connection[DictRow],
    dispatcher: PriceDispatcher,
    *,
    period: str,
    indices: tuple[str, ...],
    today: date,
) -> CompareReport:
    transactions = TransactionRepository(conn).list()
    inception = first_transaction_date(transactions)
    if inception is None:
        raise ValidationError("Nenhuma transacao registrada para comparar rentabilidade.")

    start = max(period_start(period, today=today) or inception, inception)
    valuation = build_portfolio_valuation(conn, dispatcher, start=start, end=today)
    if valuation.valuator is None or not valuation.transactions:
        raise ValidationError(
            "Nenhuma posicao com historico de precos para comparar"
            + (f" (sem historico: {', '.join(valuation.excluded)})." if valuation.excluded else ".")
        )

    grid = date_grid(start, today, GRANULARITY_BY_PERIOD[period])
    portfolio_levels = [
        _HUNDRED * (Decimal("1") + compute_twr(valuation.transactions, None, start, on, valuator=valuation.valuator))
        for on in grid
    ]
    series = [CompareSeries(PORTFOLIO_SERIES, portfolio_levels)]

    index_errors: dict[str, str] = {}
    resolved: list[str] = []
    for index in indices:
        try:
            series.append(CompareSeries(index, _base_100(dispatcher.get_index_series(index, grid))))
            resolved.append(index)
        except MarketDataError as exc:
            index_errors[index] = str(exc)

    data_as_of = _latest_data_date(conn, dispatcher, resolved, start=start, end=today)
    return CompareReport(
        grid=grid,
        series=series,
        excluded=valuation.excluded,
        index_errors=index_errors,
        data_as_of=data_as_of,
    )


def _latest_data_date(
    conn: psycopg.Connection[DictRow],
    dispatcher: PriceDispatcher,
    indices: list[str],
    *,
    start: date,
    end: date,
) -> date | None:
    """Freshest real data date across the portfolio (variable income) and the
    resolved indices — what the last, possibly forward-filled, grid point
    actually reflects. Relies on the dispatcher's on-disk cache, so it does not
    re-hit the network for series already fetched above."""
    dates: list[date] = []
    assets = AssetRepository(conn)
    for holding in HoldingRepository(conn).list():
        asset = assets.get(holding.ticker)
        if asset is not None and asset.asset_type in VARIABLE_INCOME_TYPES:
            when = dispatcher.latest_history_date(holding.ticker, start, end)
            if when is not None:
                dates.append(when)
    for index in indices:
        when = dispatcher.latest_index_date(index, start, end)
        if when is not None:
            dates.append(when)
    return max(dates) if dates else None
