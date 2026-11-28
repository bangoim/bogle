"""Category rules (issue #116): the user's "lines like this one are that category".

A rule has a target category and conditions, all of which must hold (AND):
the other party's document (the merchant's CNPJ, or the CPF/CNPJ of the payer
or receiver in ``paymentData``), the description (``contem``, ``comeca`` or
``igual``, without regard to case, accents or repeated spaces; ``igual`` is
the whole description), a range of the absolute value in reais (both ends
included) and the account. A document or a description is required.

**Precedence.** The user's own category of a line > a rule's > Pluggy's.
Between rules, the newest that matches wins.

**When a rule applies.** The sync runs the rules over the lines it inserts
(:func:`apply_to_new`), never over the stored ones: a line it updates keeps the
rule it had. Creating a rule applies it to every stored line it matches, at
once (:func:`create_rule`); :func:`preview` counts exactly those lines, with the
same :func:`matches`. That application is recorded, each changed line with the
rule and category it had, so :func:`undo_last` puts them back and drops the
rule. Removing a rule (:func:`remove_rule`) gives its lines to the newest other
rule that matches them, else back to Pluggy's category.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

import psycopg
from psycopg.rows import DictRow

import bogle.format as fmt
from bogle.domain.errors import SyncInProgressError, ValidationError
from bogle.spending import repository
from bogle.spending.classify import normalized
from bogle.spending.models import Category, DescriptionMatch, RuleConditions, RuleLine, StoredRule

Connection = psycopg.Connection[DictRow]

CPF_DIGITS = 11
CNPJ_DIGITS = 14


class RuleBasis(StrEnum):
    """What ``categorize --rule`` builds the rule on; the values are what is typed."""

    DOCUMENT = "documento"
    DESCRIPTION = "descricao"


@dataclass(frozen=True, slots=True)
class RuleDraft:
    """A rule not stored yet: the category and the conditions."""

    category: Category
    conditions: RuleConditions
    account: str | None = None
    """The account's name, when the rule is limited to one."""


@dataclass(frozen=True, slots=True)
class RulePreview:
    """What creating the rule would change: the stored lines it matches."""

    draft: RuleDraft
    lines: tuple[RuleLine, ...]

    @property
    def count(self) -> int:
        return len(self.lines)

    @property
    def manual(self) -> int:
        """Of those, the lines with a category set by hand, which keeps counting."""
        return sum(1 for line in self.lines if line.manual)


@dataclass(frozen=True, slots=True)
class Undone:
    """What :func:`undo_last` did: the rule it dropped and how many lines went back."""

    rule_id: int
    restored: int


# ------------------------------------------------------------- casamento


def matches(conditions: RuleConditions, line: RuleLine) -> bool:
    """Whether the line meets every condition given."""
    if conditions.document is not None and conditions.document not in line.documents:
        return False
    if conditions.description is not None:
        text = normalized(conditions.description)
        description = normalized(line.description)
        if conditions.match is DescriptionMatch.EQUALS:
            if description != text:
                return False
        elif conditions.match is DescriptionMatch.STARTS:
            if not description.startswith(text):
                return False
        elif text not in description:
            return False
    if conditions.min_value is not None and line.value < conditions.min_value:
        return False
    if conditions.max_value is not None and line.value > conditions.max_value:
        return False
    return conditions.account_id is None or line.account_id == conditions.account_id


def check(conditions: RuleConditions) -> None:
    """Refuse a rule that cannot be stored or would take every line."""
    if conditions.document is None and conditions.description is None:
        raise ValidationError("A regra precisa de um documento ou de uma descrição.")
    if conditions.description is not None and not normalized(conditions.description):
        raise ValidationError("A descrição da regra está vazia.")
    if conditions.description is not None and conditions.match is None:
        raise ValidationError("Informe como casar a descrição: contem, comeca ou igual.")
    low, high = conditions.min_value, conditions.max_value
    if low is not None and high is not None and low > high:
        raise ValidationError(f"Valor mínimo ({fmt.money(low)}) maior que o máximo ({fmt.money(high)}).")


