from __future__ import annotations

import os
import sqlite3
from pathlib import Path

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS assets (
    ticker       TEXT PRIMARY KEY,
    target_weight REAL NOT NULL,
    name         TEXT
);

CREATE TABLE IF NOT EXISTS transactions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker           TEXT    NOT NULL REFERENCES assets(ticker),
    purchase_date    TEXT    NOT NULL,
    shares           REAL   NOT NULL,
    unit_price       REAL   NOT NULL,
    total_investment REAL   NOT NULL,
    fees             REAL   NOT NULL DEFAULT 0,
    total_cost       REAL   NOT NULL
);

CREATE VIEW IF NOT EXISTS holdings AS
SELECT
    t.ticker,
    a.target_weight,
    SUM(t.shares)                        AS total_shares,
    SUM(t.total_cost)                    AS total_cost,
    SUM(t.total_cost) / SUM(t.shares)   AS avg_cost_per_share
FROM transactions t
JOIN assets a ON t.ticker = a.ticker
GROUP BY t.ticker;
"""


def get_db_path() -> Path:
    """Return the path to the SQLite database file.

    Respects the ``BOGLE_DB`` environment variable; falls back to
    ``~/.bogle/bogle.db``.
    """
    env = os.environ.get("BOGLE_DB")
    if env:
        return Path(env)
    return Path.home() / ".bogle" / "bogle.db"


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open (or create) the database and return a connection.

    Foreign-key enforcement is turned on and the row factory is set to
    ``sqlite3.Row`` so results behave like dicts.
    """
    if db_path is None:
        db_path = get_db_path()
    db_path = str(db_path)
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create tables and views if they don't already exist (idempotent)."""
    conn.executescript(_SCHEMA_SQL)
