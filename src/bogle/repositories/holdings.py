from __future__ import annotations

import psycopg
from psycopg.rows import DictRow

from bogle.domain.assets import AssetType
from bogle.domain.holdings import Holding

_SELECT_COLUMNS = "ticker, target_weight, asset_type, total_shares, total_invested"


def _row_to_holding(row: dict) -> Holding:
    return Holding(
        ticker=row["ticker"],
        target_weight=row["target_weight"],
        asset_type=AssetType(row["asset_type"]),
        total_shares=row["total_shares"],
        total_invested=row["total_invested"],
    )


class HoldingRepository:
    """Read-only access to the ``holdings`` view (active positions only)."""

    def __init__(self, conn: psycopg.Connection[DictRow]) -> None:
        self._conn = conn

    def list(self) -> list[Holding]:
        with self._conn.cursor() as cur:
            cur.execute(f"SELECT {_SELECT_COLUMNS} FROM holdings ORDER BY ticker")
            rows = cur.fetchall()
        return [_row_to_holding(r) for r in rows]

    def get(self, ticker: str) -> Holding | None:
        with self._conn.cursor() as cur:
            cur.execute(
                f"SELECT {_SELECT_COLUMNS} FROM holdings WHERE ticker = %s",
                (ticker.upper(),),
            )
            row = cur.fetchone()
        return _row_to_holding(row) if row is not None else None
