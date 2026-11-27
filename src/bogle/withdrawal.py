"""What the withdrawal suggestion reads from the database (issue #80).

:func:`~bogle.rebalancing.suggest_withdrawal` is pure; this gathers what it needs
from one connection, so ``bogle withdraw`` and the Saque screen ask the same
question the same way:

- the open positions, priced (the ``holdings`` view — only what can be sold);
- the tickers the glide path is taking out (:func:`~bogle.glide_path.exiting_tickers`);
- the phase, from ``birth_year`` and ``retirement_age``;
- the stock sales already registered this month, for the R$20k exemption;
- the date of the oldest lot still held of each fixed-income ticker, for the
  regressive IR table: the redemption takes the oldest units first.

Reads only. Unlike ``bogle suggest``, it records nothing — not even
``last_rebalance_date``: a withdrawal is not the cycle's evaluation, and the sale
is only real once it is registered with ``bogle sell``.
"""

from __future__ import annotations

from collections import deque
from datetime import date, datetime
from decimal import Decimal

import psycopg
from psycopg.rows import DictRow

from bogle.data.dispatcher import PriceDispatcher
from bogle.domain.assets import FIXED_INCOME_TYPES, AssetType
from bogle.domain.transactions import Transaction, TransactionType
from bogle.glide_path import active_glide_path, exiting_tickers
from bogle.position import get_portfolio_summary
from bogle.rebalancing import WithdrawalSuggestion, retirement_phase, suggest_withdrawal
from bogle.repositories.assets import AssetRepository
from bogle.repositories.transactions import TransactionRepository
from bogle.settings import BIRTH_YEAR, RETIREMENT_AGE, get_setting

_ZERO = Decimal("0")


def plan_withdrawal(
    conn: psycopg.Connection[DictRow], dispatcher: PriceDispatcher | None, amount: Decimal, *, today: date
) -> WithdrawalSuggestion:
    """Which tickers to sell for ``amount`` (gross) on ``today``; nothing is written."""
    summary = get_portfolio_summary(conn, dispatcher, on_date=today)
    exiting = exiting_tickers(active_glide_path(conn), (p.ticker for p in summary.positions))
    retired = retirement_phase(get_setting(conn, BIRTH_YEAR), get_setting(conn, RETIREMENT_AGE), today.year)
    transactions = TransactionRepository(conn).list()
    types = {asset.ticker: asset.asset_type for asset in AssetRepository(conn).list()}
    fixed_income = {p.ticker for p in summary.positions if p.asset_type in FIXED_INCOME_TYPES}
    held_since = {
        ticker: since
        for ticker in fixed_income
        if (since := oldest_lot([t for t in transactions if t.ticker == ticker])) is not None
    }
    return suggest_withdrawal(
        summary,
        amount,
        exiting,
        retired=retired,
        month_stock_sales=month_stock_sales(transactions, types, today),
        held_since=held_since,
        today=today,
    )


def _day(value: datetime | date) -> date:
    # A sessao do banco esta no fuso de Sao Paulo: a data local e a do timestamp.
    return value.date() if isinstance(value, datetime) else value


def month_stock_sales(transactions: list[Transaction], types: dict[str, AssetType], today: date) -> Decimal:
    """The gross of the stock sales registered in the month of ``today``.

    Only ``STOCK``: the R$20k exemption is the stock market's alone, and it is
    measured over every stock sold in the month, not per ticker.
    """
    return sum(
        (
            t.total_investment
            for t in transactions
            if t.transaction_type is TransactionType.SELL
            and types.get(t.ticker) is AssetType.STOCK
            and (_day(t.date).year, _day(t.date).month) == (today.year, today.month)
        ),
        _ZERO,
    )


def oldest_lot(transactions: list[Transaction]) -> date | None:
    """The purchase date of the oldest unit still held of one ticker, first in first out.

    Fixed income redeems the oldest units first, and the regressive table counts
    the days from their purchase. ``None`` when nothing is held.
    """
    trades = sorted(
        (t for t in transactions if t.transaction_type in (TransactionType.BUY, TransactionType.SELL)),
        # Como no custo medio: dia a dia, e no mesmo dia as compras antes das vendas.
        key=lambda t: (_day(t.date), t.transaction_type is not TransactionType.BUY, t.id),
    )
    lots: deque[list] = deque()
    for txn in trades:
        if txn.transaction_type is TransactionType.BUY:
            lots.append([_day(txn.date), txn.shares])
            continue
        left = txn.shares
        while left > 0 and lots:
            taken = min(left, lots[0][1])
            lots[0][1] -= taken
            left -= taken
            if lots[0][1] <= 0:
                lots.popleft()
    return lots[0][0] if lots else None
