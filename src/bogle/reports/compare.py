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
from bogle.domain.errors import MarketDataError, ValidationError
from bogle.reports.periods import period_start
from bogle.reports.valuation import (
    GRANULARITY_BY_PERIOD,
    build_portfolio_valuation,
    date_grid,
    first_transaction_date,
)
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
    for index in indices:
        try:
            series.append(CompareSeries(index, _base_100(dispatcher.get_index_series(index, grid))))
        except MarketDataError as exc:
            index_errors[index] = str(exc)
    return CompareReport(grid=grid, series=series, excluded=valuation.excluded, index_errors=index_errors)
