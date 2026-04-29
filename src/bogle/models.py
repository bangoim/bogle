from __future__ import annotations

import psycopg


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------

def add_asset(
    conn: psycopg.Connection,
    ticker: str,
    target_weight: float,
) -> None:
    """Insert a new asset. Raises ``psycopg.errors.UniqueViolation`` if the
    ticker already exists."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO assets (ticker, target_weight) VALUES (%s, %s)",
            (ticker.upper(), target_weight),
        )
    conn.commit()


def get_asset(conn: psycopg.Connection, ticker: str) -> dict | None:
    """Return a single asset row, or ``None`` if not found."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ticker, target_weight FROM assets WHERE ticker = %s",
            (ticker.upper(),),
        )
        return cur.fetchone()


def list_assets(conn: psycopg.Connection) -> list[dict]:
    """Return all assets ordered by ticker."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ticker, target_weight FROM assets ORDER BY ticker"
        )
        return cur.fetchall()


def update_asset(
    conn: psycopg.Connection,
    ticker: str,
    target_weight: float,
) -> bool:
    """Update the target_weight of an existing asset.

    Returns ``True`` if a row was updated.
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE assets SET target_weight = %s WHERE ticker = %s",
            (target_weight, ticker.upper()),
        )
        rowcount = cur.rowcount
    conn.commit()
    return rowcount > 0


def delete_asset(conn: psycopg.Connection, ticker: str) -> bool:
    """Delete an asset.

    Raises ``psycopg.errors.ForeignKeyViolation`` if the asset still has
    transactions (enforced by the foreign-key constraint).
    """
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM assets WHERE ticker = %s", (ticker.upper(),)
        )
        rowcount = cur.rowcount
    conn.commit()
    return rowcount > 0


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

def add_transaction(
    conn: psycopg.Connection,
    ticker: str,
    purchase_date: str,
    shares: float,
    unit_price: float,
    fees: float = 0.0,
) -> int:
    """Record a purchase and return the new transaction id.

    ``total_investment`` (shares * unit_price) and ``total_cost``
    (total_investment + fees) are computed automatically.
    """
    total_investment = shares * unit_price
    total_cost = total_investment + fees

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO transactions
                (ticker, purchase_date, shares, unit_price,
                 total_investment, fees, total_cost)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (ticker.upper(), purchase_date, shares, unit_price,
             total_investment, fees, total_cost),
        )
        row = cur.fetchone()
    conn.commit()
    return row["id"]


def list_transactions(
    conn: psycopg.Connection, ticker: str | None = None
) -> list[dict]:
    """Return transactions, optionally filtered by ticker."""
    with conn.cursor() as cur:
        if ticker:
            cur.execute(
                """
                SELECT id, ticker, purchase_date, shares, unit_price,
                       total_investment, fees, total_cost
                FROM transactions
                WHERE ticker = %s
                ORDER BY purchase_date
                """,
                (ticker.upper(),),
            )
        else:
            cur.execute(
                """
                SELECT id, ticker, purchase_date, shares, unit_price,
                       total_investment, fees, total_cost
                FROM transactions
                ORDER BY purchase_date
                """
            )
        return cur.fetchall()


def delete_transaction(conn: psycopg.Connection, transaction_id: int) -> bool:
    """Delete a transaction by id. Returns ``True`` if a row was deleted."""
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM transactions WHERE id = %s", (transaction_id,)
        )
        rowcount = cur.rowcount
    conn.commit()
    return rowcount > 0


# ---------------------------------------------------------------------------
# Holdings (read-only, backed by the SQL view)
# ---------------------------------------------------------------------------

def get_holdings(conn: psycopg.Connection) -> list[dict]:
    """Return the consolidated position for every asset that has transactions."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ticker, target_weight, total_shares, total_cost,
                   avg_cost_per_share
            FROM holdings
            ORDER BY ticker
            """
        )
        return cur.fetchall()


def get_holding(
    conn: psycopg.Connection, ticker: str
) -> dict | None:
    """Return the consolidated position for a single ticker, or ``None``."""
    with conn.cursor() as cur:
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
