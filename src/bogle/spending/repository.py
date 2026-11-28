"""SQL over the ``spending`` schema (issue #114).

Plain functions over a connection, like the rest of the code that reads and
writes this schema. Atomicity is the caller's: the sync wraps each account in
one ``conn.transaction()``.

Contract for #115 onward: a transaction with a PENDING duplicate suggestion in
``transaction_id`` stays out of every analysis (:func:`suspected_duplicate_ids`).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from bogle.data.pluggy import PluggyAccount, PluggyBill, PluggyCategory, PluggyItem, PluggyTransaction, dumps
from bogle.spending.models import AccountStatus, ItemState, StoredAccount, StoredTransaction, account_label

Connection = psycopg.Connection[DictRow]

SYNC_LOCK = 114_2026
"""Key of the advisory lock that keeps two syncs from writing at once."""


def _json(value: Any) -> Jsonb:
    return Jsonb(value, dumps=dumps)


# ---------------------------------------------------------------- trava


def try_lock(conn: Connection) -> bool:
    row = conn.execute("SELECT pg_try_advisory_lock(%s) AS locked", (SYNC_LOCK,)).fetchone()
    return bool(row and row["locked"])


def unlock(conn: Connection) -> None:
    conn.execute("SELECT pg_advisory_unlock(%s)", (SYNC_LOCK,))


# ----------------------------------------------------------- categorias


def upsert_categories(conn: Connection, categories: Sequence[PluggyCategory]) -> None:
    if not categories:
        return
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO spending.categories (id, description, description_translated, parent_id, parent_description)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                description = EXCLUDED.description,
                description_translated = EXCLUDED.description_translated,
                parent_id = EXCLUDED.parent_id,
                parent_description = EXCLUDED.parent_description
            """,
            [(c.id, c.description, c.description_translated, c.parent_id, c.parent_description) for c in categories],
        )


# ----------------------------------------------------------------- itens


def upsert_item(conn: Connection, item: PluggyItem, *, checked_at: datetime) -> None:
    conn.execute(
        """
        INSERT INTO spending.items (
            id, connector_id, connector_name, status, execution_status, error_code, error_message,
            pluggy_created_at, last_updated_at, consent_expires_at, checked_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            connector_id = EXCLUDED.connector_id,
            connector_name = EXCLUDED.connector_name,
            status = EXCLUDED.status,
            execution_status = EXCLUDED.execution_status,
            error_code = EXCLUDED.error_code,
            error_message = EXCLUDED.error_message,
            pluggy_created_at = EXCLUDED.pluggy_created_at,
            last_updated_at = EXCLUDED.last_updated_at,
            consent_expires_at = EXCLUDED.consent_expires_at,
            checked_at = EXCLUDED.checked_at
        """,
        (
            item.id,
            item.connector_id,
            item.connector_name,
            item.status,
            item.execution_status,
            item.error_code,
            item.error_message,
            item.created_at,
            item.last_updated_at,
            item.consent_expires_at,
            checked_at,
        ),
    )


def delete_items_except(conn: Connection, item_ids: Sequence[str]) -> None:
    """Connections no longer configured leave; their accounts stay, without an item."""
    conn.execute("DELETE FROM spending.items WHERE NOT (id = ANY(%s))", (list(item_ids),))


def item_states(conn: Connection) -> list[ItemState]:
    """Every stored connection, named after its accounts (else the connector, else the id)."""
    rows = conn.execute(
        """
        SELECT i.*, (
            SELECT array_agg(COALESCE(a.marketing_name, a.name) ORDER BY a.type, a.id)
            FROM spending.accounts a WHERE a.item_id = i.id
        ) AS account_names
        FROM spending.items i
        ORDER BY i.id
        """
    ).fetchall()
    states = []
    for row in rows:
        names = list(dict.fromkeys(row["account_names"] or []))
        label = ", ".join(names) or row["connector_name"] or row["id"][:8]
        states.append(
            ItemState(
                id=row["id"],
                label=label,
                status=row["status"],
                execution_status=row["execution_status"],
                error_code=row["error_code"],
                last_updated_at=row["last_updated_at"],
                consent_expires_at=row["consent_expires_at"],
                checked_at=row["checked_at"],
            )
        )
    return sorted(states, key=lambda state: (state.label, state.id))


