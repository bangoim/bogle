"""Shared parsers for CLI options.

Format-only validation lives here; range/coherence rules belong to the
domain validators and repositories, which already aggregate friendly
errors.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from bogle.db import DEFAULT_TIMEZONE
from bogle.domain.errors import ValidationError


def parse_decimal(value: str, option: str) -> Decimal:
    """Parse a CLI decimal argument.

    The argument arrives as a string so we get exact decimal handling
    instead of going through ``float`` (and its 0.1 + 0.2 surprises).
    """
    try:
        parsed = Decimal(value)
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
