from __future__ import annotations

import os

import psycopg
from psycopg.rows import dict_row

DEFAULT_DATABASE_URL = "postgresql://localhost/bogle"
DEFAULT_TIMEZONE = "America/Sao_Paulo"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS assets (
    ticker        TEXT PRIMARY KEY,
    target_weight NUMERIC(5, 4) NOT NULL,
    name          TEXT
);

CREATE TABLE IF NOT EXISTS transactions (
    id               BIGSERIAL    PRIMARY KEY,
    ticker           TEXT         NOT NULL REFERENCES assets(ticker),
    purchase_date    TIMESTAMPTZ  NOT NULL,
    shares           NUMERIC(20, 8) NOT NULL,
    unit_price       NUMERIC(20, 4) NOT NULL,
    total_investment NUMERIC(20, 4) NOT NULL,
    fees             NUMERIC(20, 4) NOT NULL DEFAULT 0,
    total_cost       NUMERIC(20, 4) NOT NULL
);

CREATE OR REPLACE VIEW holdings AS
SELECT
    t.ticker,
    a.target_weight,
    SUM(t.shares)                      AS total_shares,
    SUM(t.total_cost)                  AS total_cost,
    SUM(t.total_cost) / SUM(t.shares)  AS avg_cost_per_share
FROM transactions t
JOIN assets a ON t.ticker = a.ticker
GROUP BY t.ticker, a.target_weight;
"""


def get_database_url() -> str:
    """Return the PostgreSQL connection URL.

    Respects the ``BOGLE_DATABASE_URL`` environment variable; falls back to
    ``postgresql://localhost/bogle``.
    """
    return os.environ.get("BOGLE_DATABASE_URL", DEFAULT_DATABASE_URL)


def get_connection(database_url: str | None = None) -> psycopg.Connection:
    """Open a connection to PostgreSQL and configure the session.

    The session timezone is set to ``America/Sao_Paulo`` and rows are returned
    as ``dict``-like mappings.
    """
    if database_url is None:
        database_url = get_database_url()

    conn = psycopg.connect(database_url, row_factory=dict_row)
    with conn.cursor() as cur:
        cur.execute(f"SET TIME ZONE '{DEFAULT_TIMEZONE}'")
    conn.commit()
    return conn


def init_db(conn: psycopg.Connection) -> None:
    """Create tables and views if they don't already exist (idempotent)."""
    with conn.cursor() as cur:
        cur.execute(_SCHEMA_SQL)
    conn.commit()
