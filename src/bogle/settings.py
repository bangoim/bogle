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
HIDE_VALUES = "hide_values"
THEME = "theme"
BIRTH_YEAR = "birth_year"
RETIREMENT_AGE = "retirement_age"
GLIDE_PATH_APPLIED_YEAR = "glide_path_applied_year"
LAST_ANNUAL_REBALANCE_YEAR = "last_annual_rebalance_year"
SPENDING_LAST_SYNC_AT = "spending_last_sync_at"

DEFAULT_THEME = "textual-dark"

DEFAULT_DECIMAL_SEPARATOR = ","
"""O padrao brasileiro, ``1.234,56``. Quem prefere ``1,234.56`` troca na Config."""

_VALID_PERIODS = (6, 12)


def _parse_period(raw: str) -> int:
    try:
        period = int(raw)
    except ValueError:
        raise ValidationError(f"'{raw}' não é um inteiro válido.") from None
    if period not in _VALID_PERIODS:
        raise ValidationError(f"Período de rebalanceamento deve ser 6 ou 12 meses, recebido {period}.")
    return period


def _parse_indices(raw: str) -> list[str]:
    indices = [part.strip().upper() for part in raw.split(",") if part.strip()]
    if not indices:
        raise ValidationError("Lista de índices vazia. Informe valores separados por vírgula (ex: CDI,IBOV).")
    return indices


def _parse_threshold(raw: str) -> Decimal:
    try:
        threshold = Decimal(raw)
    except InvalidOperation:
        raise ValidationError(f"'{raw}' não é um decimal válido.") from None
    if not (Decimal("0") < threshold < Decimal("1")):
        raise ValidationError(f"Threshold deve estar em (0, 1), recebido {threshold}.")
    return threshold


def _parse_separator(raw: str) -> str:
    separator = raw.strip()
    if separator not in (".", ","):
        raise ValidationError(f"Separador decimal deve ser '.' ou ',', recebido {raw!r}.")
    return separator


def _parse_theme(raw: str) -> str:
    # Import tardio: um comando direto nao paga pelo textual so porque a chave
    # existe — so quem realmente troca de tema carrega a lista.
    from textual.theme import BUILTIN_THEMES

    theme = raw.strip()
    if theme not in BUILTIN_THEMES:
        raise ValidationError(f"Tema '{theme}' não existe. Opções: {', '.join(sorted(BUILTIN_THEMES))}.")
    return theme


_TRUE = ("true", "1", "sim", "yes", "on")
_FALSE = ("false", "0", "não", "nao", "no", "off")


def _parse_bool(raw: str) -> bool:
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValidationError(f"'{raw}' não é um booleano. Use {_TRUE[0]} ou {_FALSE[0]}.")


