"""Shared parsers for user input, in both frontends.

Format-only validation lives here; range/coherence rules belong to the
domain validators and repositories, which already aggregate friendly
errors. The TUI's forms reuse these so a value the CLI accepts is a value
the forms accept (and the other way around).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from bogle import format as fmt
from bogle.db import DEFAULT_TIMEZONE
from bogle.domain.errors import ValidationError


def parse_decimal(value: str, option: str) -> Decimal:
    """Parse a decimal the user typed.

    The value arrives as a string so we get exact decimal handling instead of
    going through ``float`` (and its 0.1 + 0.2 surprises). The configured
    separators are accepted (``1.234,56`` when the decimal is a comma), and so is
    the canonical dot decimal.
    """
    reading = fmt.read_number(value)
    if reading.canonical is None:
        raise ValidationError(f"{option}: {_separator_problem(value, reading.reason)}")
    try:
        parsed = Decimal(reading.canonical)
    except InvalidOperation:
        raise ValidationError(f"{option} deve ser um numero decimal, recebido {value!r}.") from None
    # NaN/Infinity parseiam como Decimal mas estouram em comparacoes e no banco.
    if not parsed.is_finite():
        raise ValidationError(f"{option} deve ser um numero decimal, recebido {value!r}.")
    return parsed


def _separator_problem(value: str, reason: str) -> str:
    """Explain a rejected number in terms of the separators in force."""
    separators = fmt.separators()
    if reason == fmt.AMBIGUOUS:
        plain = value.strip().replace(separators.thousands, "")
        return (
            f"{value!r} tem duas leituras, porque '{separators.thousands}' pode ser milhar ou decimal. "
            f"Escreva {plain} para o inteiro, ou {value.strip()}{separators.decimal}00 com os centavos."
        )
    return (
        f"nao consegui ler {value!r} como numero. Com separador decimal '{separators.decimal}', "
        f"o milhar e '{separators.thousands}' em grupos de tres (ex: {fmt.sample()})."
    )


def parse_date(value: str, option: str) -> datetime:
    """Parse an ISO date (YYYY-MM-DD) into an America/Sao_Paulo datetime."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValidationError(f"{option} deve ser uma data ISO (YYYY-MM-DD), recebido {value!r}.") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
    return parsed
