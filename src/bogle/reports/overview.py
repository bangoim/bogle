"""Headline portfolio overview at a reference date (issue #73).

The four numbers the TUI opens with, all measured at the same reference date —
the previous day's close (D-1), so opening the app never waits on an intraday
quote and the result is cacheable:

1. **patrimony** — market value of the positions on that date;
2. **variation** — patrimony minus the capital invested in them (R$ and %);
3. **twr_12m** / **twr_total** — time-weighted return over the last 12 months
   and since the first transaction.

TWR (issue #20) is the honest lens for a headline return: it removes the size
and the timing of contributions and withdrawals and credits income, so a fresh
aporte never reads as performance.

Tickers without a historical price source (TESOURO — see #17 — or a ticker whose
history fetch failed) are excluded from *every* number, invested capital
included, so patrimony and variation stay comparable; ``excluded`` carries them
for the caller to report, the same policy as the other historical reports (#67).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import psycopg
from psycopg.rows import DictRow

from bogle.analytics.twr import compute_twr
from bogle.data.dispatcher import PriceDispatcher
from bogle.reports.periods import period_start
from bogle.reports.valuation import build_portfolio_valuation, first_transaction_date, patrimony_at
from bogle.repositories.holdings import HoldingRepository
from bogle.repositories.transactions import TransactionRepository

_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class PortfolioOverview:
    as_of: date
    """Reference date of every number below (D-1 for the TUI's Home)."""
    inception: date | None
    """First transaction ever; ``None`` when the ledger is empty."""
    invested: Decimal
    """Capital invested in the positions that could be valued."""
    patrimony: Decimal | None
    """``None`` when nothing could be valued at ``as_of``."""
    twr_12m: Decimal | None
    twr_total: Decimal | None
    excluded: list[str]

    @property
    def is_empty(self) -> bool:
        return self.inception is None

    @property
    def variation(self) -> Decimal | None:
        return self.patrimony - self.invested if self.patrimony is not None else None

    @property
    def variation_percent(self) -> Decimal | None:
        # Invested capital goes negative once sales returned more cash than went
        # in (see Holding), and a percentage over that base would be nonsense.
        variation = self.variation
        if variation is None or self.invested <= _ZERO:
            return None
        return variation / self.invested


def compute_overview(
    conn: psycopg.Connection[DictRow],
    dispatcher: PriceDispatcher,
    *,
    as_of: date,
) -> PortfolioOverview:
    """Value the portfolio at ``as_of`` and measure its return up to that date."""
    transactions = TransactionRepository(conn).list()
    holdings = HoldingRepository(conn).list()
    inception = first_transaction_date(transactions)

    if inception is None or as_of < inception:
        # Empty ledger, or the first transaction is younger than the reference
        # date: there is no earlier close to value.
        return PortfolioOverview(
            as_of=as_of,
            inception=inception,
            invested=sum((h.total_invested for h in holdings), _ZERO),
            patrimony=None,
            twr_12m=None,
            twr_total=None,
            excluded=[],
        )

    valuation = build_portfolio_valuation(conn, dispatcher, start=inception, end=as_of)
    excluded = set(valuation.excluded)

    twr_total: Decimal | None = None
    twr_12m: Decimal | None = None
    if valuation.valuator is not None and valuation.transactions:
        twr_total = compute_twr(valuation.transactions, None, inception, as_of, valuator=valuation.valuator)
        start_12m = max(inception, period_start("12m", today=as_of) or inception)
        twr_12m = compute_twr(valuation.transactions, None, start_12m, as_of, valuator=valuation.valuator)

    return PortfolioOverview(
        as_of=as_of,
        inception=inception,
        invested=sum((h.total_invested for h in holdings if h.ticker not in excluded), _ZERO),
        patrimony=patrimony_at(valuation, as_of),
        twr_12m=twr_12m,
        twr_total=twr_total,
        excluded=valuation.excluded,
    )
