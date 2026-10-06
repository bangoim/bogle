"""Tests for the TUI's recording forms (issue #74).

The core of the epic: an invalid value never reaches the database and says why,
the JCP/RENDIMENTO rule holds, a confirmed entry is written with exactly the
values typed, and the "what next" modal decides between another entry and Home.
"""

from __future__ import annotations

import threading
from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from textual.widgets import Checkbox, Input, Select

from bogle import format as fmt
from bogle.closeout import ClearedTarget
from bogle.domain.errors import AssetNotFoundError, ValidationError, WeightSumExceededError
from bogle.domain.transactions import TransactionType
from bogle.tui import services
from bogle.tui.screens.home import HomeScreen
from bogle.tui.screens.modals import ClearedTargetModal, ConfirmModal, NextStepModal
from bogle.tui.screens.register import IncomeFormScreen, RegisterScreen, SellPickerScreen, TradeFormScreen
from bogle.tui.widgets.form import Field
from tests.tui_fakes import (
    ToastSpy,
    make_app,
    make_cleared_target,
    make_sale,
    make_transaction,
    open_screen,
    sale_position,
    settle,
    stub_services,
    table_columns,
    table_rows,
)

SAO_PAULO = ZoneInfo("America/Sao_Paulo")


class RecordSpy:
    """Records what the form asked to write, and can fail on demand.

    Echoes back what the matching service returns: a sale answers with a
    :class:`~bogle.tui.services.SaleOutcome`, which is where the cleared target
    travels (``cleared`` sets it up).
    """

    def __init__(
        self, kind: TransactionType, *, error: Exception | None = None, cleared: ClearedTarget | None = None
    ) -> None:
        self.kind = kind
        self.error = error
        self.cleared = cleared
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        entry = {k: v for k, v in kwargs.items() if k != "income_type"}
        if self.kind is TransactionType.SELL:
            return make_sale(cleared=self.cleared, **entry)
        return make_transaction(kwargs.get("income_type", self.kind), **entry)

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