# ---------------------------------------------------------------- contas


def _stored_account(row: DictRow) -> StoredAccount:
    return StoredAccount(
        id=row["id"],
        pluggy_id=row["pluggy_id"],
        replaced_pluggy_ids=tuple(row["replaced_pluggy_ids"] or ()),
        item_id=row["item_id"],
        type=row["type"],
        subtype=row["subtype"],
        number=row["number"],
        name=row["name"],
        marketing_name=row["marketing_name"],
        tax_number=row["tax_number"],
        transfer_number=row["transfer_number"],
        credit_brand=row["credit_brand"],
        last_synced_at=row["last_synced_at"],
        first_transaction_date=row["first_transaction_date"],
        last_transaction_date=row["last_transaction_date"],
    )


def accounts(conn: Connection) -> list[StoredAccount]:
    rows = conn.execute("SELECT * FROM spending.accounts ORDER BY id").fetchall()
    return [_stored_account(row) for row in rows]


def account(conn: Connection, account_id: int) -> StoredAccount:
    row = conn.execute("SELECT * FROM spending.accounts WHERE id = %s", (account_id,)).fetchone()
    assert row is not None  # a conta acabou de ser gravada na mesma transacao
    return _stored_account(row)


def _account_values(fresh: PluggyAccount, item_id: str) -> dict[str, Any]:
    return {
        "pluggy_id": fresh.id,
        "item_id": item_id,
        "type": fresh.type,
        "subtype": fresh.subtype,
        "number": fresh.number,
        "name": fresh.name,
        "marketing_name": fresh.marketing_name,
        "owner": fresh.owner,
        "tax_number": fresh.tax_number,
        "currency_code": fresh.currency_code,
        "balance": fresh.balance,
        "transfer_number": fresh.transfer_number,
        "credit_brand": fresh.credit_brand,
        "credit_level": fresh.credit_level,
        "credit_limit": fresh.credit_limit,
        "available_credit_limit": fresh.available_credit_limit,
        "minimum_payment": fresh.minimum_payment,
        "balance_close_date": fresh.balance_close_date,
        "balance_due_date": fresh.balance_due_date,
        "raw": _json(fresh.raw),
    }


_ACCOUNT_COLUMNS = (
    "pluggy_id",
    "item_id",
    "type",
    "subtype",
    "number",
    "name",
    "marketing_name",
    "owner",
    "tax_number",
    "currency_code",
    "balance",
    "transfer_number",
    "credit_brand",
    "credit_level",
    "credit_limit",
    "available_credit_limit",
    "minimum_payment",
    "balance_close_date",
    "balance_due_date",
    "raw",
)


def insert_account(conn: Connection, fresh: PluggyAccount, item_id: str) -> int:
    columns = ", ".join(_ACCOUNT_COLUMNS)
    values = ", ".join(f"%({column})s" for column in _ACCOUNT_COLUMNS)
    row = conn.execute(
        f"INSERT INTO spending.accounts ({columns}) VALUES ({values}) RETURNING id",
        _account_values(fresh, item_id),
    ).fetchone()
    assert row is not None  # INSERT ... RETURNING sempre devolve a linha
    return row["id"]


def update_account(
    conn: Connection, account_id: int, fresh: PluggyAccount, item_id: str, *, replaced: str | None
) -> None:
    """Refresh the account from Pluggy; ``replaced`` is the id it had, when it was inherited."""
    assignments = ", ".join(f"{column} = %({column})s" for column in _ACCOUNT_COLUMNS)
    values = _account_values(fresh, item_id) | {"id": account_id, "replaced": replaced}
    conn.execute(
        f"""
        UPDATE spending.accounts SET {assignments},
            replaced_pluggy_ids = CASE
                WHEN %(replaced)s::text IS NULL OR %(replaced)s::text = ANY(replaced_pluggy_ids)
                THEN replaced_pluggy_ids
                ELSE array_append(replaced_pluggy_ids, %(replaced)s::text)
            END
        WHERE id = %(id)s
        """,
        values,
    )


