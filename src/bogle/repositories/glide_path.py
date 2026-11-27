from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

import psycopg
from psycopg import errors as pg_errors
from psycopg.rows import DictRow

from bogle.domain.errors import AssetNotFoundError


class GlidePathRepository:
    """Data access for ``glide_path_points`` (issues #78, #79).

    The rules a point must follow (an age the app accepts, tickers that exist,
    weights that add up to exactly 1) are checked by :mod:`bogle.glide_path`
    before anything reaches here; this layer only reads and writes rows.
    """

    def __init__(self, conn: psycopg.Connection[DictRow]) -> None:
        self._conn = conn

    def points(self) -> dict[int, dict[str, Decimal]]:
        """Every anchor of the trajectory: ``{age: {ticker: target_weight}}``, by age."""
        with self._conn.cursor() as cur:
            cur.execute("SELECT age, ticker, target_weight FROM glide_path_points ORDER BY age, ticker")
            rows = cur.fetchall()
        points: dict[int, dict[str, Decimal]] = {}
        for row in rows:
            points.setdefault(row["age"], {})[row["ticker"]] = row["target_weight"]
        return points

    def replace_point(self, age: int, weights: Mapping[str, Decimal]) -> None:
        """Make ``weights`` the whole anchor of ``age``: tickers left out leave it."""
        try:
            with self._conn.transaction(), self._conn.cursor() as cur:
                cur.execute("DELETE FROM glide_path_points WHERE age = %s", (age,))
                cur.executemany(
                    "INSERT INTO glide_path_points (age, ticker, target_weight) VALUES (%s, %s, %s)",
                    [(age, ticker.upper(), weight) for ticker, weight in weights.items()],
                )
        except pg_errors.ForeignKeyViolation:
            # So por corrida: quem chama ja conferiu que os tickers existem.
            raise AssetNotFoundError(", ".join(sorted(weights))) from None

    def remove_point(self, age: int) -> bool:
        """Drop the anchor of ``age``; ``False`` when there was none."""
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute("DELETE FROM glide_path_points WHERE age = %s", (age,))
            return cur.rowcount > 0

    def clear(self) -> int:
        """Drop every anchor, and say how many there were."""
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute("SELECT COUNT(DISTINCT age) AS ages FROM glide_path_points")
            row = cur.fetchone()
            cur.execute("DELETE FROM glide_path_points")
        return row["ages"] if row is not None else 0
