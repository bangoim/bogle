from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import psycopg
from psycopg import errors as pg_errors

from bogle.domain.assets import Asset, AssetType, Indexer
from bogle.domain.errors import (
    AssetAlreadyExistsError,
    AssetHasTransactionsError,
    AssetNotFoundError,
    ValidationError,
    WeightSumExceededError,
)

_SELECT_COLUMNS = (
    "ticker, target_weight, asset_type, issuer, indexer, rate, "
    "is_prefixed, daily_liquidity, purchase_date, maturity_date"
)


def _row_to_asset(row: dict) -> Asset:
    return Asset(
        ticker=row["ticker"],
        target_weight=row["target_weight"],
        asset_type=AssetType(row["asset_type"]),
        issuer=row["issuer"],
        indexer=Indexer(row["indexer"]) if row["indexer"] is not None else None,
        rate=row["rate"],
        is_prefixed=row["is_prefixed"],
        daily_liquidity=row["daily_liquidity"],
        purchase_date=row["purchase_date"],
        maturity_date=row["maturity_date"],
    )


class AssetRepository:
    """Data access for the ``assets`` table.

    All write methods enforce the invariant ``SUM(target_weight) <= 1``
    atomically: any operation that would break the invariant is rolled
    back and a ``WeightSumExceededError`` is raised.
    """

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, ticker: str) -> Asset | None:
        with self._conn.cursor() as cur:
            cur.execute(
                f"SELECT {_SELECT_COLUMNS} FROM assets WHERE ticker = %s",
                (ticker.upper(),),
            )
            row = cur.fetchone()
        return _row_to_asset(row) if row is not None else None

    def list(self) -> list[Asset]:
        with self._conn.cursor() as cur:
            cur.execute(
                f"SELECT {_SELECT_COLUMNS} FROM assets ORDER BY ticker"
            )
            rows = cur.fetchall()
        return [_row_to_asset(r) for r in rows]

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def add(
        self,
        ticker: str,
        target_weight: Decimal,
        *,
        asset_type: AssetType = AssetType.STOCK,
        issuer: str | None = None,
        indexer: Indexer | None = None,
        rate: Decimal | None = None,
        is_prefixed: bool | None = None,
        daily_liquidity: bool | None = None,
        purchase_date: datetime | None = None,
        maturity_date: datetime | None = None,
    ) -> Asset:
        ticker = ticker.upper()
        try:
            with self._conn.transaction(), self._conn.cursor() as cur:
                cur.execute(
                    """
                        INSERT INTO assets (
                            ticker, target_weight, asset_type, issuer,
                            indexer, rate, is_prefixed, daily_liquidity,
                            purchase_date, maturity_date
                        ) VALUES (
                            %s, %s, %s, %s,
                            %s, %s, %s, %s,
                            %s, %s
                        )
                        """,
                    (
                        ticker, target_weight, asset_type.value, issuer,
                        indexer.value if indexer is not None else None,
                        rate, is_prefixed, daily_liquidity,
                        purchase_date, maturity_date,
                    ),
                )
                self._guard_weight_sum(cur)
        except pg_errors.UniqueViolation:
            raise AssetAlreadyExistsError(ticker) from None
        except pg_errors.CheckViolation as exc:
            raise ValidationError(
                f"Combinacao invalida de campos para o tipo {asset_type.value} "
                f"(constraint {exc.diag.constraint_name})."
            ) from None
        return Asset(
            ticker=ticker,
            target_weight=target_weight,
            asset_type=asset_type,
            issuer=issuer,
            indexer=indexer,
            rate=rate,
            is_prefixed=is_prefixed,
            daily_liquidity=daily_liquidity,
            purchase_date=purchase_date,
            maturity_date=maturity_date,
        )

    def update_weight(self, ticker: str, target_weight: Decimal) -> Asset:
        ticker = ticker.upper()
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(
                "UPDATE assets SET target_weight = %s WHERE ticker = %s",
                (target_weight, ticker),
            )
            if cur.rowcount == 0:
                raise AssetNotFoundError(ticker)
            self._guard_weight_sum(cur)
        # Return the freshly updated row (need full state, not just weight).
        result = self.get(ticker)
        assert result is not None  # row existed (rowcount > 0).
        return result

    def remove(self, ticker: str) -> None:
        ticker = ticker.upper()
        try:
            with self._conn.transaction(), self._conn.cursor() as cur:
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