def refresh_freshness(conn: Connection, account_id: int, *, now: datetime, today: date) -> None:
    """Last sync, newest past transaction and oldest one, recomputed from what is stored.

    The coverage never moves forward: pruned PENDING lines cannot make the
    history look shorter than it was.
    """
    conn.execute(
        """
        UPDATE spending.accounts a SET
            last_synced_at = %(now)s,
            last_transaction_date = s.last_date,
            first_transaction_date = CASE
                WHEN a.first_transaction_date IS NULL THEN s.first_date
                WHEN s.first_date IS NULL THEN a.first_transaction_date
                ELSE LEAST(a.first_transaction_date, s.first_date)
            END
        FROM (
            SELECT MIN(local_date) AS first_date, MAX(local_date) FILTER (WHERE local_date <= %(today)s) AS last_date
            FROM spending.transactions WHERE account_id = %(id)s
        ) s
        WHERE a.id = %(id)s
        """,
        {"now": now, "today": today, "id": account_id},
    )


def account_statuses(conn: Connection) -> list[AccountStatus]:
    rows = conn.execute(
        """
        SELECT name, marketing_name, item_id, last_synced_at, last_transaction_date, first_transaction_date
        FROM spending.accounts ORDER BY type, COALESCE(marketing_name, name), id
        """
    ).fetchall()
    return [
        AccountStatus(
            label=account_label(row["name"], row["marketing_name"]),
            item_id=row["item_id"],
            last_synced_at=row["last_synced_at"],
            last_transaction_date=row["last_transaction_date"],
            first_transaction_date=row["first_transaction_date"],
        )
        for row in rows
    ]


# ------------------------------------------------------------ transacoes


def oldest_pending(conn: Connection, account_id: int) -> date | None:
    row = conn.execute(
        "SELECT MIN(local_date) AS day FROM spending.transactions WHERE account_id = %s AND status = 'PENDING'",
        (account_id,),
    ).fetchone()
    return row["day"] if row else None


def account_transactions(conn: Connection, account_id: int) -> list[StoredTransaction]:
    rows = conn.execute(
        """
        SELECT id, pluggy_id, provider_id, local_date, amount, status, description,
               date AS moment, installment_number
        FROM spending.transactions WHERE account_id = %s ORDER BY id
        """,
        (account_id,),
    ).fetchall()
    return [StoredTransaction(**row) for row in rows]


_TRANSACTION_COLUMNS = (
    "pluggy_id",
    "account_id",
    "provider_id",
    "provider_code",
    "date",
    "local_date",
    "status",
    "type",
    "amount",
    "currency_code",
    "amount_in_account_currency",
    "balance",
    "description",
    "description_raw",
    "category_id",
    "category",
    "operation_type",
    "merchant_name",
    "merchant_business_name",
    "merchant_cnpj",
    "merchant_cnae",
    "merchant_category",
    "payment_data",
    "bill_id",
    "bill_post_date",
    "bill_forecast_month",
    "payment_type",
    "installment_number",
    "total_installments",
    "installments_total_amount",
    "purchase_date",
    "payee_mcc",
    "card_number",
    "pluggy_created_at",
    "pluggy_updated_at",
    "raw",
)


def _transaction_values(fresh: PluggyTransaction, account_id: int) -> dict[str, Any]:
    return {
        "pluggy_id": fresh.id,
        "account_id": account_id,
        "provider_id": fresh.provider_id,
        "provider_code": fresh.provider_code,
        "date": fresh.date,
        "local_date": fresh.local_date,
        "status": fresh.status,
        "type": fresh.type,
        "amount": fresh.amount,
        "currency_code": fresh.currency_code,
        "amount_in_account_currency": fresh.amount_in_account_currency,
        "balance": fresh.balance,
        "description": fresh.description,
        "description_raw": fresh.description_raw,
        "category_id": fresh.category_id,
        "category": fresh.category,
        "operation_type": fresh.operation_type,
        "merchant_name": fresh.merchant_name,
        "merchant_business_name": fresh.merchant_business_name,
        "merchant_cnpj": fresh.merchant_cnpj,
        "merchant_cnae": fresh.merchant_cnae,
        "merchant_category": fresh.merchant_category,
        "payment_data": _json(fresh.payment_data) if fresh.payment_data is not None else None,
        "bill_id": fresh.bill_id,
        "bill_post_date": fresh.bill_post_date,
        "bill_forecast_month": fresh.bill_forecast_month,
        "payment_type": fresh.payment_type,
        "installment_number": fresh.installment_number,
        "total_installments": fresh.total_installments,
        "installments_total_amount": fresh.installments_total_amount,
        "purchase_date": fresh.purchase_date,
        "payee_mcc": fresh.payee_mcc,
        "card_number": fresh.card_number,
        "pluggy_created_at": fresh.created_at,
        "pluggy_updated_at": fresh.updated_at,
        "raw": _json(fresh.raw),
    }


