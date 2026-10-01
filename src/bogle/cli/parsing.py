"""Shared parsers for user input, in both frontends.

Reading a value the user typed lives here, and so do the ranges that belong to
the *value itself* — a target weight is a fraction in ``(0, 1]`` (``[0, 1]`` when
it changes an existing asset), a contracted rate is positive and bounded by its
column. Coherence between fields (which metadata a type requires, whether a
ticker exists) belongs to the domain validators and repositories, which aggregate
friendly errors.

Every rule takes the field's name as an argument, so the message names what the
user was filling: ``--weight`` from the command, ``Peso-alvo`` from the form. The
TUI's forms reuse these, so a value the CLI accepts is a value the forms accept —
and the other way around.
"""

from __future__ import annotations

from collections.abc import Sequence
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


def parse_weight(value: str, option: str, *, allow_zero: bool = False) -> Decimal:
    """Parse a target weight: a decimal fraction in ``(0, 1]`` (``0.6`` = 60%).

    ``allow_zero`` opens the range to ``[0, 1]``, for changing an existing asset:
    zero is how an asset leaves the plan and keeps its history (migration 006),
    and without it the only way to get there was a sale that empties the position
    — a target put back by mistake could not be taken out again. Registering an
    asset still takes a weight: one that enters the plan with nothing is not
    entering it.
    """
    weight = parse_decimal(value, option)
    above_floor = weight >= Decimal("0") if allow_zero else weight > Decimal("0")
    if not (above_floor and weight <= Decimal("1")):
        interval = "[0, 1]" if allow_zero else "(0, 1]"
        raise ValidationError(f"{option} deve estar em {interval}, recebido {weight}.")
    return weight


def parse_rate(value: str, option: str) -> Decimal:
    """Parse a contracted rate (``1.10`` = 110% of CDI, ``0.065`` = IPCA + 6.5%)."""
    rate = parse_decimal(value, option)
    # Limite espelha a coluna rate NUMERIC(10, 6): |valor| < 10^4.
    if not (Decimal("0") < rate < Decimal("10000")):
        raise ValidationError(f"{option} deve estar em (0, 10000), recebido {rate}.")
    return rate


def parse_price_overrides(values: Sequence[str], option: str) -> dict[str, Decimal]:
    """Parse repeated ``TICKER=PRECO`` options into ``{ticker: price}``.

    The syntax only exists in the command (the interface asks per row), but the
    number goes through :func:`parse_decimal` all the same, so ``114,86`` and
    ``114.86`` mean the same thing in both. Whether the ticker is in the portfolio
    and whether the price makes sense for its type is checked by
    :func:`~bogle.rebalancing.suggest_allocation`, which has the positions.
    """
    prices: dict[str, Decimal] = {}
    for raw in values:
        ticker, separator, price = raw.partition("=")
        if not separator or not ticker.strip():
            raise ValidationError(f"{option} espera TICKER=PRECO (ex: VWRA11=114,86), recebido {raw!r}.")
        name = ticker.strip().upper()
        if name in prices:
            raise ValidationError(f"{option} repetido para {name}: informe um preco so por ticker.")
        prices[name] = parse_decimal(price.strip(), f"{option} {name}")
    return prices


def parse_date(value: str, option: str) -> datetime:
    """Parse an ISO date (YYYY-MM-DD) into an America/Sao_Paulo datetime."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValidationError(f"{option} deve ser uma data ISO (YYYY-MM-DD), recebido {value!r}.") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
    return parsed
