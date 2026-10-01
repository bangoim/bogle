"""What a ticker holds at the close of each day, and what a sale may take from it.

The rule every write to the ledger keeps: **at the close of any day, no ticker
holds fewer than zero shares**. Positions are counted at the end of the day, so
the purchases and the sales of one date are netted whatever order they were
registered in: a sale is covered when what was held before plus that day's
purchases cover it.

It is a rule about the whole history, not about today's position. A sale dated
in the past has to fit the position of its own day *and* leave every later sale
covered — selling in February the shares a March sale already sold is the same
impossibility as selling shares never bought, it just shows up a month later.

Pure functions over one ticker's transactions; :mod:`bogle.sales` reads the
ledger and applies them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from bogle.domain.transactions import Transaction, TransactionType

_ZERO = Decimal("0")


def _as_date(value: date) -> date:
    return value.date() if isinstance(value, datetime) else value


def end_of_day_positions(transactions: list[Transaction]) -> list[tuple[date, Decimal]]:
    """Shares held at the close of each day the ticker traded, in date order.

    Income moves no shares and opens no day here.
    """
    moved: dict[date, Decimal] = {}
    for txn in transactions:
        if txn.transaction_type is TransactionType.BUY:
            delta = txn.shares
        elif txn.transaction_type is TransactionType.SELL:
            delta = -txn.shares
        else:
            continue
        day = _as_date(txn.date)
        moved[day] = moved.get(day, _ZERO) + delta
    closes: list[tuple[date, Decimal]] = []
    held = _ZERO
    for day in sorted(moved):
        held += moved[day]
        closes.append((day, held))
    return closes


@dataclass(frozen=True, slots=True)
class Sellable:
    """How much a new sale dated ``on`` can take out of a ticker."""

    held: Decimal
    """Shares held at the close of ``on``, that day's trades included."""
    free: Decimal
    """The most the sale can take without leaving any later day below zero."""
    covers: date | None
    """The later day that caps ``free`` below ``held``: a sale on it needs the rest."""


def sellable_on(transactions: list[Transaction], on: date) -> Sellable:
    """What a sale dated ``on`` may sell, given the ticker's history without it.

    A sale lowers every close from its day on by the same amount, so the room it
    has is the smallest close from ``on`` onward. When a later day is the one
    that caps it, that day is reported: "only 2 are free, the other 8 cover the
    March sale" is checkable, a bare "only 2" is not.
    """
    held = _ZERO
    free = _ZERO
    covers: date | None = None
    for day, close in end_of_day_positions(transactions):
        if day <= on:
            held = free = close
        elif close < free:
            free, covers = close, day
    return Sellable(held=held, free=max(free, _ZERO), covers=covers)


def first_uncovered(transactions: list[Transaction]) -> tuple[date, Decimal] | None:
    """The first day the ticker closes below zero, and how many shares are missing."""
    for day, close in end_of_day_positions(transactions):
        if close < _ZERO:
            return day, -close
    return None
