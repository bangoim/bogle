"""How many shares a sale is allowed to sell.

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

Both frontends call this inside the same transaction as the sale, and both spell
the whole position the same way — ``--all`` on the command, "Vender tudo" on the
form — as ``shares=None``. That is the one quantity the app can fill in by
itself, and resolving it here (instead of on the screen that offered it) is what
keeps the number written equal to the number the ledger has.
"""

from __future__ import annotations

from decimal import Decimal

import psycopg
from psycopg.rows import DictRow

from bogle.domain.errors import InsufficientSharesError
from bogle.repositories.holdings import HoldingRepository

_ZERO = Decimal("0")


def available_shares(conn: psycopg.Connection[DictRow], ticker: str) -> Decimal:
    """Shares held in ``ticker`` right now; zero when the position is closed."""
    holding = HoldingRepository(conn).get(ticker)
    return holding.total_shares if holding is not None else _ZERO


def resolve_sale_shares(conn: psycopg.Connection[DictRow], ticker: str, shares: Decimal | None = None) -> Decimal:
    """The quantity the sale will write, refused when the position cannot cover it.

    ``shares=None`` asks for the whole position — the answer to "zerar a
    posicao", which is also the only way to be sure the position really closes
    (and so that :func:`~bogle.closeout.clear_closed_target` really runs).
    """
    available = available_shares(conn, ticker)
    if shares is None:
        if available <= _ZERO:
            raise InsufficientSharesError(ticker.upper(), available, available)
        return available
    if shares > available:
        raise InsufficientSharesError(ticker.upper(), available, shares)
    return shares
