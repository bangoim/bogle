"""Data access for ``price_history`` (issue #82): the closes the app has already seen.

Two writers with different rights, which is the whole policy of the table:

- ``replace=False`` only *adds* sessions: Yahoo loading the long history, or
  covering the recent window while brapi is down. A close already stored is never
  overwritten by it.
- ``replace=True`` is brapi revalidating the recent window. A value that changed
  is corrected (and the row's ``source`` becomes the new writer); an equal one
  keeps the source it had, since that provider did say the same thing first.

``confirm`` stamps ``loaded_on`` on every row of a date range, changed or not.
That is what "this window was checked today" looks like in the table, and what
lets the next screen of the day skip the providers.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import psycopg
from psycopg.rows import DictRow

from bogle.data.models import StoredClose, StoredSpan
from bogle.db import get_connection

_CENT = Decimal("0.01")

_INSERT = """
    INSERT INTO price_history (symbol, date, close, source, loaded_on)
    VALUES (%s, %s, %s, %s, %s)
"""

_ADD_ONLY = _INSERT + "ON CONFLICT (symbol, date) DO NOTHING"

# Nos SET de um ON CONFLICT, `price_history.*` e a linha antiga: a comparacao do
# source ve o close de antes, qualquer que seja a ordem das atribuicoes.
_REPLACE = (
    _INSERT
    + """
    ON CONFLICT (symbol, date) DO UPDATE SET
        source = CASE WHEN price_history.close = EXCLUDED.close
                      THEN price_history.source ELSE EXCLUDED.source END,
        close = EXCLUDED.close,
        loaded_on = EXCLUDED.loaded_on
"""
)


def _cents(value: Decimal) -> Decimal:
    """Two places, rounded on the way in: Yahoo answers ``112.95999908``."""
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


class PriceHistoryRepository:
    """Reads and writes ``price_history`` over the connection it is given."""

    def __init__(self, conn: psycopg.Connection[DictRow]) -> None:
        self._conn = conn

    def span(self, symbol: str) -> StoredSpan | None:
        """First and last stored session of ``symbol``, and its last load day."""
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT MIN(date) AS first, MAX(date) AS last, MAX(loaded_on) AS loaded_on "
                "FROM price_history WHERE symbol = %s",
                (symbol,),
            )
            row = cur.fetchone()
        if row is None or row["first"] is None:
            return None
        return StoredSpan(first=row["first"], last=row["last"], loaded_on=row["loaded_on"])

    def closes(self, symbol: str, start: date, end: date) -> list[StoredClose]:
        """Stored closes of ``symbol`` in ``[start, end]``, oldest first."""
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT date, close, source FROM price_history "
                "WHERE symbol = %s AND date BETWEEN %s AND %s ORDER BY date",
                (symbol, start, end),
            )
            rows = cur.fetchall()
        return [StoredClose(date=row["date"], close=row["close"], source=row["source"]) for row in rows]

    def save(
        self,
        symbol: str,
        closes: Sequence[StoredClose],
        *,
        loaded_on: date,
        replace: bool = False,
        confirm: tuple[date, date] | None = None,
    ) -> None:
        """Write ``closes`` and, given ``confirm``, stamp the whole range as loaded.

        One transaction: a window half corrected and half confirmed would read as
        checked today while part of it never was.
        """
        params = [(symbol, row.date, _cents(row.close), row.source, loaded_on) for row in closes]
        with self._conn.transaction(), self._conn.cursor() as cur:
            if params:
                cur.executemany(_REPLACE if replace else _ADD_ONLY, params)
            if confirm is not None:
                cur.execute(
                    "UPDATE price_history SET loaded_on = %s WHERE symbol = %s AND date BETWEEN %s AND %s",
                    (loaded_on, symbol, *confirm),
                )


class PriceHistoryStore:
    """The repository behind one connection per call: what the dispatcher holds.

    A dispatcher lives as long as a command or a screen, and on the interface it
    runs in worker threads. Holding a connection for that long would share it
    across threads and keep it idle between screens; opening one per operation is
    the rule everywhere else in the app (see :func:`bogle.db.get_connection`).
    """

    def __init__(self, connect: Callable[[], psycopg.Connection[DictRow]] = get_connection) -> None:
        self._connect = connect

    def span(self, symbol: str) -> StoredSpan | None:
        conn = self._connect()
        try:
            return PriceHistoryRepository(conn).span(symbol)
        finally:
            conn.close()

    def closes(self, symbol: str, start: date, end: date) -> list[StoredClose]:
        conn = self._connect()
        try:
            return PriceHistoryRepository(conn).closes(symbol, start, end)
        finally:
            conn.close()

    def save(
        self,
        symbol: str,
        closes: Sequence[StoredClose],
        *,
        loaded_on: date,
        replace: bool = False,
        confirm: tuple[date, date] | None = None,
    ) -> None:
        conn = self._connect()
        try:
            PriceHistoryRepository(conn).save(symbol, closes, loaded_on=loaded_on, replace=replace, confirm=confirm)
        finally:
            conn.close()
