"""Storing what each transaction is (issue #115): the rules' verdict and the user's.

:func:`reclassify` runs the rules of :mod:`bogle.spending.classify` over every
stored line and writes what changed; the sync calls it at the end of every run,
so a match that needs both sides (a bill payment, a transfer between two
accounts) is redone once both are stored. :func:`set_kind` is the user's
choice, which no sync overwrites.
"""

from __future__ import annotations

from datetime import datetime

import psycopg
from psycopg.rows import DictRow

from bogle.domain.errors import ValidationError
from bogle.spending import repository
from bogle.spending.classify import Kind, classify
from bogle.spending.models import TransactionKind

Connection = psycopg.Connection[DictRow]

KIND_NAMES = tuple(kind.value for kind in Kind)


def reclassify(conn: Connection) -> int:
    """Recompute every line's kind; returns how many changed. The caller holds the transaction."""
    verdicts = classify(
        repository.classification_lines(conn),
        repository.bill_payments(conn),
        repository.own_documents(conn),
    )
    return repository.write_classifications(conn, verdicts)


def parse_kind(text: str) -> Kind:
    try:
        return Kind(text.strip().lower())
    except ValueError:
        raise ValidationError(f"Tipo '{text}' inválido. Use um destes: {', '.join(KIND_NAMES)}.") from None


def set_kind(conn: Connection, transaction_id: int, kind: Kind | None, *, now: datetime) -> TransactionKind:
    """Mark the line as ``kind`` (``None`` hands it back to the rules) and return it as it now stands."""
    with conn.transaction():
        if not repository.set_manual_kind(conn, transaction_id, kind.value if kind is not None else None, now=now):
            raise ValidationError(f"Transação {transaction_id} não encontrada.")
        after = repository.transaction_kind(conn, transaction_id)
    assert after is not None  # acabou de ser atualizada, na mesma transacao
    return after
