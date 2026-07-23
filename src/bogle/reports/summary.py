"""Aggregated portfolio summary (issue #28).

Headline numbers come straight from the live position (#19):
``variation = total_value - total_invested`` is the capital gain over the
capital still at risk (realized + unrealized — the view nets sale proceeds out
of ``total_invested``; income is NOT in it, see #29 for the decomposition).

"Lucro do mes" is the R$ P&L of the window: the patrimony delta minus what was
contributed, plus what was withdrawn and received as income — otherwise a fresh
aporte would read as profit. It is computed over the tickers with a historical
source (the foundation excludes TESOURO — see #67), so the excluded list must
be surfaced next to the number.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from bogle.domain.transactions import Transaction, TransactionType
from bogle.reports.dividends import INCOME_TYPES, received_amount

_ZERO = Decimal("0")


def _as_date(value: date) -> date:
    return value.date() if isinstance(value, datetime) else value


def income_received(transactions: list[Transaction], *, start: date | None, end: date) -> Decimal:
    """Income (JCP net) received in ``[start, end]``."""
    return sum(
        (
            received_amount(t)
            for t in transactions
            if t.transaction_type in INCOME_TYPES
            and _as_date(t.date) <= end
            and (start is None or _as_date(t.date) >= start)
        ),
        _ZERO,
    )


def window_profit(
    transactions: list[Transaction],
    value_start: Decimal,
    value_end: Decimal,
    *,
    start: date,
    end: date,
) -> Decimal:
    """R$ P&L over ``(start, end]``: patrimony delta net of external flows, plus
    income received. ``value_start`` is the end-of-day patrimony at ``start``,
    so flows dated exactly ``start`` are already inside it and stay out."""
    invested = _ZERO
    proceeds = _ZERO
    income = _ZERO
    for t in transactions:
        on = _as_date(t.date)
        if not (start < on <= end):
            continue
        if t.transaction_type is TransactionType.BUY:
            invested += t.total_cost
        elif t.transaction_type is TransactionType.SELL:
            proceeds += t.total_investment
        elif t.transaction_type in INCOME_TYPES:
            income += received_amount(t)
    return value_end - value_start - invested + proceeds + income