def insert_transaction(conn: Connection, fresh: PluggyTransaction, account_id: int, *, now: datetime) -> int:
    columns = ", ".join((*_TRANSACTION_COLUMNS, "first_seen_at", "last_seen_at"))
    values = ", ".join((*(f"%({column})s" for column in _TRANSACTION_COLUMNS), "%(now)s", "%(now)s"))
    row = conn.execute(
        f"INSERT INTO spending.transactions ({columns}) VALUES ({values}) RETURNING id",
        _transaction_values(fresh, account_id) | {"now": now},
    ).fetchone()
    assert row is not None  # INSERT ... RETURNING sempre devolve a linha
    return row["id"]


def update_transaction(
    conn: Connection, transaction_id: int, fresh: PluggyTransaction, account_id: int, *, now: datetime
) -> None:
    """Overwrite the stored line with Pluggy's, its id included (a remap changes it)."""
    assignments = ", ".join(f"{column} = %({column})s" for column in _TRANSACTION_COLUMNS)
    conn.execute(
        f"UPDATE spending.transactions SET {assignments}, last_seen_at = %(now)s WHERE id = %(id)s",
        _transaction_values(fresh, account_id) | {"now": now, "id": transaction_id},
    )


def delete_transactions(conn: Connection, transaction_ids: Iterable[int]) -> int:
    ids = list(transaction_ids)
    if not ids:
        return 0
    cursor = conn.execute("DELETE FROM spending.transactions WHERE id = ANY(%s)", (ids,))
    return cursor.rowcount


# --------------------------------------------------------------- faturas


def account_bills(conn: Connection, account_id: int) -> list[tuple[int, str, date]]:
    rows = conn.execute(
        "SELECT id, pluggy_id, due_date FROM spending.bills WHERE account_id = %s ORDER BY id", (account_id,)
    ).fetchall()
    return [(row["id"], row["pluggy_id"], row["due_date"]) for row in rows]


def _bill_values(fresh: PluggyBill, account_id: int, now: datetime) -> dict[str, Any]:
    return {
        "pluggy_id": fresh.id,
        "account_id": account_id,
        "due_date": fresh.due_date,
        "closing_date": fresh.closing_date,
        "total_amount": fresh.total_amount,
        "currency_code": fresh.currency_code,
        "minimum_payment_amount": fresh.minimum_payment_amount,
        "allows_installments": fresh.allows_installments,
        "finance_charges": _json(fresh.finance_charges),
        "payments": _json(fresh.payments),
        "raw": _json(fresh.raw),
        "now": now,
    }


_BILL_COLUMNS = (
    "pluggy_id",
    "account_id",
    "due_date",
    "closing_date",
    "total_amount",
    "currency_code",
    "minimum_payment_amount",
    "allows_installments",
    "finance_charges",
    "payments",
    "raw",
)


def insert_bill(conn: Connection, fresh: PluggyBill, account_id: int, *, now: datetime) -> None:
    columns = ", ".join((*_BILL_COLUMNS, "first_seen_at", "last_seen_at"))
    values = ", ".join((*(f"%({column})s" for column in _BILL_COLUMNS), "%(now)s", "%(now)s"))
    conn.execute(
        f"INSERT INTO spending.bills ({columns}) VALUES ({values})",
        _bill_values(fresh, account_id, now),
    )


