"""The portfolio snapshot behind ``bogle position`` (issues #21/#28, #73).

Bundles what a "current position" view needs beyond the per-ticker table: the
live position itself, the month's P&L and the income received over the last 12
calendar months. It lives here — instead of inside the command — so the CLI and
the TUI's Position screen report the *same numbers* from the same code.

Month profit needs historical prices to value the window's opening patrimony, so
it is only available when a dispatcher is given (``None`` = the ``--no-prices``
view); income comes straight from the ledger and is always available.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import psycopg
from psycopg.rows import DictRow

from bogle.data.dispatcher import PriceDispatcher
from bogle.position import PortfolioSummary, get_portfolio_summary
from bogle.reports.dividends import twelve_month_start
from bogle.reports.periods import add_months
from bogle.reports.summary import income_received, window_profit
from bogle.reports.valuation import build_portfolio_valuation, patrimony_at
from bogle.repositories.transactions import TransactionRepository


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    summary: PortfolioSummary
    month_profit: Decimal | None
    """``None`` without a dispatcher (no historical prices to value the window)."""
    income_12m: Decimal
    excluded: list[str]
    """Tickers left out of the month profit for lacking price history."""


def compute_snapshot(
    conn: psycopg.Connection[DictRow],
    dispatcher: PriceDispatcher | None,
    *,
    today: date,
) -> PortfolioSnapshot:
    """Price the portfolio and fold in the month profit and the 12m income."""
    month_start = add_months(today, -1)
    summary = get_portfolio_summary(conn, dispatcher)
    transactions = TransactionRepository(conn).list()
    valuation = (
        build_portfolio_valuation(conn, dispatcher, start=month_start, end=today) if dispatcher is not None else None
    )

    income_12m = income_received(transactions, start=twelve_month_start(today), end=today)
    month_profit: Decimal | None = None
    excluded: list[str] = []
    if valuation is not None:
        excluded = valuation.excluded
        value_start = patrimony_at(valuation, month_start)
        value_end = patrimony_at(valuation, today)
        if value_start is not None and value_end is not None:
            month_profit = window_profit(valuation.transactions, value_start, value_end, start=month_start, end=today)

    return PortfolioSnapshot(
        summary=summary,
        month_profit=month_profit,
        income_12m=income_12m,
        excluded=excluded,
    )