def _winner(rules: Sequence[StoredRule], line: RuleLine) -> StoredRule | None:
    """The newest rule that matches; ``rules`` come newest first."""
    return next((rule for rule in rules if matches(rule.conditions, line)), None)


# -------------------------------------------------------------- operacoes


def _hold_sync(conn: Connection) -> None:
    """Keep a sync out while rules change: it would apply the old set to the lines it inserts."""
    if not repository.try_lock_for_transaction(conn):
        raise SyncInProgressError()


def draft_for(
    conn: Connection,
    transaction_id: int,
    category: Category,
    *,
    basis: RuleBasis | None = None,
    text: str | None = None,
    match: DescriptionMatch | None = None,
    min_value: Decimal | None = None,
    max_value: Decimal | None = None,
    same_account: bool = False,
) -> RuleDraft:
    """A rule built from a stored line.

    By default on the line's document (the merchant's CNPJ, else the other
    party's); without one, or when a text or a match is given, on the
    description: ``text`` (default: the whole description) compared by
    ``match`` (default: ``contem`` for a text, ``igual`` for the whole
    description). ``basis`` forces one; a document with a text takes both.
    """
    found = repository.rule_lines(conn, [transaction_id])
    if not found:
        raise ValidationError(f"Transação {transaction_id} não encontrada.")
    line = found[0]
    by_description = basis is RuleBasis.DESCRIPTION or text is not None or match is not None
    by_document = basis is RuleBasis.DOCUMENT or (basis is None and not by_description)
    if by_document and not line.documents:
        if basis is RuleBasis.DOCUMENT:
            raise ValidationError(f"Transação {transaction_id} sem documento (CNPJ ou CPF do favorecido).")
        by_document, by_description = False, True
    description: str | None = None
    if by_description:
        description = text.strip() if text is not None else line.description.strip()
        match = match or (DescriptionMatch.CONTAINS if text is not None else DescriptionMatch.EQUALS)
    conditions = RuleConditions(
        document=line.documents[0] if by_document else None,
        description=description,
        match=match if by_description else None,
        min_value=min_value,
        max_value=max_value,
        account_id=line.account_id if same_account else None,
    )
    check(conditions)
    account = None
    if same_account:
        account = next((a.label for a in repository.accounts(conn) if a.id == line.account_id), None)
    return RuleDraft(category, conditions, account)


def preview(conn: Connection, draft: RuleDraft) -> RulePreview:
    """The stored lines the rule would take: exactly what :func:`create_rule` changes."""
    return RulePreview(draft, tuple(_matching(conn, draft.conditions)))


def _matching(conn: Connection, conditions: RuleConditions) -> list[RuleLine]:
    return [line for line in repository.rule_lines(conn) if matches(conditions, line)]


def create_rule(conn: Connection, draft: RuleDraft, *, now: datetime) -> tuple[int, int]:
    """Store the rule and apply it to the stored lines it matches; returns its id and how many changed."""
    check(draft.conditions)
    with conn.transaction():
        _hold_sync(conn)
        rule_id = repository.insert_rule(conn, draft.category.id, draft.conditions, now=now)
        lines = _matching(conn, draft.conditions)
        repository.set_rule_categories(conn, [(line.id, rule_id, draft.category.id) for line in lines])
        repository.insert_application(
            conn, rule_id, [(line.id, line.rule_id, line.rule_category_id) for line in lines], now=now
        )
    return rule_id, len(lines)


def apply_to_new(conn: Connection, transaction_ids: Sequence[int]) -> int:
    """The sync's part: the newest matching rule of each new line. The caller holds the transaction."""
    if not transaction_ids:
        return 0
    rules = repository.stored_rules(conn)
    if not rules:
        return 0
    assignments: list[tuple[int, int | None, str | None]] = []
    for line in repository.rule_lines(conn, transaction_ids):
        rule = _winner(rules, line)
        if rule is not None:
            assignments.append((line.id, rule.id, rule.category_id))
    repository.set_rule_categories(conn, assignments)
    return len(assignments)


