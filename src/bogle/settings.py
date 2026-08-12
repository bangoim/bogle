"""User settings persisted in ``user_settings`` (issue #31).

Key/value rows in JSONB, one row per setting; an absent row means "use the
default". Every supported key is declared in :data:`SETTINGS` with its type,
default and parser — ``set_setting`` rejects unknown keys and malformed values,
so the table only ever holds well-formed entries. Decimals and dates are stored
as JSON strings (never floats) to preserve exactness.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import psycopg
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from bogle.domain.errors import UnknownSettingError, ValidationError

REBALANCE_PERIOD_MONTHS = "rebalance_period_months"
DEFAULT_COMPARE_INDICES = "default_compare_indices"
WEIGHT_DRIFT_THRESHOLD = "weight_drift_threshold"
LAST_REBALANCE_DATE = "last_rebalance_date"
DECIMAL_SEPARATOR = "decimal_separator"

_VALID_PERIODS = (6, 12)


def _parse_period(raw: str) -> int:
    try:
        period = int(raw)
    except ValueError:
        raise ValidationError(f"'{raw}' nao e um inteiro valido.") from None
    if period not in _VALID_PERIODS:
        raise ValidationError(f"Periodo de rebalanceamento deve ser 6 ou 12 meses, recebido {period}.")
    return period


def _parse_indices(raw: str) -> list[str]:
    indices = [part.strip().upper() for part in raw.split(",") if part.strip()]
    if not indices:
        raise ValidationError("Lista de indices vazia. Informe valores separados por virgula (ex: CDI,IBOV).")
    return indices


def _parse_threshold(raw: str) -> Decimal:
    try:
        threshold = Decimal(raw)
    except InvalidOperation:
        raise ValidationError(f"'{raw}' nao e um decimal valido.") from None
    if not (Decimal("0") < threshold < Decimal("1")):
        raise ValidationError(f"Threshold deve estar em (0, 1), recebido {threshold}.")
    return threshold


def _parse_separator(raw: str) -> str:
    separator = raw.strip()
    if separator not in (".", ","):
        raise ValidationError(f"Separador decimal deve ser '.' ou ',', recebido {raw!r}.")
    return separator


def _parse_date(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ValidationError(f"'{raw}' nao e uma data valida (formato YYYY-MM-DD).") from None


@dataclass(frozen=True, slots=True)
class SettingSpec:
    key: str
    type_name: str
    description: str
    default: Any
    parse: Callable[[str], Any]
    # JSON representation <-> typed value (JSONB never stores Decimal/date).
    to_json: Callable[[Any], Any]
    from_json: Callable[[Any], Any]


SETTINGS: dict[str, SettingSpec] = {
    spec.key: spec
    for spec in (
        SettingSpec(
            key=REBALANCE_PERIOD_MONTHS,
            type_name="int",
            description="Ciclo de avaliacao de rebalanceamento em meses (6 ou 12).",
            default=12,
            parse=_parse_period,
            to_json=int,
            from_json=int,
        ),
        SettingSpec(
            key=DEFAULT_COMPARE_INDICES,
            type_name="list[str]",
            description="Indices usados por 'bogle compare' sem --index (separados por virgula).",
            default=["IBOV", "CDI"],
            parse=_parse_indices,
            to_json=list,
            from_json=lambda value: [str(item) for item in value],
        ),
        SettingSpec(
            key=WEIGHT_DRIFT_THRESHOLD,
            type_name="decimal",
            description="Drift (em fracao) a partir do qual um ativo vira BUY.",
            default=Decimal("0.05"),
            parse=_parse_threshold,
            to_json=str,
            from_json=Decimal,
        ),
        SettingSpec(
            key=DECIMAL_SEPARATOR,
            type_name="str",
            description="Separador decimal na exibicao ('.' ou ','); o outro caractere separa o milhar.",
            default=".",
            parse=_parse_separator,
            to_json=str,
            from_json=str,
        ),
        SettingSpec(
            key=LAST_REBALANCE_DATE,
            type_name="date",
            description="Data da ultima avaliacao de rebalanceamento (atualizada por 'bogle suggest').",
            default=None,
            parse=_parse_date,
            to_json=date.isoformat,
            from_json=date.fromisoformat,
        ),
    )
}


@dataclass(frozen=True, slots=True)
class SettingEntry:
    """A setting as shown by ``bogle config list``: current (or default) value
    plus provenance."""

    key: str
    value: Any
    type_name: str
    description: str
    is_default: bool
    updated_at: datetime | None


def _spec(key: str) -> SettingSpec:
    spec = SETTINGS.get(key)
    if spec is None:
        raise UnknownSettingError(key, sorted(SETTINGS))
    return spec


def get_setting(conn: psycopg.Connection[DictRow], key: str) -> Any:
    """The typed value of ``key``, or its default when never set."""
    spec = _spec(key)
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM user_settings WHERE key = %s", (key,))
        row = cur.fetchone()
    return spec.from_json(row["value"]) if row is not None else spec.default


def set_setting(conn: psycopg.Connection[DictRow], key: str, raw: str) -> Any:
    """Parse ``raw`` (CLI string) per the key's type and upsert. Returns the
    typed value."""
    return set_value(conn, key, _spec(key).parse(raw))


def set_value(conn: psycopg.Connection[DictRow], key: str, value: Any) -> Any:
    """Upsert an already-typed value (used by code paths like ``bogle suggest``
    recording ``last_rebalance_date``)."""
    spec = _spec(key)
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_settings (key, value) VALUES (%s, %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
            """,
            (key, Jsonb(spec.to_json(value))),
        )
    return value


def unset_setting(conn: psycopg.Connection[DictRow], key: str) -> None:
    """Remove the row, reverting the key to its default. Unset keys are a no-op."""
    _spec(key)
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM user_settings WHERE key = %s", (key,))


def list_settings(conn: psycopg.Connection[DictRow]) -> list[SettingEntry]:
    """Every supported key with its current value (set or default)."""
    with conn.cursor() as cur:
        cur.execute("SELECT key, value, updated_at FROM user_settings")
        rows = {row["key"]: row for row in cur.fetchall()}
    entries = []
    for key in sorted(SETTINGS):
        spec = SETTINGS[key]
        row = rows.get(key)
        entries.append(
            SettingEntry(
                key=key,
                value=spec.from_json(row["value"]) if row is not None else spec.default,
                type_name=spec.type_name,
                description=spec.description,
                is_default=row is None,
                updated_at=row["updated_at"] if row is not None else None,
            )
        )
    return entries


def format_value(value: Any) -> str:
    """Human/scriptable rendering: lists comma-joined, dates ISO, None explicit."""
    if value is None:
        return "(nao definido)"
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    if isinstance(value, date):
        return value.isoformat()
    return str(value)
