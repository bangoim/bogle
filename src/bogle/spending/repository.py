"""SQL over the ``spending`` schema (issue #114).

Plain functions over a connection, like the rest of the code that reads and
writes this schema. Atomicity is the caller's: the sync wraps each account in
one ``conn.transaction()``.

Contract for #115 onward: a transaction with a PENDING duplicate suggestion in
``transaction_id`` stays out of every analysis (:func:`suspected_duplicate_ids`).
Analyses read the view ``spending.expenses`` (migrations 011 and 012), which
applies that, keeps only the kinds that count as spending (#115), and carries the
category that counts and its group (#116).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from bogle.data.pluggy import PluggyAccount, PluggyBill, PluggyCategory, PluggyItem, PluggyTransaction, dumps
from bogle.spending.classify import BillPayment, Line, Verdict, counterpart_document, digits, parse_bill_payments
from bogle.spending.models import (
    AccountStatus,
    Category,
    CategoryGroup,
    DescriptionMatch,
    ExpenseLine,
    IncomeLine,
    ItemState,
    RuleApplication,
    RuleConditions,
    RuleLine,
    StoredAccount,
    StoredRule,
    StoredTransaction,
    TransactionCategory,
    TransactionKind,
    account_label,
)

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


def try_lock_for_transaction(conn: Connection) -> bool:
    """The sync's lock until the current transaction ends; ``False`` while a sync holds it."""
    row = conn.execute("SELECT pg_try_advisory_xact_lock(%s) AS locked", (SYNC_LOCK,)).fetchone()
    return bool(row and row["locked"])


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
    every field of the newer one, its Pluggy id included, and the newer one's
    kind and category set by hand when it has none of its own; the newer line goes, and the
    suggestion with it. ``False`` when there is no such PENDING suggestion.
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
    if newer["manual_kind"] is not None:
        # O tipo dado a mao (#115) no lado novo vale quando o antigo nao tem um.
        conn.execute(
            """
            UPDATE spending.transactions SET manual_kind = %s, manual_kind_at = %s
            WHERE id = %s AND manual_kind IS NULL
            """,
            (newer["manual_kind"], newer["manual_kind_at"], row["duplicate_of_id"]),
        )
    if newer["manual_category_id"] is not None:
        # Idem para a categoria dada a mao (#116).
        conn.execute(
            """
            UPDATE spending.transactions SET manual_category_id = %s, manual_category_at = %s
            WHERE id = %s AND manual_category_id IS NULL
            """,
            (newer["manual_category_id"], newer["manual_category_at"], row["duplicate_of_id"]),
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


# ------------------------------------------------------------ marcacao (#115)


def classification_lines(conn: Connection) -> list[Line]:
    """Every stored transaction, as much of it as the rules of :mod:`bogle.spending.classify` read."""
    rows = conn.execute(
        """
        SELECT t.id, t.account_id, a.type AS account_type, t.type, t.amount, t.local_date, t.description,
               t.category_id, t.operation_type, t.payment_data, t.amount_in_account_currency
        FROM spending.transactions t JOIN spending.accounts a ON a.id = t.account_id
        ORDER BY t.id
        """
    ).fetchall()
    return [Line(**row) for row in rows]


def bill_payments(conn: Connection) -> list[BillPayment]:
    """Every payment registered on a stored bill (``payments`` of ``/bills``)."""
    rows = conn.execute("SELECT account_id, due_date, payments FROM spending.bills ORDER BY id").fetchall()
    return [
        payment for row in rows for payment in parse_bill_payments(row["account_id"], row["due_date"], row["payments"])
    ]


def own_documents(conn: Connection) -> set[str]:
    """The user's tax numbers, digits only: the ones of the connected accounts."""
    rows = conn.execute("SELECT DISTINCT tax_number FROM spending.accounts WHERE tax_number IS NOT NULL").fetchall()
    return {doc for doc in (digits(row["tax_number"]) for row in rows) if doc}


def write_classifications(conn: Connection, verdicts: Mapping[int, Verdict]) -> int:
    """Store the rules' verdicts; only the lines whose verdict changed are written.

    ``manual_kind`` is never touched. Returns how many lines changed.
    """
    rows = conn.execute(
        "SELECT id, auto_kind, auto_reason, paired_transaction_id FROM spending.transactions"
    ).fetchall()
    current = {row["id"]: (row["auto_kind"], row["auto_reason"], row["paired_transaction_id"]) for row in rows}
    changed = [
        (str(verdict.kind), verdict.reason, verdict.paired_id, transaction_id)
        for transaction_id, verdict in verdicts.items()
        if transaction_id in current
        and current[transaction_id] != (str(verdict.kind), verdict.reason, verdict.paired_id)
    ]
    if changed:
        with conn.cursor() as cur:
            cur.executemany(
                """
                UPDATE spending.transactions
                SET auto_kind = %s, auto_reason = %s, paired_transaction_id = %s
                WHERE id = %s
                """,
                changed,
            )
    return len(changed)


def transaction_kind(conn: Connection, transaction_id: int) -> TransactionKind | None:
    row = conn.execute(
        """
        SELECT t.id, a.name, a.marketing_name, t.local_date, t.description, t.amount,
               t.kind, t.auto_kind, t.auto_reason, t.manual_kind
        FROM spending.transactions t JOIN spending.accounts a ON a.id = t.account_id
        WHERE t.id = %s
        """,
        (transaction_id,),
    ).fetchone()
    if row is None:
        return None
    return TransactionKind(
        id=row["id"],
        account=account_label(row["name"], row["marketing_name"]),
        local_date=row["local_date"],
        description=row["description"],
        amount=row["amount"],
        kind=row["kind"],
        auto_kind=row["auto_kind"],
        auto_reason=row["auto_reason"],
        manual_kind=row["manual_kind"],
    )


def set_manual_kind(conn: Connection, transaction_id: int, kind: str | None, *, now: datetime) -> bool:
    """The user's kind for the line (``None`` gives it back to the rules); ``False`` when there is no such line."""
    cursor = conn.execute(
        "UPDATE spending.transactions SET manual_kind = %s, manual_kind_at = %s WHERE id = %s",
        (kind, now if kind is not None else None, transaction_id),
    )
    return cursor.rowcount == 1


# ------------------------------------------------------- categorias (#116)


def stored_categories(conn: Connection) -> list[Category]:
    """Pluggy's categories as stored, named in Portuguese when Pluggy sent it."""
    rows = conn.execute(
        """
        SELECT id, COALESCE(NULLIF(description_translated, ''), description) AS name, description, parent_id
        FROM spending.categories ORDER BY id
        """
    ).fetchall()
    return [Category(**row) for row in rows]


def _group(row: DictRow) -> CategoryGroup:
    return CategoryGroup(id=row["id"], name=row["name"], nature=row["nature"], fallback=row["is_fallback"])


def category_groups(conn: Connection) -> list[CategoryGroup]:
    """Every group by name, the reserve group last."""
    rows = conn.execute("SELECT * FROM spending.category_groups ORDER BY is_fallback, lower(name), id").fetchall()
    return [_group(row) for row in rows]


def insert_group(conn: Connection, name: str, nature: str) -> CategoryGroup:
    row = conn.execute(
        "INSERT INTO spending.category_groups (name, nature) VALUES (%s, %s) RETURNING *", (name, nature)
    ).fetchone()
    assert row is not None  # INSERT ... RETURNING sempre devolve a linha
    return _group(row)


def update_group(conn: Connection, group_id: int, *, name: str | None = None, nature: str | None = None) -> None:
    conn.execute(
        """
        UPDATE spending.category_groups
        SET name = COALESCE(%s, name), nature = COALESCE(%s, nature)
        WHERE id = %s
        """,
        (name, nature, group_id),
    )


def delete_group(conn: Connection, group_id: int, *, moving_to: int) -> int:
    """Remove the group, its mappings moved to ``moving_to``; returns how many moved."""
    moved = conn.execute(
        "UPDATE spending.category_group_map SET group_id = %s WHERE group_id = %s", (moving_to, group_id)
    ).rowcount
    conn.execute("DELETE FROM spending.category_groups WHERE id = %s AND NOT is_fallback", (group_id,))
    return moved


def map_category(conn: Connection, category_id: str, group_id: int) -> None:
    conn.execute(
        """
        INSERT INTO spending.category_group_map (category_id, group_id) VALUES (%s, %s)
        ON CONFLICT (category_id) DO UPDATE SET group_id = EXCLUDED.group_id
        """,
        (category_id, group_id),
    )


def category_mappings(conn: Connection) -> dict[str, int]:
    """The explicit mappings: category id -> group id."""
    rows = conn.execute("SELECT category_id, group_id FROM spending.category_group_map").fetchall()
    return {row["category_id"]: row["group_id"] for row in rows}


def category_resolution(conn: Connection) -> list[tuple[str, int, str | None]]:
    """Every known category, its group, and the category whose mapping decided it (``None``: unmapped)."""
    rows = conn.execute(
        "SELECT category_id, group_id, mapped_from FROM spending.category_group_of ORDER BY category_id"
    ).fetchall()
    return [(row["category_id"], row["group_id"], row["mapped_from"]) for row in rows]


def transaction_category(conn: Connection, transaction_id: int) -> TransactionCategory | None:
    row = conn.execute(
        """
        SELECT t.id, a.name, a.marketing_name, t.local_date, t.description, t.amount,
               t.category, t.category_id, t.rule_id, t.rule_category_id, t.manual_category_id,
               t.effective_category_id,
               COALESCE(NULLIF(pc.description_translated, ''), pc.description, t.category) AS pluggy_name,
               COALESCE(NULLIF(rc.description_translated, ''), rc.description) AS rule_name,
               COALESCE(NULLIF(mc.description_translated, ''), mc.description) AS manual_name,
               g.name AS group_name
        FROM spending.transactions t
        JOIN spending.accounts a ON a.id = t.account_id
        LEFT JOIN spending.categories pc ON pc.id = t.category_id
        LEFT JOIN spending.categories rc ON rc.id = t.rule_category_id
        LEFT JOIN spending.categories mc ON mc.id = t.manual_category_id
        LEFT JOIN spending.category_group_of r ON r.category_id = t.effective_category_id
        LEFT JOIN spending.category_groups g ON g.id = COALESCE(
            r.group_id, (SELECT id FROM spending.category_groups WHERE is_fallback)
        )
        WHERE t.id = %s
        """,
        (transaction_id,),
    ).fetchone()
    if row is None:
        return None
    if row["manual_category_id"] is not None:
        category = row["manual_name"] or row["manual_category_id"]
    elif row["rule_category_id"] is not None:
        category = row["rule_name"] or row["rule_category_id"]
    else:
        category = row["pluggy_name"]
    return TransactionCategory(
        id=row["id"],
        account=account_label(row["name"], row["marketing_name"]),
        local_date=row["local_date"],
        description=row["description"],
        amount=row["amount"],
        category=category,
        group=row["group_name"] or "",
        pluggy_category=row["pluggy_name"],
        rule_category=(row["rule_name"] or row["rule_category_id"]) if row["rule_category_id"] else None,
        rule_id=row["rule_id"],
        manual_category=(row["manual_name"] or row["manual_category_id"]) if row["manual_category_id"] else None,
    )


def set_manual_category(conn: Connection, transaction_id: int, category_id: str | None, *, now: datetime) -> bool:
    """The user's category for the line (``None`` gives it back to the rule or Pluggy); ``False``: no such line."""
    cursor = conn.execute(
        "UPDATE spending.transactions SET manual_category_id = %s, manual_category_at = %s WHERE id = %s",
        (category_id, now if category_id is not None else None, transaction_id),
    )
    return cursor.rowcount == 1


# ------------------------------------------------------------ regras (#116)


def rule_lines(conn: Connection, transaction_ids: Sequence[int] | None = None) -> list[RuleLine]:
    """The stored lines (or only ``transaction_ids``) as the category rules read them."""
    query = """
        SELECT id, account_id, type, ABS(COALESCE(amount_in_account_currency, amount)) AS value, description,
               merchant_cnpj, payment_data, manual_category_id IS NOT NULL AS manual, rule_id, rule_category_id
        FROM spending.transactions
    """
    if transaction_ids is None:
        rows = conn.execute(query + " ORDER BY id").fetchall()
    else:
        rows = conn.execute(query + " WHERE id = ANY(%s) ORDER BY id", (list(transaction_ids),)).fetchall()
    return [
        RuleLine(
            id=row["id"],
            account_id=row["account_id"],
            value=row["value"],
            description=row["description"],
            documents=tuple(
                dict.fromkeys(
                    doc
                    for doc in (digits(row["merchant_cnpj"]), counterpart_document(row["type"], row["payment_data"]))
                    if doc
                )
            ),
            manual=row["manual"],
            rule_id=row["rule_id"],
            rule_category_id=row["rule_category_id"],
        )
        for row in rows
    ]


def insert_rule(conn: Connection, category_id: str, conditions: RuleConditions, *, now: datetime) -> int:
    row = conn.execute(
        """
        INSERT INTO spending.category_rules (
            category_id, document, description, description_match, min_value, max_value, account_id, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (
            category_id,
            conditions.document,
            conditions.description,
            conditions.match.value if conditions.match is not None else None,
            conditions.min_value,
            conditions.max_value,
            conditions.account_id,
            now,
        ),
    ).fetchone()
    assert row is not None  # INSERT ... RETURNING sempre devolve a linha
    return row["id"]


def stored_rules(conn: Connection, rule_id: int | None = None) -> list[StoredRule]:
    """Every rule (or just ``rule_id``), newest first: the order they win in."""
    rows = conn.execute(
        """
        SELECT r.*, COALESCE(NULLIF(c.description_translated, ''), c.description) AS category_name,
               a.name AS account_name, a.marketing_name,
               (SELECT count(*) FROM spending.transactions t WHERE t.rule_id = r.id) AS transactions
        FROM spending.category_rules r
        JOIN spending.categories c ON c.id = r.category_id
        LEFT JOIN spending.accounts a ON a.id = r.account_id
        WHERE %(id)s::bigint IS NULL OR r.id = %(id)s::bigint
        ORDER BY r.id DESC
        """,
        {"id": rule_id},
    ).fetchall()
    return [
        StoredRule(
            id=row["id"],
            category_id=row["category_id"],
            category=row["category_name"],
            conditions=RuleConditions(
                document=row["document"],
                description=row["description"],
                match=DescriptionMatch(row["description_match"]) if row["description_match"] else None,
                min_value=row["min_value"],
                max_value=row["max_value"],
                account_id=row["account_id"],
            ),
            account=account_label(row["account_name"], row["marketing_name"]) if row["account_name"] else None,
            created_at=row["created_at"],
            transactions=row["transactions"],
        )
        for row in rows
    ]


def delete_rule(conn: Connection, rule_id: int) -> None:
    """Remove the rule and its applications; no line may still point at it."""
    conn.execute("DELETE FROM spending.category_rules WHERE id = %s", (rule_id,))


def rule_transaction_ids(conn: Connection, rule_id: int) -> list[int]:
    rows = conn.execute("SELECT id FROM spending.transactions WHERE rule_id = %s ORDER BY id", (rule_id,)).fetchall()
    return [row["id"] for row in rows]


def set_rule_categories(conn: Connection, assignments: Sequence[tuple[int, int | None, str | None]]) -> None:
    """``(transaction, rule, category)``: the rule's category of each line (``None, None`` clears it)."""
    if not assignments:
        return
    with conn.cursor() as cur:
        cur.executemany(
            "UPDATE spending.transactions SET rule_id = %s, rule_category_id = %s WHERE id = %s",
            [(rule_id, category_id, transaction_id) for transaction_id, rule_id, category_id in assignments],
        )


def insert_application(
    conn: Connection, rule_id: int, changes: Sequence[tuple[int, int | None, str | None]], *, now: datetime
) -> int:
    """Record an application of the rule: each changed line with the ``(rule, category)`` it had."""
    row = conn.execute(
        "INSERT INTO spending.rule_applications (rule_id, applied_at) VALUES (%s, %s) RETURNING id", (rule_id, now)
    ).fetchone()
    assert row is not None  # INSERT ... RETURNING sempre devolve a linha
    application_id = row["id"]
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO spending.rule_application_changes
                (application_id, transaction_id, previous_rule_id, previous_category_id)
            VALUES (%s, %s, %s, %s)
            """,
            [(application_id, *change) for change in changes],
        )
    return application_id


def last_application(conn: Connection) -> RuleApplication | None:
    row = conn.execute(
        """
        SELECT p.id, p.rule_id, p.applied_at,
               (SELECT count(*) FROM spending.rule_application_changes c WHERE c.application_id = p.id) AS changes
        FROM spending.rule_applications p ORDER BY p.id DESC LIMIT 1
        """
    ).fetchone()
    return RuleApplication(**row) if row is not None else None


def application_changes(conn: Connection, application_id: int) -> list[tuple[int, int | None, str | None]]:
    """``(transaction, previous rule, previous category)`` of each line the application changed."""
    rows = conn.execute(
        """
        SELECT transaction_id, previous_rule_id, previous_category_id
        FROM spending.rule_application_changes WHERE application_id = %s ORDER BY transaction_id
        """,
        (application_id,),
    ).fetchall()
    return [(row["transaction_id"], row["previous_rule_id"], row["previous_category_id"]) for row in rows]


# ------------------------------------------------------------ analises (#117)


def expense_lines(conn: Connection, account_ids: Sequence[int], since: date, until: date) -> list[ExpenseLine]:
    """The lines of ``spending.expenses`` of the accounts from ``since`` to ``until``, both included."""
    rows = conn.execute(
        """
        SELECT e.account_id, e.local_date, e.value, e.category_id, e.category,
               g.id AS group_id, g.name AS group_name, g.nature, g.is_fallback,
               t.merchant_business_name, t.merchant_name, t.description
        FROM spending.expenses e
        JOIN spending.transactions t ON t.id = e.transaction_id
        JOIN spending.category_groups g ON g.id = e.group_id
        WHERE e.account_id = ANY(%s) AND e.local_date BETWEEN %s AND %s
        ORDER BY e.local_date, e.transaction_id
        """,
        (list(account_ids), since, until),
    ).fetchall()
    return [
        ExpenseLine(
            account_id=row["account_id"],
            local_date=row["local_date"],
            value=row["value"],
            category_id=row["category_id"],
            category=row["category"],
            group=CategoryGroup(
                id=row["group_id"], name=row["group_name"], nature=row["nature"], fallback=row["is_fallback"]
            ),
            merchant=_merchant(row),
        )
        for row in rows
    ]


def _merchant(row: DictRow) -> str:
    for column in ("merchant_business_name", "merchant_name"):
        name = (row[column] or "").strip()
        if name:
            return name
    return row["description"]


def income_lines(conn: Connection, account_ids: Sequence[int], since: date, until: date) -> list[IncomeLine]:
    """The lines of kind ``receita`` of the accounts, as ``spending.expenses`` filters them (#115)."""
    rows = conn.execute(
        """
        SELECT t.account_id, t.local_date,
               CASE
                   WHEN a.type = 'CREDIT' THEN -COALESCE(t.amount_in_account_currency, t.amount)
                   ELSE COALESCE(t.amount_in_account_currency, t.amount)
               END AS value
        FROM spending.transactions t
        JOIN spending.accounts a ON a.id = t.account_id
        WHERE t.kind = 'receita'
          AND t.account_id = ANY(%s) AND t.local_date BETWEEN %s AND %s
          AND NOT EXISTS (
              SELECT 1 FROM spending.duplicate_suggestions d
              WHERE d.transaction_id = t.id AND d.status = 'PENDING'
          )
        ORDER BY t.local_date, t.id
        """,
        (list(account_ids), since, until),
    ).fetchall()
    return [IncomeLine(**row) for row in rows]
