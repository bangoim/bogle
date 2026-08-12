"""Tests for the TUI's recording forms (issue #74).

The core of the epic: an invalid value never reaches the database and says why,
the JCP/RENDIMENTO rule holds, a confirmed entry is written with exactly the
values typed, and the "what next" modal decides between another entry and Home.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from textual.widgets import Input, Select

from bogle.domain.errors import AssetNotFoundError, ValidationError
from bogle.domain.transactions import TransactionType
from bogle.tui import services
from bogle.tui.screens.home import HomeScreen
from bogle.tui.screens.modals import ConfirmModal, NextStepModal
from bogle.tui.screens.register import IncomeFormScreen, RegisterScreen, TradeFormScreen
from bogle.tui.widgets.form import Field
from tests.tui_fakes import ToastSpy, make_app, make_transaction, settle, stub_services

SAO_PAULO = ZoneInfo("America/Sao_Paulo")


class RecordSpy:
    """Records what the form asked to write, and can fail on demand."""

    def __init__(self, kind: TransactionType, *, error: Exception | None = None) -> None:
        self.kind = kind
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return make_transaction(
            kwargs.get("income_type", self.kind), **{k: v for k, v in kwargs.items() if k != "income_type"}
        )

    @property
    def last(self) -> dict[str, Any]:
        return self.calls[-1]


@pytest.fixture(autouse=True)
def _services(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_services(monkeypatch)


async def open_form(pilot: Any, screen: Any) -> Any:
    await pilot.app.push_screen(screen)
    await settle(pilot)
    return pilot.app.screen


def fill(screen: Any, **values: str) -> None:
    for field_id, value in values.items():
        screen.field(field_id).set_value(value)


def error_of(screen: Any, field_id: str) -> str:
    return screen.field(field_id).error


class TestSubmenu:
    @pytest.mark.asyncio
    async def test_each_number_opens_its_form(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            await pilot.press("2")  # Registrar, na Home
            await settle(pilot)
            assert isinstance(app.screen, RegisterScreen)
            await pilot.press("2")  # Venda
            await settle(pilot)
            assert isinstance(app.screen, TradeFormScreen)
            assert app.screen.kind is TransactionType.SELL
            await pilot.press("escape")
            await settle(pilot)
            await pilot.press("3")  # Provento
            await settle(pilot)
            assert isinstance(app.screen, IncomeFormScreen)


class TestValidation:
    @pytest.mark.asyncio
    async def test_typing_a_bad_quantity_explains_it_next_to_the_field(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_form(pilot, TradeFormScreen(kind=TransactionType.BUY))
            await pilot.press("tab")  # do ticker para a quantidade
            await pilot.press("a", "b", "c")
            await pilot.pause()
            assert error_of(screen, "shares") == "Quantidade deve ser um numero decimal, recebido 'abc'."

    @pytest.mark.asyncio
    async def test_submitting_an_incomplete_form_writes_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        spy = RecordSpy(TransactionType.BUY)
        monkeypatch.setattr(services, "record_buy", spy)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_form(pilot, TradeFormScreen(kind=TransactionType.BUY))
            await pilot.press("ctrl+s")
            await settle(pilot)
            assert spy.calls == []
            assert isinstance(app.screen, TradeFormScreen)  # nem chegou no modal
            assert error_of(screen, "ticker") == "Ticker e obrigatorio."
            assert error_of(screen, "shares") == "Quantidade e obrigatorio."
            assert error_of(screen, "price") == "Preco unitario e obrigatorio."

    @pytest.mark.asyncio
    async def test_zero_quantity_is_rejected_before_the_repository(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_form(pilot, TradeFormScreen(kind=TransactionType.BUY))
            fill(screen, ticker="PETR4", shares="0", price="30")
            await pilot.press("ctrl+s")
            await settle(pilot)
            assert error_of(screen, "shares") == "Quantidade deve ser maior que zero, recebido 0."

    @pytest.mark.asyncio
    async def test_negative_fees_are_rejected(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_form(pilot, TradeFormScreen(kind=TransactionType.BUY))
            fill(screen, ticker="PETR4", shares="1", price="30", fees="-1")
            await pilot.press("ctrl+s")
            await settle(pilot)
            assert error_of(screen, "fees") == "Taxas nao pode ser negativo, recebido -1."

    @pytest.mark.asyncio
    async def test_unknown_ticker_is_caught_from_the_registered_list(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_form(pilot, TradeFormScreen(kind=TransactionType.BUY))
            fill(screen, ticker="NOPE", shares="1", price="30")
            await pilot.press("ctrl+s")
            await settle(pilot)
            assert "NOPE" in error_of(screen, "ticker")
            assert "nao encontrado" in error_of(screen, "ticker")

    @pytest.mark.asyncio
    async def test_bad_date_format_is_rejected(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_form(pilot, TradeFormScreen(kind=TransactionType.BUY))
            fill(screen, ticker="PETR4", shares="1", price="30", date="10/03/2026")
            await pilot.press("ctrl+s")
            await settle(pilot)
            assert error_of(screen, "date") == "Data deve ser uma data ISO (YYYY-MM-DD), recebido '10/03/2026'."

    @pytest.mark.asyncio
    async def test_date_defaults_to_today(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_form(pilot, TradeFormScreen(kind=TransactionType.BUY))
            assert screen.field("date").value == datetime.now(tz=SAO_PAULO).date().isoformat()


class TestBuyFlow:
    @pytest.mark.asyncio
    async def test_confirmed_entry_is_written_with_the_typed_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        spy = RecordSpy(TransactionType.BUY)
        monkeypatch.setattr(services, "record_buy", spy)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_form(pilot, TradeFormScreen(kind=TransactionType.BUY))
            fill(screen, ticker="auvp11", shares="3", price="126.25", fees="0.13", date="2026-03-10")
            await pilot.press("ctrl+s")
            await settle(pilot)
            assert isinstance(app.screen, ConfirmModal)
            await pilot.press("enter")  # botao Registrar do modal
            await settle(pilot)

            assert spy.last == {
                "ticker": "AUVP11",  # normalizado
                "when": datetime(2026, 3, 10, tzinfo=SAO_PAULO),
                "shares": Decimal("3"),
                "unit_price": Decimal("126.25"),
                "fees": Decimal("0.13"),
            }

    @pytest.mark.asyncio
    async def test_confirmation_modal_summarizes_the_entry(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_form(pilot, TradeFormScreen(kind=TransactionType.BUY))
            fill(screen, ticker="AUVP11", shares="3", price="126.25", fees="0.13", date="2026-03-10")
            await pilot.press("ctrl+s")
            await settle(pilot)
            modal = app.screen
            assert isinstance(modal, ConfirmModal)
            assert modal.body == ("Compra: 3 x AUVP11 @ 126.25 em 2026-03-10\nTaxas 0.13\nCusto total: 378.88")

    @pytest.mark.asyncio
    async def test_cancelling_the_modal_writes_nothing_and_keeps_the_values(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spy = RecordSpy(TransactionType.BUY)
        monkeypatch.setattr(services, "record_buy", spy)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_form(pilot, TradeFormScreen(kind=TransactionType.BUY))
            fill(screen, ticker="AUVP11", shares="3", price="126.25")
            await pilot.press("ctrl+s")
            await settle(pilot)
            await pilot.press("escape")  # cancela
            await settle(pilot)
            assert spy.calls == []
            assert isinstance(app.screen, TradeFormScreen)
            assert screen.field("shares").value == "3"

    @pytest.mark.asyncio
    async def test_new_entry_clears_the_form_and_stays(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_form(pilot, TradeFormScreen(kind=TransactionType.BUY))
            fill(screen, ticker="AUVP11", shares="3", price="126.25", fees="0.13")
            await pilot.press("ctrl+s")
            await settle(pilot)
            await pilot.press("enter")  # confirma
            await settle(pilot)
            assert isinstance(app.screen, NextStepModal)
            await pilot.press("enter")  # "Novo lancamento"
            await settle(pilot)
            assert isinstance(app.screen, TradeFormScreen)
            assert screen.field("ticker").value == ""
            assert screen.field("shares").value == ""
            assert screen.field("fees").value == "0"  # volta ao default, nao vazio

    @pytest.mark.asyncio
    async def test_back_to_home_pops_every_screen(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.press("2")  # Registrar
            await settle(pilot)
            await pilot.press("1")  # Compra
            await settle(pilot)
            screen = app.screen
            fill(screen, ticker="AUVP11", shares="3", price="126.25")
            await pilot.press("ctrl+s")
            await settle(pilot)
            await pilot.press("enter")  # confirma
            await settle(pilot)
            assert isinstance(app.screen, NextStepModal)
            await pilot.click("#home")
            await settle(pilot)
            assert isinstance(app.screen, HomeScreen)
            assert len(app.screen_stack) == 1

    @pytest.mark.asyncio
    async def test_repository_error_keeps_the_user_in_the_form(self, monkeypatch: pytest.MonkeyPatch) -> None:
        spy = RecordSpy(TransactionType.BUY, error=AssetNotFoundError("AUVP11"))
        monkeypatch.setattr(services, "record_buy", spy)
        toasts = ToastSpy()
        toasts.install(monkeypatch, TradeFormScreen)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_form(pilot, TradeFormScreen(kind=TransactionType.BUY))
            fill(screen, ticker="AUVP11", shares="3", price="126.25")
            await pilot.press("ctrl+s")
            await settle(pilot)
            await pilot.press("enter")
            await settle(pilot)
            assert isinstance(app.screen, TradeFormScreen)
            assert screen.field("shares").value == "3"  # nada perdido
            assert toasts.severity_of("nao encontrado") == "error"


class TestSellFlow:
    @pytest.mark.asyncio
    async def test_sale_carries_the_withheld_tax(self, monkeypatch: pytest.MonkeyPatch) -> None:
        spy = RecordSpy(TransactionType.SELL)
        monkeypatch.setattr(services, "record_sell", spy)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_form(pilot, TradeFormScreen(kind=TransactionType.SELL))
            fill(screen, ticker="AUVP11", shares="1", price="130", fees="0.13", tax="0.01", date="2026-06-20")
            await pilot.press("ctrl+s")
            await settle(pilot)
            modal = app.screen
            assert isinstance(modal, ConfirmModal)
            assert modal.body == (
                "Venda: 1 x AUVP11 @ 130.00 em 2026-06-20\nTaxas 0.13, IR retido 0.01\nProduto bruto da venda: 130.00"
            )
            await pilot.press("enter")
            await settle(pilot)
            assert spy.last["tax_withheld"] == Decimal("0.01")

    @pytest.mark.asyncio
    async def test_buy_form_has_no_withheld_tax_field(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_form(pilot, TradeFormScreen(kind=TransactionType.BUY))
            assert [field.id for field in screen.query(Field)] == ["ticker", "shares", "price", "fees", "date"]


class TestIncomeFlow:
    @pytest.mark.asyncio
    async def test_dividend_is_recorded_with_its_type(self, monkeypatch: pytest.MonkeyPatch) -> None:
        spy = RecordSpy(TransactionType.DIVIDEND)
        monkeypatch.setattr(services, "record_income", spy)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_form(pilot, IncomeFormScreen())
            fill(screen, ticker="PETR4", amount="123.45", date="2026-05-15")
            await pilot.press("ctrl+s")
            await settle(pilot)
            await pilot.press("enter")
            await settle(pilot)
            assert spy.last == {
                "ticker": "PETR4",
                "income_type": TransactionType.DIVIDEND,
                "when": datetime(2026, 5, 15, tzinfo=SAO_PAULO),
                "amount": Decimal("123.45"),
                "tax_withheld": None,
            }

    @pytest.mark.asyncio
    async def test_jcp_requires_the_withheld_tax(self, monkeypatch: pytest.MonkeyPatch) -> None:
        spy = RecordSpy(TransactionType.JCP)
        monkeypatch.setattr(services, "record_income", spy)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_form(pilot, IncomeFormScreen())
            screen.query_one(Select).value = TransactionType.JCP
            await pilot.pause()
            fill(screen, ticker="PETR4", amount="200")
            await pilot.press("ctrl+s")
            await settle(pilot)

            assert spy.calls == []
            assert error_of(screen, "tax") == "IR retido e obrigatorio para JCP (15% retido na fonte)."

            fill(screen, tax="30")
            await pilot.press("ctrl+s")
            await settle(pilot)
            await pilot.press("enter")
            await settle(pilot)
            assert spy.last["income_type"] is TransactionType.JCP
            assert spy.last["tax_withheld"] == Decimal("30")

    @pytest.mark.asyncio
    async def test_rendimento_disables_the_withheld_tax(self, monkeypatch: pytest.MonkeyPatch) -> None:
        spy = RecordSpy(TransactionType.RENDIMENTO)
        monkeypatch.setattr(services, "record_income", spy)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_form(pilot, IncomeFormScreen())
            fill(screen, tax="1")  # valor digitado antes de trocar o tipo
            screen.query_one(Select).value = TransactionType.RENDIMENTO
            await pilot.pause()

            tax = screen.field("tax")
            assert not tax.enabled
            assert tax.value == ""  # limpo, para nao gravar o que nao se aplica
            assert "nao se aplica" in tax.input.placeholder

            fill(screen, ticker="MXRF11", amount="80")
            await pilot.press("ctrl+s")
            await settle(pilot)
            await pilot.press("enter")
            await settle(pilot)
            assert spy.last["tax_withheld"] is None

    @pytest.mark.asyncio
    async def test_switching_back_from_rendimento_re_enables_the_field(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_form(pilot, IncomeFormScreen())
            select = screen.query_one(Select)
            select.value = TransactionType.RENDIMENTO
            await pilot.pause()
            select.value = TransactionType.INTEREST
            await pilot.pause()
            assert screen.field("tax").enabled

    @pytest.mark.asyncio
    async def test_summary_shows_the_net_amount_when_tax_was_withheld(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_form(pilot, IncomeFormScreen())
            screen.query_one(Select).value = TransactionType.JCP
            await pilot.pause()
            fill(screen, ticker="PETR4", amount="200", tax="30", date="2026-05-15")
            await pilot.press("ctrl+s")
            await settle(pilot)
            modal = app.screen
            assert isinstance(modal, ConfirmModal)
            assert modal.body == ("JCP: PETR4 em 2026-05-15\nValor bruto: 200.00\nIR retido: 30.00\nLiquido: 170.00")

    @pytest.mark.asyncio
    async def test_validation_error_from_the_repository_becomes_a_toast(self, monkeypatch: pytest.MonkeyPatch) -> None:
        spy = RecordSpy(TransactionType.DIVIDEND, error=ValidationError("amount deve ser maior que zero, recebido 0."))
        monkeypatch.setattr(services, "record_income", spy)
        toasts = ToastSpy()
        toasts.install(monkeypatch, IncomeFormScreen)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_form(pilot, IncomeFormScreen())
            fill(screen, ticker="PETR4", amount="1")
            await pilot.press("ctrl+s")
            await settle(pilot)
            await pilot.press("enter")
            await settle(pilot)
            assert isinstance(app.screen, IncomeFormScreen)
            assert toasts.severity_of("amount deve ser maior que zero") == "error"


class TestAutocomplete:
    @pytest.mark.asyncio
    async def test_registered_tickers_feed_the_suggester(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_form(pilot, TradeFormScreen(kind=TransactionType.BUY))
            ticker = screen.field("ticker").input
            assert ticker.suggester is not None
            assert await ticker.suggester.get_suggestion("auv") == "AUVP11"

    @pytest.mark.asyncio
    async def test_missing_ticker_list_does_not_block_the_form(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Banco fora do ar na abertura: sem autocomplete, mas o formulario abre e
        # aceita o ticker (o repositorio valida na gravacao).
        def boom() -> list[str]:
            raise ValidationError("sem banco")

        monkeypatch.setattr(services, "list_tickers", boom)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_form(pilot, TradeFormScreen(kind=TransactionType.BUY))
            fill(screen, ticker="QUALQUER", shares="1", price="1")
            await pilot.press("ctrl+s")
            await settle(pilot)
            assert isinstance(app.screen, ConfirmModal)


class TestKeyboard:
    @pytest.mark.asyncio
    async def test_enter_in_a_field_submits(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_form(pilot, TradeFormScreen(kind=TransactionType.BUY))
            fill(screen, ticker="PETR4", shares="1", price="30")
            screen.field("price").input.focus()
            await pilot.press("enter")
            await settle(pilot)
            assert isinstance(app.screen, ConfirmModal)

    @pytest.mark.asyncio
    async def test_escape_leaves_the_form(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.press("2")
            await settle(pilot)
            await pilot.press("1")
            await settle(pilot)
            assert isinstance(app.screen, TradeFormScreen)
            await pilot.press("escape")
            await settle(pilot)
            assert isinstance(app.screen, RegisterScreen)

    @pytest.mark.asyncio
    async def test_ticker_field_takes_focus_when_the_form_opens(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_form(pilot, TradeFormScreen(kind=TransactionType.BUY))
            assert app.focused is screen.field("ticker").query_one(Input)
