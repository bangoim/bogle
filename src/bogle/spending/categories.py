"""Category groups and the user's category of a transaction (issue #116).

Pluggy's 130 categories are read in groups (``spending.category_groups``),
each ``fixo`` or ``variavel``. A category's group is its own mapping, else its
nearest ancestor's, else the reserve group (``Outros``), and one that got there
without any mapping is *unmapped*: :func:`unmapped_categories` lists them for a
warning until the user maps them. Migration 012 writes the default.

The user's category of a line (:func:`set_category`) wins over a rule's
(:mod:`bogle.spending.rules`) and Pluggy's, and no sync touches it.

Names are compared without regard to case, accents or repeated spaces: a
category by its Portuguese or English name (or Pluggy's id), a group by its own.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime

import psycopg
from psycopg.rows import DictRow

from bogle.domain.errors import ValidationError
from bogle.spending import repository
from bogle.spending.classify import normalized
from bogle.spending.models import Category, CategoryGroup, GroupNature, GroupSummary, TransactionCategory

Connection = psycopg.Connection[DictRow]

NATURE_NAMES = tuple(nature.value for nature in GroupNature)


def parse_nature(text: str) -> GroupNature:
    try:
        return GroupNature(normalized(text))
    except ValueError:
        raise ValidationError(f"Natureza '{text}' inválida. Use {' ou '.join(NATURE_NAMES)}.") from None


# ------------------------------------------------------------ categorias


def find_category(conn: Connection, text: str) -> Category:
    """A stored category by Pluggy's id or by name (Portuguese or English)."""
    wanted = text.strip()
    categories = repository.stored_categories(conn)
    if wanted.isdigit():
        found = [category for category in categories if category.id == wanted]
    else:
        key = normalized(wanted)
        found = [
            category
            for category in categories
            if key and key in (normalized(category.name), normalized(category.description))
        ]
    if not found:
        raise ValidationError(
            f"Categoria '{text}' não encontrada. Veja as categorias em 'bogle spending groups --all'."
        )
    if len(found) > 1:
        options = ", ".join(f"{category.id} ({category.name})" for category in found)
        raise ValidationError(f"Categoria '{text}' é ambígua: {options}. Use o id.")
    return found[0]


def set_category(
    conn: Connection, transaction_id: int, category: Category | None, *, now: datetime
) -> TransactionCategory:
    """Give the line ``category`` (``None`` hands it back to the rule or Pluggy); returns it as it now stands."""
    with conn.transaction():
        category_id = category.id if category is not None else None
        if not repository.set_manual_category(conn, transaction_id, category_id, now=now):
            raise ValidationError(f"Transação {transaction_id} não encontrada.")
        after = repository.transaction_category(conn, transaction_id)
    assert after is not None  # acabou de ser atualizada, na mesma transacao
    return after


# ----------------------------------------------------------------- grupos


def _clean_name(name: str) -> str:
    cleaned = " ".join(name.split())
    if not cleaned:
        raise ValidationError("O nome do grupo está vazio.")
    return cleaned


def find_group(conn: Connection, name: str) -> CategoryGroup:
    key = normalized(name)
    found = [group for group in repository.category_groups(conn) if normalized(group.name) == key]
    if not found:
        raise ValidationError(f"Grupo '{name}' não encontrado.")
    return found[0]


def _check_free(conn: Connection, name: str, *, besides: int | None = None) -> None:
    key = normalized(name)
    for group in repository.category_groups(conn):
        if group.id != besides and normalized(group.name) == key:
            raise ValidationError(f"Já existe o grupo '{group.name}'.")


def create_group(conn: Connection, name: str, nature: GroupNature) -> CategoryGroup:
    cleaned = _clean_name(name)
    with conn.transaction():
        _check_free(conn, cleaned)
        return repository.insert_group(conn, cleaned, nature.value)


def rename_group(conn: Connection, name: str, new_name: str) -> tuple[CategoryGroup, str]:
    """Returns the group as it was and its new name."""
    cleaned = _clean_name(new_name)
    with conn.transaction():
        group = find_group(conn, name)
        _check_free(conn, cleaned, besides=group.id)
        repository.update_group(conn, group.id, name=cleaned)
    return group, cleaned


def set_nature(conn: Connection, name: str, nature: GroupNature) -> CategoryGroup:
    with conn.transaction():
        group = find_group(conn, name)
        repository.update_group(conn, group.id, nature=nature.value)
    return group


def map_category(conn: Connection, category_text: str, group_name: str) -> tuple[Category, CategoryGroup]:
    """Put the category (and its children without a mapping of their own) in the group."""
    with conn.transaction():
        category = find_category(conn, category_text)
        group = find_group(conn, group_name)
        repository.map_category(conn, category.id, group.id)
    return category, group


def remove_group(conn: Connection, name: str, destination: str) -> tuple[CategoryGroup, CategoryGroup, int]:
    """Remove the group, its categories moved to ``destination``; returns both and how many moved."""
    with conn.transaction():
        group = find_group(conn, name)
        if group.fallback:
            raise ValidationError(
                f"O grupo '{group.name}' é o de reserva e não pode ser removido (pode ser renomeado)."
            )
        target = find_group(conn, destination)
        if target.id == group.id:
            raise ValidationError("O destino precisa ser outro grupo.")
        moved = repository.delete_group(conn, group.id, moving_to=target.id)
    return group, target, moved


def group_summaries(conn: Connection) -> list[GroupSummary]:
    """Every group with the categories mapped to it and how many end up in it."""
    groups = repository.category_groups(conn)
    names = {category.id: category.name for category in repository.stored_categories(conn)}
    mapped: dict[int, list[str]] = {}
    for category_id, group_id in sorted(repository.category_mappings(conn).items()):
        mapped.setdefault(group_id, []).append(names.get(category_id, category_id))
    sizes = Counter(group_id for _, group_id, _ in repository.category_resolution(conn))
    return [GroupSummary(group, tuple(mapped.get(group.id, ())), sizes[group.id]) for group in groups]


def category_groups(conn: Connection) -> list[tuple[Category, CategoryGroup, bool]]:
    """Every stored category, its group, and whether a mapping (its own or an ancestor's) decided it."""
    groups = {group.id: group for group in repository.category_groups(conn)}
    resolved = {
        category_id: (group_id, origin) for category_id, group_id, origin in repository.category_resolution(conn)
    }
    rows: list[tuple[Category, CategoryGroup, bool]] = []
    for category in repository.stored_categories(conn):
        group_id, origin = resolved[category.id]
        rows.append((category, groups[group_id], origin is not None))
    return rows


def unmapped_categories(conn: Connection) -> list[Category]:
    """Stored categories in the reserve group only because nothing maps them."""
    return [category for category, _, mapped in category_groups(conn) if not mapped]
