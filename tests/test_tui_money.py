"""Tests for the money input: amounts typed from the cents, as in banking apps."""

from __future__ import annotations

from decimal import Decimal
from typing import override

import pytest
from textual.app import App, ComposeResult

from bogle import format as fmt
from bogle.tui.validators import DecimalField
from bogle.tui.widgets.money import MoneyInput


class MoneyApp(App[None]):
    def __init__(self, value: str = "", *, validators: list[DecimalField] | None = None) -> None:
        super().__init__()
        self.initial = value
        self.validators = validators or []

    @override
    def compose(self) -> ComposeResult:
        yield MoneyInput(value=self.initial, validators=self.validators)

    @property
    def money(self) -> MoneyInput:
        return self.query_one(MoneyInput)


class TestTyping:
    @pytest.mark.asyncio
    async def test_each_digit_enters_as_the_last_cent(self) -> None:
        app = MoneyApp()
        async with app.run_test() as pilot:
            shown = []
            for digit in "328684":
                await pilot.press(digit)
                shown.append(app.money.value)
            assert shown == ["0.03", "0.32", "3.28", "32.86", "328.68", "3,286.84"]
            assert app.money.canonical == "3286.84"
            assert app.money.amount == Decimal("3286.84")

    @pytest.mark.asyncio
    async def test_the_display_follows_the_configured_separator(self) -> None:
        fmt.configure(",")
        app = MoneyApp()
        async with app.run_test() as pilot:
            await pilot.press(*"328684")
            assert app.money.value == "3.286,84"
            assert app.money.canonical == "3286.84"  # o que os parsers leem nao muda

    @pytest.mark.asyncio
    async def test_separators_letters_and_signs_are_not_typed(self) -> None:
        app = MoneyApp()
        async with app.run_test() as pilot:
            await pilot.press("1", ",", ".", "-", "a", "2")
            assert app.money.value == "0.12"

    @pytest.mark.asyncio
    async def test_the_cursor_position_does_not_matter(self) -> None:
        # Como nos bancos: o digito sempre entra pelo fim.
        app = MoneyApp()
        async with app.run_test() as pilot:
            await pilot.press(*"1234")
            await pilot.press("home", "5")
            assert app.money.value == "123.45"

    @pytest.mark.asyncio
    async def test_typing_over_a_full_selection_starts_over(self) -> None:
        app = MoneyApp()
        async with app.run_test() as pilot:
            await pilot.press(*"1234")
            app.money.select_all()
            await pilot.press("7")
            assert app.money.value == "0.07"


class TestDeleting:
    @pytest.mark.asyncio
    async def test_backspace_takes_the_last_digit_back(self) -> None:
        app = MoneyApp()
        async with app.run_test() as pilot:
            await pilot.press(*"328684", "backspace")
            assert app.money.value == "328.68"

    @pytest.mark.asyncio
    async def test_past_zero_the_field_is_empty_again(self) -> None:
        # Em branco tem sentido proprio (IR opcional, "volta ao mercado" nos
        # modais), entao precisa dar para chegar nele so com o backspace.
        app = MoneyApp()
        async with app.run_test() as pilot:
            await pilot.press("1", "backspace")
            assert app.money.value == "0.00"
            await pilot.press("backspace")
            assert app.money.value == ""
            assert app.money.canonical == ""
            assert app.money.amount is None

    @pytest.mark.asyncio
    async def test_clearing_everything_empties_it(self) -> None:
        app = MoneyApp()
        async with app.run_test() as pilot:
            await pilot.press(*"1234")
            app.money.select_all()
            await pilot.press("backspace")
            assert app.money.value == ""


class TestPasting:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("pasted", "shown"),
        [("3286.84", "3,286.84"), ("3286,8", "3,286.80"), ("3.286,84", "3,286.84"), ("3,286.84", "3,286.84")],
    )
    async def test_a_pasted_amount_is_read_as_a_number(self, pasted: str, shown: str) -> None:
        # Digito a digito, "3286,8" viraria 328,68: colar e trazer um numero pronto.
        app = MoneyApp()
        async with app.run_test():
            app.money.insert_text_at_cursor(pasted)
            assert app.money.value == shown

    @pytest.mark.asyncio
    async def test_something_that_is_not_an_amount_is_refused(self) -> None:
        app = MoneyApp()
        async with app.run_test() as pilot:
            await pilot.press(*"12")
            app.money.insert_text_at_cursor("abc")
            assert app.money.value == "0.12"


class TestValues:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("initial", "shown"), [("", ""), ("0", "0.00"), ("114.86", "114.86"), ("300,5", "300.50")])
    async def test_a_value_set_by_the_code_is_a_plain_number(self, initial: str, shown: str) -> None:
        # O valor inicial de um campo e o que um modal reabre com chegam como
        # numero simples, e sao mostrados no formato do campo.
        app = MoneyApp(initial)
        async with app.run_test():
            assert app.money.value == shown

    @pytest.mark.asyncio
    async def test_digits_continue_from_a_value_set_by_the_code(self) -> None:
        app = MoneyApp("0")
        async with app.run_test() as pilot:
            await pilot.press("5")
            assert app.money.value == "0.05"

    @pytest.mark.asyncio
    async def test_the_validators_read_the_number_not_the_display(self) -> None:
        # "3,286.84" nao passaria no parser de entrada (milhar e recusado);
        # o validador recebe "3286.84".
        app = MoneyApp(validators=[DecimalField("Valor", positive=True)])
        async with app.run_test() as pilot:
            await pilot.press(*"328684")
            result = app.money.validate(app.money.value)
            assert result is not None and result.is_valid
            app.money.select_all()
            await pilot.press("0")
            result = app.money.validate(app.money.value)
            assert result is not None
            assert result.failure_descriptions == ["Valor deve ser maior que zero, recebido 0.00."]

    @pytest.mark.asyncio
    async def test_an_amount_too_long_to_be_real_stops_growing(self) -> None:
        app = MoneyApp()
        async with app.run_test() as pilot:
            await pilot.press(*("9" * 16))
            assert app.money.canonical == "9999999999999.99"