async def open_sale(pilot: Any, **position: Any) -> Any:
    """The sale form over a chosen position — what the picker hands it."""
    return await open_form(pilot, TradeFormScreen(kind=TransactionType.SELL, position=sale_position(**position)))


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
            await pilot.press("2")  # Venda: a lista de posicoes, nao o formulario
            await settle(pilot)
            assert isinstance(app.screen, SellPickerScreen)
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
            assert error_of(screen, "shares") == "Quantidade deve ser um número decimal, recebido 'abc'."

    @pytest.mark.asyncio
    async def test_typing_markup_does_not_break_the_error_line(self) -> None:
        # A mensagem de validacao cita o valor digitado ("recebido '[/i]'"), e a
        # linha de erro nao pode tentar interpretar isso como markup.
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_form(pilot, TradeFormScreen(kind=TransactionType.BUY))
            await pilot.press("tab")  # do ticker para a quantidade
            await pilot.press(*"[/i]")
            await pilot.pause()
            assert app.is_running
            assert error_of(screen, "shares") == "Quantidade deve ser um número decimal, recebido '[/i]'."

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
            assert error_of(screen, "ticker") == "Ticker é obrigatório."
            assert error_of(screen, "shares") == "Quantidade é obrigatória."
            assert error_of(screen, "price") == "Preço unitário é obrigatório."

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
    async def test_a_money_field_takes_no_sign(self) -> None:
        # Taxa negativa nao existe, e a mascara de centavos nem deixa o sinal
        # entrar: o "-" e ignorado e o 1 vira o ultimo centavo.
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_form(pilot, TradeFormScreen(kind=TransactionType.BUY))
            screen.field("fees").input.focus()
            await pilot.press("-", "1")
            assert screen.field("fees").input.value == "0.01"
            assert screen.field("fees").value == "0.01"

    @pytest.mark.asyncio
    async def test_unknown_ticker_is_caught_from_the_registered_list(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_form(pilot, TradeFormScreen(kind=TransactionType.BUY))
            fill(screen, ticker="NOPE", shares="1", price="30")
            await pilot.press("ctrl+s")
            await settle(pilot)
            assert "NOPE" in error_of(screen, "ticker")
            assert "não encontrado" in error_of(screen, "ticker")

    @pytest.mark.asyncio
    async def test_either_separator_marks_the_cents(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A entrada nao segue a configuracao de exibicao: virgula e ponto valem
        # os dois, e o milhar vai sem separador.
        spy = RecordSpy(TransactionType.BUY)
        monkeypatch.setattr(services, "record_buy", spy)
        fmt.configure(",")
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_form(pilot, TradeFormScreen(kind=TransactionType.BUY))
            fill(screen, ticker="PETR4", shares="1000,5", price="1234.50", fees="0,13")
            await pilot.press("ctrl+s")
            await settle(pilot)
            modal = app.screen
            assert isinstance(modal, ConfirmModal)
            assert "1.000,5 x PETR4 @ 1.234,50" in modal.body  # exibicao agrupada
            await pilot.press("enter")
            await settle(pilot)
            assert spy.last["shares"] == Decimal("1000.5")
            assert spy.last["unit_price"] == Decimal("1234.50")
            assert spy.last["fees"] == Decimal("0.13")

    @pytest.mark.asyncio
    async def test_a_thousands_separator_is_refused_with_what_to_type(self) -> None:
        # Na quantidade, que e digitada livre (os valores em reais vao pela
        # mascara de centavos, onde separador nenhum e digitado).
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_form(pilot, TradeFormScreen(kind=TransactionType.BUY))
            fill(screen, ticker="PETR4", shares="1.234,5", price="30")
            await pilot.press("ctrl+s")
            await settle(pilot)
            assert isinstance(app.screen, TradeFormScreen)  # nao abriu o modal
            assert "milhar vai sem separador" in error_of(screen, "shares")
            assert "escreva 1000 ou 1000,00" in error_of(screen, "shares")

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
    async def test_date_defaults_to_today_in_sao_paulo(self) -> None:
        # Mesmo default do `bogle buy` (_resolve_date): o fuso da maquina daria
        # uma data diferente da que a CLI grava.
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
            assert screen.field("fees").value == "0.00"  # volta ao default, nao vazio

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
            await pilot.click("#dialog-home")
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
            assert toasts.severity_of("não encontrado") == "error"


class TestSellPicker:
    """A venda comeca pela lista do que se tem, e nao por um ticker digitado."""

    @pytest.mark.asyncio
    async def test_the_open_positions_are_listed_with_what_bounds_the_sale(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SellPickerScreen())
            assert table_columns(screen) == ["Ticker", "Tipo", "Qtd", "Preço médio", "Cotação", "Montante"]
            assert [row[0] for row in table_rows(screen)] == ["PETR4", "CDB-XP-2027"]

    @pytest.mark.asyncio
    async def test_choosing_a_row_opens_the_sale_of_that_position(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, SellPickerScreen())
            await pilot.press("enter")
            await settle(pilot)
            form = app.screen
            assert isinstance(form, TradeFormScreen)
            assert form.kind is TransactionType.SELL
            assert form.position is not None
            assert form.position.ticker == "PETR4"

    @pytest.mark.asyncio
    async def test_nothing_held_says_so_instead_of_offering_a_form(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(services, "list_open_positions", lambda **_: [])
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SellPickerScreen())
            assert "Nenhuma posição aberta" in screen.note
            await pilot.press("enter")
            await settle(pilot)
            assert app.screen is screen  # nao abriu formulario nenhum

    @pytest.mark.asyncio
    async def test_atualizar_asks_the_provider_again(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A cotacao esta na tela, entao 'r' tem de furar o cache de cinco minutos:
        # repetir o preco de antes faz a tecla parecer quebrada.
        calls: list[bool] = []

        def load(*, refresh: bool = False) -> list[Any]:
            calls.append(refresh)
            return []

        monkeypatch.setattr(services, "list_open_positions", load)
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, SellPickerScreen())
            await pilot.press("r")
            await settle(pilot)
            assert calls == [False, True]

    @pytest.mark.asyncio
    async def test_a_failed_load_reports_and_leaves_no_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(**_: Any) -> list[Any]:
            raise ValidationError("sem banco")

        monkeypatch.setattr(services, "list_open_positions", boom)
        toasts = ToastSpy()
        toasts.install(monkeypatch, SellPickerScreen)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SellPickerScreen())
            assert table_rows(screen) == []
            assert toasts.severity_of("sem banco") == "error"


class TestSellFlow:
    @pytest.mark.asyncio
    async def test_sale_carries_the_withheld_tax(self, monkeypatch: pytest.MonkeyPatch) -> None:
        spy = RecordSpy(TransactionType.SELL)
        monkeypatch.setattr(services, "record_sell", spy)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_sale(pilot)
            fill(screen, shares="1", price="130", fees="0.13", tax="0.01", date="2026-06-20")
            await pilot.press("ctrl+s")
            await settle(pilot)
            modal = app.screen
            assert isinstance(modal, ConfirmModal)
            assert modal.body == (
                "Venda: 1 x AUVP11 @ 130.00 em 2026-06-20\nTaxas 0.13, IR retido 0.01\nProduto bruto da venda: 130.00"
            )
            await pilot.press("enter")
            await settle(pilot)
            assert spy.last["ticker"] == "AUVP11"  # veio da posicao escolhida
            assert spy.last["tax_withheld"] == Decimal("0.01")

    @pytest.mark.asyncio
    async def test_the_forms_carry_only_the_fields_their_kind_has(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            buy = await open_form(pilot, TradeFormScreen(kind=TransactionType.BUY))
            assert [field.id for field in buy.query(Field)] == ["ticker", "shares", "price", "fees", "date"]
            await pilot.press("escape")
            await settle(pilot)
            # A venda troca o ticker digitado pela posicao escolhida, e ganha o IR.
            sale = await open_sale(pilot)
            assert [field.id for field in sale.query(Field)] == ["shares", "price", "fees", "tax", "date"]

    @pytest.mark.asyncio
    async def test_the_form_opens_on_the_quantity_showing_what_there_is_to_sell(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_sale(pilot, shares="8", average_price=Decimal("126.25"))
            assert app.focused is screen.field("shares").input  # o ticker ja foi escolhido
            assert "8 cotas" in screen.position_line
            assert "126.25" in screen.position_line
            assert screen.query_one("#form").border_title == "Venda - AUVP11"

    @pytest.mark.asyncio
    async def test_selling_more_than_the_position_has_is_refused_next_to_the_field(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_sale(pilot, shares="8")
            fill(screen, shares="9", price="130")
            await pilot.press("ctrl+s")
            await settle(pilot)
            assert isinstance(app.screen, TradeFormScreen)  # nem chegou no modal
            assert "AUVP11 tem 8 cotas" in error_of(screen, "shares")

    @pytest.mark.asyncio
    async def test_selling_exactly_what_the_position_has_is_allowed(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_sale(pilot, shares="8")
            fill(screen, shares="8", price="130")
            await pilot.press("ctrl+s")
            await settle(pilot)
            assert isinstance(app.screen, ConfirmModal)

    @pytest.mark.asyncio
    async def test_a_partial_sale_goes_straight_to_the_next_step(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(services, "record_sell", RecordSpy(TransactionType.SELL))
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_sale(pilot)
            fill(screen, shares="1", price="130")
            await pilot.press("ctrl+s")
            await settle(pilot)
            await pilot.press("enter")  # confirma
            await settle(pilot)
            assert isinstance(app.screen, NextStepModal)  # nenhum dialogo a mais

    @pytest.mark.asyncio
    async def test_another_entry_goes_back_to_the_list_of_positions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # "Novo lancamento" numa venda nao pode ser outra venda do mesmo ticker
        # com a quantidade que esta venda acabou de mudar.
        monkeypatch.setattr(services, "record_sell", RecordSpy(TransactionType.SELL))
        app = make_app()
        async with app.run_test() as pilot:
            picker = await open_screen(pilot, SellPickerScreen())
            await pilot.press("enter")
            await settle(pilot)
            fill(app.screen, shares="1", price="130")
            await pilot.press("ctrl+s")
            await settle(pilot)
            await pilot.press("enter")  # confirma
            await settle(pilot)
            assert isinstance(app.screen, NextStepModal)
            await pilot.press("enter")  # "Novo lancamento"
            await settle(pilot)
            assert app.screen is picker


class TestSellEverything:
    """ "Vender tudo": a unica quantidade que o app sabe preencher sozinho."""

    @pytest.mark.asyncio
    async def test_checking_it_fills_the_quantity_with_the_whole_position(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_sale(pilot, shares="8")
            screen.query_one("#sell-all", Checkbox).value = True
            await pilot.pause()
            shares = screen.field("shares")
            assert shares.value == "8"
            assert not shares.enabled  # travado: o valor nao veio do teclado

    @pytest.mark.asyncio
    async def test_it_writes_the_whole_position(self, monkeypatch: pytest.MonkeyPatch) -> None:
        spy = RecordSpy(TransactionType.SELL)
        monkeypatch.setattr(services, "record_sell", spy)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_sale(pilot, shares="8")
            screen.query_one("#sell-all", Checkbox).value = True
            await pilot.pause()
            fill(screen, price="130")
            await pilot.press("ctrl+s")
            await settle(pilot)
            await pilot.press("enter")
            await settle(pilot)
            assert spy.last["shares"] == Decimal("8")

    @pytest.mark.asyncio
    async def test_unchecking_it_gives_the_field_back_with_the_number_in_it(self) -> None:
        # Destravar e para vender *quase* tudo: comecar do total e apagar um
        # digito e menos trabalho do que redigitar a posicao inteira.
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_sale(pilot, shares="8")
            checkbox = screen.query_one("#sell-all", Checkbox)
            checkbox.value = True
            await pilot.pause()
            checkbox.value = False
            await pilot.pause()
            shares = screen.field("shares")
            assert shares.enabled
            assert shares.value == "8"
            assert shares.error == ""  # nem "obrigatorio" antes de digitar nada

    @pytest.mark.asyncio
    async def test_a_fractional_position_is_filled_in_a_form_the_parser_reads_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # O campo e entrada, nao exibicao: agrupar milhar (ou mascarar no modo
        # privacidade) gravaria uma venda que o parser recusa.
        spy = RecordSpy(TransactionType.SELL)
        monkeypatch.setattr(services, "record_sell", spy)
        fmt.configure(",")
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_sale(pilot, shares="1500.5")
            screen.query_one("#sell-all", Checkbox).value = True
            await pilot.pause()
            assert screen.field("shares").value == "1500.5"
            fill(screen, price="130")
            await pilot.press("ctrl+s")
            await settle(pilot)
            await pilot.press("enter")
            await settle(pilot)
            assert spy.last["shares"] == Decimal("1500.5")


class TestClosedPosition:
    """A venda que zera a posicao leva o target junto — e diz que levou."""

    async def sell_out(self, pilot: Any, screen: Any) -> None:
        fill(screen, shares="8", price="130")  # a posicao inteira (ver `sale_position`)
        await pilot.press("ctrl+s")
        await settle(pilot)
        await pilot.press("enter")  # confirma o lancamento
        await settle(pilot)

    @pytest.mark.asyncio
    async def test_the_dialog_says_what_was_removed_before_asking_where_to_go(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spy = RecordSpy(TransactionType.SELL, cleared=make_cleared_target("AUVP11", "0.3"))
        monkeypatch.setattr(services, "record_sell", spy)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_sale(pilot)
            await self.sell_out(pilot, screen)
            modal = app.screen
            assert isinstance(modal, ClearedTargetModal)
            assert "AUVP11" in modal.notice
            assert "30.00%" in modal.notice

    @pytest.mark.asyncio
    async def test_keeping_it_leaves_the_target_at_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(services, "record_sell", RecordSpy(TransactionType.SELL, cleared=make_cleared_target()))
        updates: list[dict[str, Any]] = []
        monkeypatch.setattr(services, "update_asset", lambda **kwargs: updates.append(kwargs))
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_sale(pilot)
            await self.sell_out(pilot, screen)
            await pilot.press("enter")  # "Manter assim", o botao com foco
            await settle(pilot)
            assert updates == []
            assert isinstance(app.screen, NextStepModal)  # o fluxo normal segue

    @pytest.mark.asyncio
    async def test_escape_keeps_it_too(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Esc no dialogo nao pode ser um jeito de reverter sem querer: a mudanca
        # ja esta aplicada, e sair dele e concordar com ela.
        monkeypatch.setattr(services, "record_sell", RecordSpy(TransactionType.SELL, cleared=make_cleared_target()))
        updates: list[dict[str, Any]] = []
        monkeypatch.setattr(services, "update_asset", lambda **kwargs: updates.append(kwargs))
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_sale(pilot)
            await self.sell_out(pilot, screen)
            await pilot.press("escape")
            await settle(pilot)
            assert updates == []

    @pytest.mark.asyncio
    async def test_reverting_puts_the_previous_target_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            services, "record_sell", RecordSpy(TransactionType.SELL, cleared=make_cleared_target("AUVP11", "0.3"))
        )
        updates: list[dict[str, Any]] = []
        monkeypatch.setattr(services, "update_asset", lambda **kwargs: updates.append(kwargs))
        toasts = ToastSpy()
        toasts.install(monkeypatch, TradeFormScreen)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_sale(pilot)
            await self.sell_out(pilot, screen)
            await pilot.click("#dialog-revert")
            await settle(pilot)
            assert updates == [{"ticker": "AUVP11", "target_weight": Decimal("0.3")}]
            assert toasts.severity_of("de volta em 30.00%") == "information"

    @pytest.mark.asyncio
    async def test_a_failed_revert_is_reported_and_does_not_stop_the_flow(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Restaurar o peso pode bater no limite de 100% se outro ativo cresceu
        # nesse meio tempo: o erro tem de aparecer, nao virar um silencio.
        monkeypatch.setattr(services, "record_sell", RecordSpy(TransactionType.SELL, cleared=make_cleared_target()))

        def refuse(**_: Any) -> None:
            raise WeightSumExceededError(Decimal("1.3"))

        monkeypatch.setattr(services, "update_asset", refuse)
        toasts = ToastSpy()
        toasts.install(monkeypatch, TradeFormScreen)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_sale(pilot)
            await self.sell_out(pilot, screen)
            await pilot.click("#dialog-revert")
            await settle(pilot)
            assert toasts.severity_of("ultrapassaria") == "error"
            assert isinstance(app.screen, NextStepModal)

    @pytest.mark.asyncio
    async def test_a_buy_never_touches_a_target(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # O caminho da compra nao passa por record_sell: sem esse teste, um
        # `self.cleared` mal zerado apareceria como um dialogo na compra.
        monkeypatch.setattr(services, "record_buy", RecordSpy(TransactionType.BUY))
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_form(pilot, TradeFormScreen(kind=TransactionType.BUY))
            fill(screen, ticker="AUVP11", shares="8", price="130")
            await pilot.press("ctrl+s")
            await settle(pilot)
            await pilot.press("enter")
            await settle(pilot)
            assert isinstance(app.screen, NextStepModal)


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
            assert error_of(screen, "tax") == "IR retido é obrigatório para JCP (15% retido na fonte)."

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
            assert "não se aplica" in tax.input.placeholder

            fill(screen, ticker="MXRF11", amount="80")
            await pilot.press("ctrl+s")
            await settle(pilot)
            await pilot.press("enter")
            await settle(pilot)
            assert spy.last["tax_withheld"] is None

    @pytest.mark.asyncio
    async def test_switching_to_rendimento_clears_the_jcp_error(self) -> None:
        # O campo desabilitado nao pode ficar com o erro (nem a borda vermelha)
        # do tipo anterior enquanto o placeholder diz "nao se aplica".
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_form(pilot, IncomeFormScreen())
            select = screen.query_one(Select)
            select.value = TransactionType.JCP
            await pilot.pause()
            fill(screen, ticker="PETR4", amount="200")
            await pilot.press("ctrl+s")
            await settle(pilot)
            assert error_of(screen, "tax")  # IR obrigatorio para JCP

            select.value = TransactionType.RENDIMENTO
            await pilot.pause()
            tax = screen.field("tax")
            assert tax.error == ""
            assert not tax.input.has_class("-invalid")

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
            assert modal.body == ("JCP: PETR4 em 2026-05-15\nValor bruto: 200.00\nIR retido: 30.00\nLíquido: 170.00")

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
    async def test_a_second_submit_during_the_write_does_not_record_twice(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # O worker e exclusivo, mas uma thread ja em voo termina o que comecou:
        # sem o guard, o segundo ctrl+s abria outro modal e gravava de novo.
        release = threading.Event()
        calls: list[Any] = []

        def slow(**kwargs: Any) -> Any:
            calls.append(kwargs)
            release.wait(timeout=5)
            return make_transaction(TransactionType.BUY, **kwargs)

        monkeypatch.setattr(services, "record_buy", slow)
        toasts = ToastSpy()
        toasts.install(monkeypatch, TradeFormScreen)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_form(pilot, TradeFormScreen(kind=TransactionType.BUY))
            fill(screen, ticker="PETR4", shares="1", price="30")
            await pilot.press("ctrl+s")
            await settle(pilot)
            await pilot.press("enter")  # confirma; a gravacao fica pendente
            await pilot.pause()
            assert screen.writing

            await pilot.press("ctrl+s")
            await pilot.pause()
            assert not isinstance(app.screen, ConfirmModal)  # nenhum segundo modal
            release.set()
            await settle(pilot)
            assert len(calls) == 1
            assert toasts.severity_of("gravando o lançamento") == "warning"

    @pytest.mark.asyncio
    async def test_escape_waits_while_the_entry_is_being_written(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Sair no meio da gravacao deixaria a transacao escrita sem confirmacao
        # na tela (o worker morre com a tela) e convidaria a lancar de novo.
        release = threading.Event()

        def slow(**kwargs: Any) -> Any:
            release.wait(timeout=5)
            return make_transaction(TransactionType.BUY, **kwargs)

        monkeypatch.setattr(services, "record_buy", slow)
        toasts = ToastSpy()
        toasts.install(monkeypatch, TradeFormScreen)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_form(pilot, TradeFormScreen(kind=TransactionType.BUY))
            fill(screen, ticker="PETR4", shares="1", price="30")
            await pilot.press("ctrl+s")
            await settle(pilot)
            await pilot.press("enter")  # confirma; a gravacao fica pendente
            await pilot.pause()
            assert screen.writing

            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, TradeFormScreen)  # nao saiu
            assert toasts.severity_of("gravando o lançamento") == "warning"

            release.set()
            await settle(pilot)
            assert isinstance(app.screen, NextStepModal)
            assert not screen.writing

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
