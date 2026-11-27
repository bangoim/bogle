from __future__ import annotations

from decimal import Decimal

import psycopg
from psycopg.rows import DictRow


class GlidePathRepository:
    """Read access to ``glide_path_points`` (issue #78).

    Writing the points is the job of ``bogle glide`` (issue #79); the engine only
    reads them.
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
