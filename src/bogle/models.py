from __future__ import annotations

import sqlite3
from typing import Any

_UNSET = object()


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------

def add_asset(
    conn: sqlite3.Connection,
    ticker: str,
    target_weight: float,
    name: str | None = None,
) -> None:
    """Insert a new asset. Raises ``sqlite3.IntegrityError`` if the ticker
    already exists."""
    conn.execute(
        "INSERT INTO assets (ticker, target_weight, name) VALUES (?, ?, ?)",
        (ticker.upper(), target_weight, name),
    )
    conn.commit()


def get_asset(conn: sqlite3.Connection, ticker: str) -> sqlite3.Row | None:
    """Return a single asset row, or ``None`` if not found."""
    cur = conn.execute(
        "SELECT ticker, target_weight, name FROM assets WHERE ticker = ?",
        (ticker.upper(),),
    )
    return cur.fetchone()


def list_assets(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return all assets ordered by ticker."""
    cur = conn.execute(
        "SELECT ticker, target_weight, name FROM assets ORDER BY ticker"
    )
    return cur.fetchall()


def update_asset(
    conn: sqlite3.Connection,
    ticker: str,
    target_weight: float | None = None,
    name: str | None = _UNSET,  # type: ignore[assignment]
) -> bool:
    """Update mutable fields of an existing asset.

    Only the supplied keyword arguments are changed. Returns ``True`` if a row
    was updated.
    """
    fields: list[str] = []
    values: list[Any] = []

    if target_weight is not None:
        fields.append("target_weight = ?")
        values.append(target_weight)
    if name is not _UNSET:
        fields.append("name = ?")
        values.append(name)

    if not fields:
        return False

    values.append(ticker.upper())
    cur = conn.execute(
        f"UPDATE assets SET {', '.join(fields)} WHERE ticker = ?",  # noqa: S608
        values,
    )
    conn.commit()
    return cur.rowcount > 0


def delete_asset(conn: sqlite3.Connection, ticker: str) -> bool:
    """Delete an asset.

    Raises ``sqlite3.IntegrityError`` if the asset still has transactions
    (enforced by the foreign-key constraint).
    """
    cur = conn.execute(
        "DELETE FROM assets WHERE ticker = ?", (ticker.upper(),)
    )
    conn.commit()
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

def add_transaction(
    conn: sqlite3.Connection,
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

    cur = conn.execute(
        """
        INSERT INTO transactions
            (ticker, purchase_date, shares, unit_price,
             total_investment, fees, total_cost)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (ticker.upper(), purchase_date, shares, unit_price,
         total_investment, fees, total_cost),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def list_transactions(
    conn: sqlite3.Connection, ticker: str | None = None
) -> list[sqlite3.Row]:
    """Return transactions, optionally filtered by ticker."""
    if ticker:
        cur = conn.execute(
            """
            SELECT id, ticker, purchase_date, shares, unit_price,
                   total_investment, fees, total_cost
            FROM transactions
            WHERE ticker = ?
            ORDER BY purchase_date
            """,
            (ticker.upper(),),
        )
    else:
        cur = conn.execute(
            """
            SELECT id, ticker, purchase_date, shares, unit_price,
                   total_investment, fees, total_cost
            FROM transactions
            ORDER BY purchase_date
            """
        )
    return cur.fetchall()


def delete_transaction(conn: sqlite3.Connection, transaction_id: int) -> bool:
    """Delete a transaction by id. Returns ``True`` if a row was deleted."""
    cur = conn.execute(
        "DELETE FROM transactions WHERE id = ?", (transaction_id,)
    )
    conn.commit()
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Holdings (read-only, backed by the SQL view)
# ---------------------------------------------------------------------------

def get_holdings(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return the consolidated position for every asset that has transactions."""
    cur = conn.execute(
        """
        SELECT ticker, target_weight, total_shares, total_cost,
               avg_cost_per_share
        FROM holdings
        ORDER BY ticker
        """
    )
    return cur.fetchall()


def get_holding(
    conn: sqlite3.Connection, ticker: str
) -> sqlite3.Row | None:
    """Return the consolidated position for a single ticker, or ``None``."""
    cur = conn.execute(
        """
        SELECT ticker, target_weight, total_shares, total_cost,
               avg_cost_per_share
        FROM holdings
        WHERE ticker = ?
        """,
        (ticker.upper(),),
    )
    return cur.fetchone()