def rules(conn: Connection) -> list[StoredRule]:
    return repository.stored_rules(conn)


def _stored(conn: Connection, rule_id: int) -> StoredRule:
    found = repository.stored_rules(conn, rule_id)
    if not found:
        raise ValidationError(f"Regra {rule_id} não encontrada.")
    return found[0]


def _drop(conn: Connection, rule_id: int) -> int:
    """Delete the rule; its lines go to the newest other rule that matches, else to Pluggy's category."""
    ids = repository.rule_transaction_ids(conn, rule_id)
    repository.set_rule_categories(conn, [(transaction_id, None, None) for transaction_id in ids])
    repository.delete_rule(conn, rule_id)
    others = repository.stored_rules(conn)
    assignments: list[tuple[int, int | None, str | None]] = []
    for line in repository.rule_lines(conn, ids) if others else []:
        rule = _winner(others, line)
        if rule is not None:
            assignments.append((line.id, rule.id, rule.category_id))
    repository.set_rule_categories(conn, assignments)
    return len(ids)


def remove_rule(conn: Connection, rule_id: int) -> tuple[StoredRule, int]:
    """Remove the rule; returns it and how many lines it categorized."""
    with conn.transaction():
        _hold_sync(conn)
        rule = _stored(conn, rule_id)
        return rule, _drop(conn, rule_id)


def undo_last(conn: Connection) -> Undone:
    """Undo the last application: its lines get back what they had, and its rule goes.

    A line changed since (by a newer application, or one whose earlier rule was
    removed meanwhile) is not put back blindly: it goes the way of
    :func:`remove_rule`.
    """
    with conn.transaction():
        _hold_sync(conn)
        application = repository.last_application(conn)
        if application is None:
            raise ValidationError("Nenhuma aplicação de regra para desfazer.")
        alive = {rule.id for rule in repository.stored_rules(conn)} - {application.rule_id}
        changes = repository.application_changes(conn, application.id)
        current = {line.id: line for line in repository.rule_lines(conn, [change[0] for change in changes])}
        restore = [
            (transaction_id, rule_id, category_id)
            for transaction_id, rule_id, category_id in changes
            if transaction_id in current
            and current[transaction_id].rule_id == application.rule_id
            and (rule_id is None or rule_id in alive)
        ]
        repository.set_rule_categories(conn, restore)
        _drop(conn, application.rule_id)
    return Undone(application.rule_id, len(restore))


# ---------------------------------------------------------------- exibicao


def document_label(document: str) -> str:
    """``12345678000190`` -> ``12.345.678/0001-90``; a CPF as ``111.222.333-44``."""
    if len(document) == CNPJ_DIGITS:
        d = document
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    if len(document) == CPF_DIGITS:
        d = document
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    return document


_MATCH_WORDS = {
    DescriptionMatch.CONTAINS: "contém",
    DescriptionMatch.STARTS: "começa com",
    DescriptionMatch.EQUALS: "igual a",
}


def describe(conditions: RuleConditions, account: str | None = None) -> str:
    """``documento 12.345.678/0001-90, descrição contém "uber", valor até 50,00``."""
    parts: list[str] = []
    if conditions.document is not None:
        parts.append(f"documento {document_label(conditions.document)}")
    if conditions.description is not None and conditions.match is not None:
        # A descricao vem da Pluggy: sem caractere de controle no terminal.
        text = "".join(char for char in conditions.description if char.isprintable())
        parts.append(f'descrição {_MATCH_WORDS[conditions.match]} "{text}"')
    low, high = conditions.min_value, conditions.max_value
    if low is not None and high is not None:
        parts.append(f"valor de {fmt.money(low)} a {fmt.money(high)}")
    elif low is not None:
        parts.append(f"valor a partir de {fmt.money(low)}")
    elif high is not None:
        parts.append(f"valor até {fmt.money(high)}")
    if conditions.account_id is not None:
        parts.append(f"conta {account or conditions.account_id}")
    return ", ".join(parts)
