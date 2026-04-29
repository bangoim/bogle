from __future__ import annotations

from decimal import Decimal

import psycopg
from psycopg import errors as pg_errors

from bogle.domain.assets import Asset
from bogle.domain.errors import (
    AssetAlreadyExistsError,
    AssetHasTransactionsError,
    AssetNotFoundError,
    WeightSumExceededError,
)


class AssetRepository:
    """Data access for the ``assets`` table.

    All methods enforce the invariant ``SUM(target_weight) <= 1`` atomically:
    any operation that would break the invariant is rolled back and a
    ``WeightSumExceededError`` is raised.
    """

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, ticker: str) -> Asset | None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT ticker, target_weight FROM assets WHERE ticker = %s",
                (ticker.upper(),),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return Asset(ticker=row["ticker"], target_weight=row["target_weight"])

    def list(self) -> list[Asset]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT ticker, target_weight FROM assets ORDER BY ticker"
            )
            rows = cur.fetchall()
        return [
            Asset(ticker=r["ticker"], target_weight=r["target_weight"])
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def add(self, ticker: str, target_weight: Decimal) -> Asset:
        ticker = ticker.upper()
        try:
            with self._conn.transaction():
                with self._conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO assets (ticker, target_weight) "
                        "VALUES (%s, %s)",
                        (ticker, target_weight),
                    )
                    self._guard_weight_sum(cur)
        except pg_errors.UniqueViolation:
            raise AssetAlreadyExistsError(ticker) from None
        return Asset(ticker=ticker, target_weight=target_weight)

    def update_weight(self, ticker: str, target_weight: Decimal) -> Asset:
        ticker = ticker.upper()
        with self._conn.transaction():
            with self._conn.cursor() as cur:
                cur.execute(
                    "UPDATE assets SET target_weight = %s WHERE ticker = %s",
                    (target_weight, ticker),
                )
                if cur.rowcount == 0:
                    raise AssetNotFoundError(ticker)
                self._guard_weight_sum(cur)
        return Asset(ticker=ticker, target_weight=target_weight)

    def remove(self, ticker: str) -> None:
        ticker = ticker.upper()
        try:
            with self._conn.transaction():
                with self._conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM assets WHERE ticker = %s", (ticker,)
                    )
                    if cur.rowcount == 0:
                        raise AssetNotFoundError(ticker)
        except pg_errors.ForeignKeyViolation:
            raise AssetHasTransactionsError(ticker) from None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _guard_weight_sum(cur: psycopg.Cursor) -> None:
        cur.execute("SELECT COALESCE(SUM(target_weight), 0) AS total FROM assets")
        total = cur.fetchone()["total"]
        if total > Decimal("1"):
            raise WeightSumExceededError(total)
