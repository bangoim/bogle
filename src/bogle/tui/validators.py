"""Real-time validation for the forms (issue #74).

The parsing is not reimplemented: these wrap :mod:`bogle.cli.parsing` (a leaf
module with the format rules — no typer involved) and translate a
``ValidationError`` into a Textual ``ValidationResult``. The ``label`` becomes
the subject of the message, so the same parser that says
``--shares deve ser um numero decimal`` says ``Quantidade deve ser um numero
decimal`` here. ``feminine`` and ``plural`` describe the label, so what is said
about it agrees: "Quantidade é obrigatória", "Taxas não podem ser negativas".

Range rules stay next to the format ones on purpose: catching "quantidade 0"
while it is typed is the whole point of the interface — the repository would
only complain after a round trip.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from decimal import Decimal
from typing import override

from textual.validation import ValidationResult, Validator

from bogle import format as fmt
from bogle.cli.parsing import parse_date, parse_decimal
from bogle.domain.errors import ValidationError

_ZERO = Decimal("0")


def _agree(adjective: str, *, feminine: bool, plural: bool) -> str:
    """``obrigatório`` -> ``obrigatória`` / ``obrigatórios`` / ``obrigatórias``."""
    if feminine:
        adjective = adjective[:-1] + "a"
    return adjective + "s" if plural else adjective


class TextField(Validator):
    """Required free text (a ticker being registered, an issuer's name)."""

    def __init__(self, label: str, *, feminine: bool = False) -> None:
        super().__init__()
        self.label = label
        self.feminine = feminine

    @override
    def validate(self, value: str) -> ValidationResult:
        if not value.strip():
            return self.failure(f"{self.label} é {_agree('obrigatório', feminine=self.feminine, plural=False)}.")
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
        feminine: bool = False,
        plural: bool = False,
    ) -> None:
        super().__init__()
        self.label = label
        self.feminine = feminine
        self.plural = plural
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
            required = _agree("obrigatório", feminine=self.feminine, plural=self.plural)
            return self.failure(self.blank_message or f"{self.label} {'são' if self.plural else 'é'} {required}.")
        try:
            parsed = self.parse(text, self.label)
        except ValidationError as exc:
            return self.failure(str(exc))
        if self.positive and parsed <= _ZERO:
            greater = "devem ser maiores" if self.plural else "deve ser maior"
            return self.failure(f"{self.label} {greater} que zero, recebido {parsed}.")
        if not self.positive and parsed < _ZERO:
            negative = _agree("negativo", feminine=self.feminine, plural=self.plural)
            can = "podem" if self.plural else "pode"
            return self.failure(f"{self.label} não {can} ser {negative}, recebido {parsed}.")
        return self.success()


class DateField(Validator):
    def __init__(self, label: str, *, allow_blank: bool = False, feminine: bool = False) -> None:
        super().__init__()
        self.label = label
        self.feminine = feminine
        self.allow_blank = allow_blank
        """``True`` where the date is genuinely optional (a maturity date on a
        daily-liquidity instrument)."""

    @override
    def validate(self, value: str) -> ValidationResult:
        text = value.strip()
        if not text:
            if self.allow_blank:
                return self.success()
            return self.failure(f"{self.label} é {_agree('obrigatório', feminine=self.feminine, plural=False)}.")
        try:
            parse_date(text, self.label)
        except ValidationError as exc:
            return self.failure(str(exc))
        return self.success()


class HeldShares(Validator):
    """A sale's quantity, bounded by the position it comes out of.

    The ceiling is half the reason the sale starts from a list of positions: the
    number being typed has a maximum, and the maximum is knowable *while* it is
    typed. It is checked again on the way to the database
    (:func:`~bogle.sales.resolve_sale_shares`, which the command shares) — this
    one exists so the correction happens next to the field.

    The ceiling here is today's position; the one on the way to the database is
    the position on the sale's date. Today's always bounds it from above (a sale
    in the past can only have less room, never more), so this one never refuses
    what the ledger would take.
    """

    def __init__(self, ticker: str, available: Decimal, *, label: str = "Quantidade", feminine: bool = True) -> None:
        super().__init__()
        self.ticker = ticker
        self.available = available
        self.label = label
        self._amount = DecimalField(label, positive=True, feminine=feminine)
        """Format and sign first: "abc" is not a quantity above the ceiling."""

    @override
    def validate(self, value: str) -> ValidationResult:
        result = self._amount.validate(value)
        if not result.is_valid:
            return result
        shares = parse_decimal(value.strip(), self.label)
        if shares > self.available:
            return self.failure(
                f"{self.ticker} tem {fmt.exact(self.available)} cotas; marque 'Vender tudo' para zerar a posição."
            )
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
            return self.failure(f"{self.label} é obrigatório.")
        if not self.known:  # lista ainda nao carregou
            return self.success()
        if ticker not in self.known:
            # A mensagem e da interface: mandar para o `bogle add` seria mandar
            # fechar a tela para fazer o que a tela ao lado faz.
            return self.failure(f"Ativo '{ticker}' não encontrado. Cadastre em Ativos (6) antes de lançar.")
        return self.success()
