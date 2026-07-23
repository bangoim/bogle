"""Income received report (issue #30).

Aggregates the income transactions (DIVIDEND/JCP/RENDIMENTO/INTEREST) by
calendar month or by ticker. JCP is reported **net** of the tax withheld at
source; the other types are gross (FII rendimento and dividends are exempt for
individuals, and only the JCP net value is what actually lands in the account).

"Last 12 months" means calendar months: the current month plus the eleven
before it, so a month bucket never splits.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from bogle.domain.transactions import Transaction, TransactionType
from bogle.reports.periods import add_months

_ZERO = Decimal("0")

INCOME_TYPES = (
    TransactionType.DIVIDEND,
    TransactionType.JCP,
    TransactionType.RENDIMENTO,
    TransactionType.INTEREST,
)


@dataclass(frozen=True, slots=True)
class MonthlyIncome:
    """Income received in one calendar month (``month`` = first day)."""

    month: date
    dividend: Decimal
    jcp: Decimal
    rendimento: Decimal
    interest: Decimal

    @property
    def total(self) -> Decimal:
        return self.dividend + self.jcp + self.rendimento + self.interest


@dataclass(frozen=True, slots=True)
class TickerIncome:
    ticker: str
    income_type: TransactionType
    total: Decimal


def _as_date(value: date) -> date:
    return value.date() if isinstance(value, datetime) else value


def received_amount(txn: Transaction) -> Decimal:
    """What actually reached the account: JCP net of IR at source, rest gross."""
    if txn.transaction_type is TransactionType.JCP:
        return txn.total_investment - txn.tax_withheld
    return txn.total_investment


def _income_in_window(transactions: list[Transaction], start: date | None, end: date) -> list[Transaction]:
    return [
        t
        for t in transactions
        if t.transaction_type in INCOME_TYPES
        and _as_date(t.date) <= end
        and (start is None or _as_date(t.date) >= start)
    ]


def income_by_month(transactions: list[Transaction], *, start: date | None, end: date) -> list[MonthlyIncome]:
    """One row per calendar month from ``start`` (or the first income) to ``end``,
    empty months included so the series reads as continuous."""
    income = _income_in_window(transactions, start, end)
    if not income:
        return []

    buckets: dict[date, dict[TransactionType, Decimal]] = defaultdict(lambda: defaultdict(lambda: _ZERO))
    for txn in income:
        txn_date = _as_date(txn.date)
        buckets[date(txn_date.year, txn_date.month, 1)][txn.transaction_type] += received_amount(txn)

    first = date(start.year, start.month, 1) if start is not None else min(buckets)
    last = date(end.year, end.month, 1)
    rows = []
    month = first
    while month <= last:
        bucket = buckets.get(month, {})
        rows.append(
            MonthlyIncome(
                month=month,
                dividend=bucket.get(TransactionType.DIVIDEND, _ZERO),
                jcp=bucket.get(TransactionType.JCP, _ZERO),
                rendimento=bucket.get(TransactionType.RENDIMENTO, _ZERO),
                interest=bucket.get(TransactionType.INTEREST, _ZERO),
            )
        )
        month = add_months(month, 1)
    return rows


def income_by_ticker(transactions: list[Transaction], *, start: date | None, end: date) -> list[TickerIncome]:
    """One row per (ticker, income type), largest totals first."""
    totals: dict[tuple[str, TransactionType], Decimal] = defaultdict(lambda: _ZERO)
    for txn in _income_in_window(transactions, start, end):
        totals[(txn.ticker, txn.transaction_type)] += received_amount(txn)
    return [
        TickerIncome(ticker=ticker, income_type=income_type, total=total)
        for (ticker, income_type), total in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def twelve_month_start(today: date) -> date:
    """First day of the calendar window "last 12 months" (current month + 11 back)."""
    return add_months(date(today.year, today.month, 1), -11)
