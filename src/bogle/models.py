"""Legacy data-access functions for holdings.

Asset CRUD lives in :mod:`bogle.repositories.assets` and transactions in
:mod:`bogle.repositories.transactions` (repository + domain dataclass +
custom errors). The holdings functions below will be migrated to the
same layered structure when the view is reworked for sales (#9).
"""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

# ---------------------------------------------------------------------------
# Holdings (read-only, backed by the SQL view)
# ---------------------------------------------------------------------------


def get_holdings(conn: psycopg.Connection) -> list[dict]:
    """Return the consolidated position for every asset that has transactions."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT ticker, target_weight, total_shares, total_cost,
                   avg_cost_per_share
            FROM holdings
            ORDER BY ticker
            """
        )
        return cur.fetchall()


def get_holding(conn: psycopg.Connection, ticker: str) -> dict | None:
    """Return the consolidated position for a single ticker, or ``None``."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT ticker, target_weight, total_shares, total_cost,
                   avg_cost_per_share
            FROM holdings
            WHERE ticker = %s
            """,
            (ticker.upper(),),
        )
        return cur.fetchone()
