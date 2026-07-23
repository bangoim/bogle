"""Consolidated portfolio profitability (issue #27).

TWR (issue #20) over up to three windows — total / 12m / 1m — optionally set
against accumulated index returns (#67). One portfolio valuation covering the
widest window is reused by every sub-window, so history is fetched once.
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
from bogle.reports.valuation import build_portfolio_valuation, first_transaction_date
from bogle.repositories.transactions import TransactionRepository

DEFAULT_PERIODS = ("total", "12m", "1m")


@dataclass(frozen=True, slots=True)
class PeriodReturn:
    period: str
    start: date
    end: date
    twr: Decimal | None
    """``None`` when no position has a historical source."""
    index_returns: dict[str, Decimal | None]
    """Accumulated return per index; ``None`` when the source has no data."""


@dataclass(frozen=True, slots=True)
class ReturnsReport:
    rows: list[PeriodReturn]
    excluded: list[str]
    index_errors: dict[str, str]
    """Friendly message per index that could not be resolved (any window)."""


def compute_returns(
    conn: psycopg.Connection[DictRow],
    dispatcher: PriceDispatcher,
    *,
    periods: tuple[str, ...] = DEFAULT_PERIODS,
    indices: tuple[str, ...] = (),
    today: date,
) -> ReturnsReport:
    transactions = TransactionRepository(conn).list()
    inception = first_transaction_date(transactions)
    if inception is None:
        raise ValidationError("Nenhuma transacao registrada para calcular rentabilidade.")

    starts = {period: period_start(period, today=today) or inception for period in periods}
    # Janelas que comecam antes da primeira transacao nao fazem sentido: ancora nela.
    starts = {period: max(start, inception) for period, start in starts.items()}

    valuation = build_portfolio_valuation(conn, dispatcher, start=min(starts.values()), end=today)

    index_errors: dict[str, str] = {}
    rows = []
    for period in periods:
        start = starts[period]
        twr = None
        if valuation.valuator is not None and valuation.transactions:
            twr = compute_twr(valuation.transactions, None, start, today, valuator=valuation.valuator)
        index_returns: dict[str, Decimal | None] = {}
        for index in indices:
            try:
                index_returns[index] = dispatcher.get_index_return(index, start, today)
            except MarketDataError as exc:
                index_returns[index] = None
                index_errors.setdefault(index, str(exc))
        rows.append(PeriodReturn(period=period, start=start, end=today, twr=twr, index_returns=index_returns))
    return ReturnsReport(rows=rows, excluded=valuation.excluded, index_errors=index_errors)
