"""Patrimony evolution over time (issue #25).

Thin orchestration over the foundation (#67): resolve the window, build the
portfolio valuation and sample it on the period's grid. Fixed income is valued
by present value through the per-asset valuators; TESOURO (no free history,
see #17) is excluded and reported.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import psycopg
from psycopg.rows import DictRow

from bogle.data.dispatcher import PriceDispatcher
from bogle.domain.errors import ValidationError
from bogle.reports.periods import period_start
from bogle.reports.valuation import (
    GRANULARITY_BY_PERIOD,
    PatrimonyPoint,
    build_portfolio_valuation,
    date_grid,
    first_transaction_date,
    patrimony_series,
)
from bogle.repositories.transactions import TransactionRepository


@dataclass(frozen=True, slots=True)
class HistoryReport:
    points: list[PatrimonyPoint]
    granularity: str
    excluded: list[str]

    def steps(self) -> Iterator[tuple[PatrimonyPoint, Decimal | None, Decimal | None]]:
        """Each point with how much it moved from the previous one: ``(point,
        delta, fraction)``.

        Both frontends show this column pair, and each one deriving it on its own
        is how they drift apart — they already had. The first point has no
        previous one (``None``, rendered as a dash), and neither does a fraction
        over a non-positive base.
        """
        previous: Decimal | None = None
        for point in self.points:
            delta = point.value - previous if previous is not None else None
            fraction = delta / previous if delta is not None and previous is not None and previous > 0 else None
            yield point, delta, fraction
            previous = point.value


def compute_history(
    conn: psycopg.Connection[DictRow], dispatcher: PriceDispatcher, *, period: str, today: date
) -> HistoryReport:
    transactions = TransactionRepository(conn).list()
    inception = first_transaction_date(transactions)
    if inception is None:
        raise ValidationError("Nenhuma transacao registrada para montar o historico.")

    start = max(period_start(period, today=today) or inception, inception)
    valuation = build_portfolio_valuation(conn, dispatcher, start=start, end=today)
    if valuation.valuator is None:
        raise ValidationError(
            "Nenhuma posicao com historico de precos para montar o historico"
            + (f" (sem historico: {', '.join(valuation.excluded)})." if valuation.excluded else ".")
        )

    granularity = GRANULARITY_BY_PERIOD[period]
    points = patrimony_series(valuation, date_grid(start, today, granularity))
    return HistoryReport(points=points, granularity=granularity, excluded=valuation.excluded)