def _parse_date(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ValidationError(f"'{raw}' não é uma data válida (formato YYYY-MM-DD).") from None


def _parse_datetime(raw: str) -> datetime:
    # Com fuso, sempre: um instante sem fuso seria lido no fuso de quem le, e a
    # marca do sync automatico compara intervalos de horas.
    try:
        moment = datetime.fromisoformat(raw.strip())
    except ValueError:
        raise ValidationError(f"'{raw}' não é uma data e hora válida (ISO 8601 com fuso).") from None
    if moment.tzinfo is None:
        raise ValidationError(f"'{raw}' não tem fuso horário (ex: 2026-11-27T08:00:00-03:00).")
    return moment


_FIRST_BIRTH_YEAR = 1900


def _parse_year(raw: str) -> int:
    try:
        return int(raw.strip())
    except ValueError:
        raise ValidationError(f"'{raw}' não é um ano válido.") from None


def _parse_birth_year(raw: str) -> int:
    # Plausivel: nao antes de 1900 e nao no futuro. A idade da trajetoria e
    # `ano corrente - ano de nascimento`, e um ano digitado errado (26, 20000)
    # moveria todos os alvos sem que ninguem notasse.
    year = _parse_year(raw)
    last = date.today().year
    if not (_FIRST_BIRTH_YEAR <= year <= last):
        raise ValidationError(f"Ano de nascimento deve estar entre {_FIRST_BIRTH_YEAR} e {last}, recebido {year}.")
    return year


_MAX_RETIREMENT_AGE = 120
"""A mesma idade maxima de um ponto da trajetoria (``glide_path.MAX_AGE``)."""


def _parse_retirement_age(raw: str) -> int:
    try:
        age = int(raw.strip())
    except ValueError:
        raise ValidationError(f"'{raw}' não é uma idade válida.") from None
    if not (1 <= age <= _MAX_RETIREMENT_AGE):
        raise ValidationError(f"Idade de aposentadoria deve estar entre 1 e {_MAX_RETIREMENT_AGE}, recebido {age}.")
    return age


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
            description="Ciclo de avaliação de rebalanceamento em meses (6 ou 12).",
            default=12,
            parse=_parse_period,
            to_json=int,
            from_json=int,
        ),
        SettingSpec(
            key=DEFAULT_COMPARE_INDICES,
            type_name="list[str]",
            description="Índices usados por 'bogle compare' sem --index (separados por vírgula).",
            default=["IBOV", "CDI"],
            parse=_parse_indices,
            to_json=list,
            from_json=lambda value: [str(item) for item in value],
        ),
        SettingSpec(
            key=WEIGHT_DRIFT_THRESHOLD,
            type_name="decimal",
            description="Drift (em fração) a partir do qual um ativo vira BUY; na aposentadoria, a tolerância da venda anual.",
            default=Decimal("0.05"),
            parse=_parse_threshold,
            to_json=str,
            from_json=Decimal,
        ),
        SettingSpec(
            key=DECIMAL_SEPARATOR,
            type_name="str",
            description="Separador decimal na exibição ('.' ou ','); o outro caractere separa o milhar.",
            default=DEFAULT_DECIMAL_SEPARATOR,
            parse=_parse_separator,
            to_json=str,
            from_json=str,
        ),
        SettingSpec(
            key=HIDE_VALUES,
            type_name="bool",
            description="Abrir a interface interativa com os valores ocultos (a tecla 'h' alterna).",
            default=False,
            parse=_parse_bool,
            to_json=bool,
            from_json=bool,
        ),
        SettingSpec(
            key=THEME,
            type_name="str",
            description="Tema da interface interativa (a paleta de comandos também grava aqui).",
            default=DEFAULT_THEME,
            parse=_parse_theme,
            to_json=str,
            from_json=str,
        ),
        SettingSpec(
            key=LAST_REBALANCE_DATE,
            type_name="date",
            description="Data da última avaliação de rebalanceamento (atualizada por 'bogle suggest').",
            default=None,
            parse=_parse_date,
            to_json=date.isoformat,
            from_json=date.fromisoformat,
        ),
        SettingSpec(
            key=BIRTH_YEAR,
            type_name="int",
            description="Ano de nascimento; a idade de cada ano define os alvos da trajetória.",
            default=None,
            parse=_parse_birth_year,
            to_json=int,
            from_json=int,
        ),
        SettingSpec(
            key=RETIREMENT_AGE,
            type_name="int",
            description="Idade de aposentadoria; antes dela, 'bogle withdraw' avisa que a política é não vender.",
            default=70,
            parse=_parse_retirement_age,
            to_json=int,
            from_json=int,
        ),
        SettingSpec(
            key=GLIDE_PATH_APPLIED_YEAR,
            type_name="int",
            description="Último ano em que os alvos da trajetória foram aplicados (atualizado em 01/01).",
            default=None,
            parse=_parse_year,
            to_json=int,
            from_json=int,
        ),
        SettingSpec(
            key=LAST_ANNUAL_REBALANCE_YEAR,
            type_name="int",
            description="Último ano em que o rebalanceamento anual da aposentadoria foi feito ('bogle rebalance --done').",
            default=None,
            parse=_parse_year,
            to_json=int,
            from_json=int,
        ),
        SettingSpec(
            key=SPENDING_LAST_SYNC_AT,
            type_name="datetime",
            description="Último sync de gastos que gravou alguma conta (o automático espera 24 h depois dele).",
            default=None,
            parse=_parse_datetime,
            to_json=datetime.isoformat,
            from_json=datetime.fromisoformat,
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
        return "(não definido)"
    if isinstance(value, bool):
        # Antes do ramo de int: em Python, bool *e* int.
        return "true" if value else "false"
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    if isinstance(value, date):
        return value.isoformat()
    return str(value)
