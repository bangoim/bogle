"""How many shares a sale is allowed to sell, and which transactions may leave.

The ledger accepts any of them. ``TransactionRepository.add_sale`` validates the
numbers themselves and nothing else, and the ``holdings`` view answers an
oversold ticker by hiding the position — its sum went negative, so there is
nothing to show. That was a deliberate scope call while the ledger was being
built (issue #9), and it stayed harmless only while nothing read the result: the
position table, the average price, the realized gain and the tax report all read
it now, and each of them reads a hole.

So the refusal lives here, one layer above the repository, next to the other
thing a sale decides on its own (:mod:`bogle.closeout`). A sale is an intention,
and "vendi 150" against a position of 120 is a typo with a known correct answer,
not a row to write.

The position it is checked against is the one *on the sale's date*, never
today's (see :mod:`bogle.domain.ledger`). Today's position let a sale dated
before its purchase through, and one that took in February the shares a March
sale had already sold; both made the cost-basis replay refuse the ticker later,
far from the moment the typo could still be fixed. For the same reason a
purchase cannot be removed while a sale depends on it.

Both frontends call this inside the same transaction as the write, and both
spell the whole position the same way — ``--all`` on the command, "Vender tudo"
on the form — as ``shares=None``. That is the one quantity the app can fill in
by itself, and resolving it here (instead of on the screen that offered it) is
what keeps the number written equal to the number the ledger has.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import DictRow

from bogle.db import DEFAULT_TIMEZONE
from bogle.domain.errors import InsufficientSharesError, UncoveredSaleError
from bogle.domain.ledger import first_uncovered, sellable_on
from bogle.domain.transactions import TransactionType
from bogle.repositories.transactions import TransactionRepository


def resolve_sale_shares(
    conn: psycopg.Connection[DictRow], ticker: str, shares: Decimal | None = None, *, when: datetime
) -> Decimal:
    """The quantity the sale will write, refused when the position on ``when`` cannot cover it.

    ``shares=None`` asks for the whole position at the close of that day — the
    answer to "zerar a posicao", which is also the only way to be sure the
    position really closes (and so that :func:`~bogle.closeout.clear_closed_target`
    really runs). Either way the sale has to leave every later sale covered.
    """
    on = when.astimezone(ZoneInfo(DEFAULT_TIMEZONE)).date()
    room = sellable_on(TransactionRepository(conn).list(ticker), on)
    quantity = room.held if shares is None else shares
    if room.held <= 0 or quantity > room.free:
        raise InsufficientSharesError(ticker.upper(), room.held, quantity, on=on, free=room.free, covers=room.covers)
    return quantity


def remove_transaction(conn: psycopg.Connection[DictRow], transaction_id: int) -> None:
    """Delete a transaction, refused when a sale would be left selling what was not held.

    Only a purchase can do that: removing a sale or an income leaves every
    position the same or larger. The check and the delete share one database
    transaction, like a sale and its check do.
    """
    transactions = TransactionRepository(conn)
    with conn.transaction():
        target = transactions.get(transaction_id)
        if target.transaction_type is TransactionType.BUY:
            rest = [txn for txn in transactions.list(target.ticker) if txn.id != transaction_id]
            uncovered = first_uncovered(rest)
            if uncovered is not None:
                day, missing = uncovered
                raise UncoveredSaleError(transaction_id, target.ticker, day, missing)
        transactions.delete(transaction_id)
