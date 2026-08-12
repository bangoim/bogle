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
    going through ``float`` (and its 0.1 + 0.2 surprises). Either separator marks
    the cents (``1000,50`` and ``1000.50`` are the same number); a thousands
    separator is rejected, since it is what makes a number ambiguous.
    """
    canonical = fmt.to_canonical(value)
    if canonical is None:
        raise ValidationError(
            f"{option}: use um unico separador, para os centavos — milhar vai sem separador. "
            f"Recebido {value!r}; escreva 1000 ou 1000,00 (o ponto tambem vale)."
        )
    try:
        parsed = Decimal(canonical)
    except InvalidOperation:
        raise ValidationError(f"{option} deve ser um numero decimal, recebido {value!r}.") from None
    # NaN/Infinity parseiam como Decimal mas estouram em comparacoes e no banco.
    if not parsed.is_finite():
        raise ValidationError(f"{option} deve ser um numero decimal, recebido {value!r}.")
    return parsed


def parse_date(value: str, option: str) -> datetime:
    """Parse an ISO date (YYYY-MM-DD) into an America/Sao_Paulo datetime."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValidationError(f"{option} deve ser uma data ISO (YYYY-MM-DD), recebido {value!r}.") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
    return parsed
