"""Real-time validation for the forms (issue #74).

The parsing is not reimplemented: these wrap :mod:`bogle.cli.parsing` (a leaf
module with the format rules — no typer involved) and translate a
``ValidationError`` into a Textual ``ValidationResult``. The ``label`` becomes
the subject of the message, so the same parser that says
``--shares deve ser um numero decimal`` says ``Quantidade deve ser um numero
decimal`` here.

Range rules stay next to the format ones on purpose: catching "quantidade 0"
while it is typed is the whole point of the interface — the repository would
only complain after a round trip.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from decimal import Decimal
from typing import override

from textual.validation import ValidationResult, Validator

from bogle.cli.parsing import parse_date, parse_decimal
from bogle.domain.errors import ValidationError

_ZERO = Decimal("0")


class TextField(Validator):
    """Required free text (a ticker being registered, an issuer's name)."""

    def __init__(self, label: str) -> None:
        super().__init__()
        self.label = label

    @override
    def validate(self, value: str) -> ValidationResult:
        if not value.strip():
            return self.failure(f"{self.label} e obrigatorio.")
        return self.success()


class DecimalField(Validator):
    """A decimal, optionally blank, optionally constrained in sign or in range."""

    def __init__(
        self,
        label: str,
        *,
        allow_blank: bool = False,
        positive: bool = False,
        blank_message: str | None = None,
        parse: Callable[[str, str], Decimal] = parse_decimal,
    ) -> None:
        super().__init__()
        self.label = label
        self.allow_blank = allow_blank
        self.positive = positive
        """``True`` requires > 0; otherwise >= 0 (fees, taxes)."""
        self.blank_message = blank_message
        """Overrides the "obrigatorio" message (JCP explains *why* it is)."""
        self.parse = parse
        """Which shared parser to run: a weight and a rate have their own ranges,
        and reusing ``cli/parsing``'s means the form refuses exactly what the
        command refuses, with the same wording."""

    @override
    def validate(self, value: str) -> ValidationResult:
        text = value.strip()
        if not text:
            if self.allow_blank:
                return self.success()
            return self.failure(self.blank_message or f"{self.label} e obrigatorio.")
        try:
            parsed = self.parse(text, self.label)
        except ValidationError as exc:
            return self.failure(str(exc))
        if self.positive and parsed <= _ZERO:
            return self.failure(f"{self.label} deve ser maior que zero, recebido {parsed}.")
        if not self.positive and parsed < _ZERO:
            return self.failure(f"{self.label} nao pode ser negativo, recebido {parsed}.")
        return self.success()


class DateField(Validator):
    def __init__(self, label: str, *, allow_blank: bool = False) -> None:
        super().__init__()
        self.label = label
        self.allow_blank = allow_blank
        """``True`` where the date is genuinely optional (a maturity date on a
        daily-liquidity instrument)."""

    @override
    def validate(self, value: str) -> ValidationResult:
        text = value.strip()
        if not text:
            if self.allow_blank:
                return self.success()
            return self.failure(f"{self.label} e obrigatoria.")
        try:
            parse_date(text, self.label)
        except ValidationError as exc:
            return self.failure(str(exc))
        return self.success()


class KnownTicker(Validator):
    """A ticker that is already registered.

    The list arrives from a worker after the screen opens, so while it is empty
    anything passes — the repository still rejects an unknown ticker with
    ``AssetNotFoundError``.
    """

    def __init__(self, *, label: str = "Ticker") -> None:
        super().__init__()
        self.label = label
        self.known: set[str] = set()

    def learn(self, tickers: Iterable[str]) -> None:
        self.known = {ticker.upper() for ticker in tickers}

    @override
    def validate(self, value: str) -> ValidationResult:
        ticker = value.strip().upper()
        if not ticker:
            return self.failure(f"{self.label} e obrigatorio.")
        if not self.known:  # lista ainda nao carregou
            return self.success()
        if ticker not in self.known:
            return self.failure(f"Ativo '{ticker}' nao encontrado. Cadastre com 'bogle add' antes de lancar.")
        return self.success()
