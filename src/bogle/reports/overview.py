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

Everything is measured *at* ``as_of``, invested capital included: reading it off
the ``holdings`` view instead would mix in transactions dated after the
reference date, and a buy registered today would show up as a loss the size of
the aporte (the money is in the base, the shares are not in the patrimony yet).

Tickers without a historical price source (TESOURO — see #17 — or a ticker whose
history fetch failed) are excluded from *every* number, so patrimony and
variation stay comparable; ``excluded`` carries them for the caller to report,
the same policy as the other historical reports (#67). A caller showing
``patrimony`` with a non-empty ``excluded`` is showing a *partial* patrimony and
should say so.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

import psycopg
from psycopg.rows import DictRow

from bogle.analytics.twr import compute_twr
from bogle.data.dispatcher import PriceDispatcher
from bogle.domain.transactions import Transaction, TransactionType
from bogle.reports.periods import period_start
from bogle.reports.valuation import build_portfolio_valuation, first_transaction_date, patrimony_at
from bogle.repositories.transactions import TransactionRepository

_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class PortfolioOverview:
    as_of: date
    """Reference date of every number below (D-1 for the TUI's Home)."""
    inception: date | None
    """First transaction ever; ``None`` when the ledger is empty."""
    invested: Decimal
    """Capital in the positions that could be valued, as of ``as_of``."""
    patrimony: Decimal | None
    """``None`` when nothing could be valued at ``as_of``."""
    twr_12m: Decimal | None
    twr_total: Decimal | None
    twr_12m_start: date | None
    """Where the 12m window actually starts — the inception when the portfolio
    is younger than 12 months, in which case the window is shorter than its name."""
    excluded: list[str]
    excluded_reasons: dict[str, str] = field(default_factory=dict)
    """Why each excluded ticker is out (see :mod:`bogle.reports.valuation`). The
    Home screen shows it: "no price history" reads like a permanent fact about the
    asset, and one of the three reasons is a provider hiccup worth retrying."""

    @property
    def is_empty(self) -> bool:
        return self.inception is None

    @property
    def is_partial(self) -> bool:
        """``True`` when a ticker was left out, making ``patrimony`` a subset."""
        return bool(self.excluded)

    @property
    def twr_12m_is_shorter(self) -> bool:
        """``True`` when the "12m" window had to anchor on the first transaction."""
        return self.twr_12m_start is not None and self.twr_12m_start == self.inception

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


def _as_date(value: date) -> date:
    return value.date() if isinstance(value, datetime) else value


def invested_at(transactions: list[Transaction], on: date) -> Decimal:
    """Capital in the positions still held at ``on``.

    Mirrors the ``holdings`` view — BUY cost (fees included) minus gross SELL
    proceeds, counting only tickers with shares left — but as of a past date, so
    it is comparable with a patrimony valued on that same date.
    """
    shares: dict[str, Decimal] = defaultdict(lambda: _ZERO)
    invested: dict[str, Decimal] = defaultdict(lambda: _ZERO)
    for txn in transactions:
        if _as_date(txn.date) > on:
            continue
        if txn.transaction_type is TransactionType.BUY:
            shares[txn.ticker] += txn.shares
            invested[txn.ticker] += txn.total_cost
        elif txn.transaction_type is TransactionType.SELL:
            shares[txn.ticker] -= txn.shares
            invested[txn.ticker] -= txn.total_investment
    return sum((value for ticker, value in invested.items() if shares[ticker] > _ZERO), _ZERO)


def compute_overview(
    conn: psycopg.Connection[DictRow],
    dispatcher: PriceDispatcher,
    *,
    as_of: date,
) -> PortfolioOverview:
    """Value the portfolio at ``as_of`` and measure its return up to that date."""
    transactions = TransactionRepository(conn).list()
    inception = first_transaction_date(transactions)

    if inception is None or as_of < inception:
        # Empty ledger, or the first transaction is younger than the reference
        # date: there is no earlier close to value.
        return PortfolioOverview(
            as_of=as_of,
            inception=inception,
            invested=_ZERO,
            patrimony=None,
            twr_12m=None,
            twr_total=None,
            twr_12m_start=None,
            excluded=[],
        )

    valuation = build_portfolio_valuation(conn, dispatcher, start=inception, end=as_of)

    twr_total: Decimal | None = None
    twr_12m: Decimal | None = None
    start_12m: date | None = None
    if valuation.valuator is not None and valuation.transactions:
        twr_total = compute_twr(valuation.transactions, None, inception, as_of, valuator=valuation.valuator)
        start_12m = max(inception, period_start("12m", today=as_of) or inception)
        twr_12m = compute_twr(valuation.transactions, None, start_12m, as_of, valuator=valuation.valuator)

    return PortfolioOverview(
        as_of=as_of,
        inception=inception,
        # valuation.transactions ja vem restrito aos tickers avaliaveis.
        invested=invested_at(valuation.transactions, as_of),
        patrimony=patrimony_at(valuation, as_of),
        twr_12m=twr_12m,
        twr_total=twr_total,
        twr_12m_start=start_12m,
        excluded=valuation.excluded,
        excluded_reasons=valuation.reasons,
    )