def update_bill(conn: Connection, bill_id: int, fresh: PluggyBill, account_id: int, *, now: datetime) -> None:
    assignments = ", ".join(f"{column} = %({column})s" for column in _BILL_COLUMNS)
    conn.execute(
        f"UPDATE spending.bills SET {assignments}, last_seen_at = %(now)s WHERE id = %(id)s",
        _bill_values(fresh, account_id, now) | {"id": bill_id},
    )


def rewrite_bill_id(conn: Connection, account_id: int, old: str, new: str) -> None:
    """Point the account's transactions at the bill's new Pluggy id."""
    conn.execute(
        "UPDATE spending.transactions SET bill_id = %s WHERE account_id = %s AND bill_id = %s",
        (new, account_id, old),
    )


# ------------------------------------------------------------ duplicatas


def suggested_candidates(conn: Connection, account_id: int) -> set[int]:
    """Stored transactions that already were the older side of a suggestion."""
    rows = conn.execute(
        """
        SELECT s.duplicate_of_id AS id FROM spending.duplicate_suggestions s
        JOIN spending.transactions t ON t.id = s.duplicate_of_id
        WHERE t.account_id = %s
        """,
        (account_id,),
    ).fetchall()
    return {row["id"] for row in rows}


def insert_suggestion(conn: Connection, transaction_id: int, duplicate_of_id: int) -> None:
    conn.execute(
        "INSERT INTO spending.duplicate_suggestions (transaction_id, duplicate_of_id) VALUES (%s, %s)",
        (transaction_id, duplicate_of_id),
    )


def suspected_duplicate_ids(conn: Connection) -> set[int]:
    """Transactions held out of the analyses: the newer side of a PENDING suggestion."""
    rows = conn.execute("SELECT transaction_id FROM spending.duplicate_suggestions WHERE status = 'PENDING'").fetchall()
    return {row["transaction_id"] for row in rows}


def pending_duplicates(conn: Connection) -> int:
    row = conn.execute("SELECT count(*) AS n FROM spending.duplicate_suggestions WHERE status = 'PENDING'").fetchone()
    return row["n"] if row else 0


def merge_duplicate(conn: Connection, suggestion_id: int) -> bool:
    """Fold the newer side of a PENDING suggestion into the older one.

    The older line keeps its local id (what anything else points at) and takes
    every field of the newer one, its Pluggy id included; the newer line goes,
    and the suggestion with it. ``False`` when there is no such PENDING suggestion.
    """
    row = conn.execute(
        """
        SELECT transaction_id, duplicate_of_id FROM spending.duplicate_suggestions
        WHERE id = %s AND status = 'PENDING' FOR UPDATE
        """,
        (suggestion_id,),
    ).fetchone()
    if row is None:
        return False
    newer = conn.execute(
        "DELETE FROM spending.transactions WHERE id = %s RETURNING *", (row["transaction_id"],)
    ).fetchone()
    assert newer is not None  # a FK da sugestao garante a linha
    columns = [column for column in _TRANSACTION_COLUMNS if column != "account_id"]
    assignments = sql.SQL(", ").join(
        sql.SQL("{} = {}").format(sql.Identifier(column), sql.Placeholder(column)) for column in columns
    )
    values = {column: newer[column] for column in columns}
    for column in ("payment_data", "raw"):
        if values[column] is not None:
            values[column] = _json(values[column])
    conn.execute(
        sql.SQL("UPDATE spending.transactions SET {}, last_seen_at = %(last_seen_at)s WHERE id = %(id)s").format(
            assignments
        ),
        values | {"last_seen_at": newer["last_seen_at"], "id": row["duplicate_of_id"]},
    )
    return True


def reject_duplicate(conn: Connection, suggestion_id: int) -> bool:
    """Mark the suggestion REJECTED: both lines stay, and the pair is never suggested again."""
    cursor = conn.execute(
        """
        UPDATE spending.duplicate_suggestions SET status = 'REJECTED', resolved_at = NOW()
        WHERE id = %s AND status = 'PENDING'
        """,
        (suggestion_id,),
    )
    return cursor.rowcount == 1
