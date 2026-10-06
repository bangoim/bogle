"""A money input that fills from the cents, the way banking apps take an amount.

Each digit enters as the last cent and pushes the others left — ``3``, ``32``,
``328`` and ``328684`` read ``0,03``, ``0,32``, ``3,28`` and ``3.286,84`` — so an
amount is typed without ever reaching for a separator. Backspace takes the last
digit back out.

Empty is a value of its own, not zero: a blank optional tax and a blank modal
("back to the market price") mean something the forms rely on. So the field starts
empty, the first digit makes it ``0,01``, and deleting past ``0,00`` empties it
again.

What the input *shows* is the grouped display (:func:`~bogle.format.typed_money`);
what the rest of the code reads is :attr:`MoneyInput.canonical` — ``3286.84``,
the form :func:`~bogle.cli.parsing.parse_decimal` and the validators take, so a
money field is checked by exactly the rules any other number is.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any, override

from textual.validation import ValidationResult
from textual.widgets import Input

from bogle import format as fmt
from bogle.cli.parsing import parse_decimal
from bogle.domain.errors import ValidationError

_CENTS = Decimal("0.01")
_MAX_DIGITS = 15
"""Ate R$ 9.999.999.999.999,99: um dedo preso numa tecla para aqui, e nao no banco."""


class MoneyInput(Input):
    """An ``Input`` for amounts in reais, typed from the cents up."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._cents: int | None = None
        """The amount in cents; ``None`` while the field is empty."""
        super().__init__(*args, **kwargs)

    @property
    def amount(self) -> Decimal | None:
        return None if self._cents is None else Decimal(self._cents).scaleb(-2)

    @property
    def canonical(self) -> str:
        """The amount as the parsers read it (``3286.84``), or ``""`` when empty."""
        amount = self.amount
        return "" if amount is None else format(amount, "f")

    # --- o que aparece --------------------------------------------------

    def validate_value(self, value: str) -> str:
        """Keep what is shown in step with the cents, whoever set the value.

        Called by Textual on every assignment to ``value``. The edits below
        assign the display they already worked out; anything else (the field's
        initial value, :meth:`~bogle.tui.widgets.form.Field.set_value`, a modal
        reopening with what was informed) arrives as a plain number — ``0``,
        ``114.86`` — and is read as one.
        """
        if value == _shown(self._cents):
            return value
        self._cents = _cents_of(value)
        return _shown(self._cents)

    @override
    def validate(self, value: str) -> ValidationResult | None:
        # Os validadores leem o numero, nao a exibicao: "3.286,84" nao passa no
        # parser de entrada (milhar e recusado de proposito), "3286.84" passa.
        return super().validate(self.canonical if value == self.value else value)

    # --- edicao ---------------------------------------------------------

    @override
    def replace(self, text: str, start: int, end: int) -> None:
        """Every edit lands here (typing, pasting, deleting): apply it to the cents.

        Where the cursor is does not matter, as in the banking apps: a digit
        always enters as the last cent and a deletion always takes from there.
        """
        shown = self.value
        start, end = sorted((max(0, start), min(len(shown), end)))
        everything = start == 0 and end == len(shown) and end > 0
        if not text:
            if start == end:
                return
            cents = None if everything or not self._cents else self._cents // 10 ** max(1, _digits(shown[start:end]))
        elif len(text) == 1:
            if not text.isdigit():
                return  # o separador nao e digitado: ele aparece sozinho
            cents = (0 if everything or self._cents is None else self._cents * 10) + int(text)
        else:
            pasted = fmt.read_money(text)
            if pasted is None:
                self.restricted()
                return
            cents = int(pasted.quantize(_CENTS, rounding=ROUND_HALF_UP).scaleb(2))
        if cents is not None and len(str(cents)) > _MAX_DIGITS:
            self.restricted()
            return
        self._cents = cents
        self.value = _shown(cents)
        self.cursor_position = len(self.value)


def _shown(cents: int | None) -> str:
    return "" if cents is None else fmt.typed_money(Decimal(cents).scaleb(-2))


def _cents_of(value: str) -> int | None:
    """A plain number (``0``, ``114.86``, ``114,86``) in cents; ``None`` when blank or not a number."""
    if not value.strip():
        return None
    try:
        amount = parse_decimal(value, "valor")
    except ValidationError:
        return None
    if amount < 0:
        return None
    return int(amount.quantize(_CENTS, rounding=ROUND_HALF_UP).scaleb(2))


def _digits(text: str) -> int:
    return sum(char.isdigit() for char in text)
