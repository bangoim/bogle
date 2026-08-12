"""Tests for the TUI's contribution screen (issue #76): the amount, the split and
the cycle evaluation it records.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from textual.widgets import DataTable

from bogle.domain.errors import MissingPriceError, ValidationError
from bogle.format import MASK
from bogle.tui import services
from bogle.tui.screens.suggest import SuggestScreen
from bogle.tui.widgets.form import Field
from tests.tui_fakes import (
    ToastSpy,
    make_app,
    make_suggestion,
    open_screen,
    settle,
    stub_services,
    table_columns,
    table_rows,
)


class SuggestSpy:
    """Serves a suggestion and records the amounts asked for."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.amounts: list[Decimal] = []
        self.error = error

    def __call__(self, amount: Decimal, **_: Any) -> Any:
        if self.error is not None:
            raise self.error
        self.amounts.append(amount)
        return make_suggestion(amount=amount)


@pytest.fixture(autouse=True)
def _services(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_services(monkeypatch)


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> SuggestSpy:
    loader = SuggestSpy()
    monkeypatch.setattr(services, "load_suggestion", loader)
    return loader


async def ask(pilot: Any, screen: SuggestScreen, amount: str) -> None:
    field = screen.query_one("#amount", Field)
    field.set_value(amount)
    field.input.focus()
    await pilot.press("enter")
    await settle(pilot)


class TestAmount:
    @pytest.mark.asyncio
    async def test_opens_asking_for_the_amount_without_fetching(self, spy: SuggestSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            assert spy.amounts == []
            assert screen.note == "Informe o valor do aporte e pressione Enter."
            assert app.focused is screen.query_one("#amount", Field).input

    @pytest.mark.asyncio
    async def test_enter_asks_for_the_split_and_shows_the_amount_in_the_header(self, spy: SuggestSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            assert spy.amounts == [Decimal("1500")]
            assert screen.sub_title == "aporte - 1,500.00"

    @pytest.mark.asyncio
    async def test_an_invalid_amount_never_reaches_the_service(self, spy: SuggestSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "0")
            assert spy.amounts == []
            assert screen.query_one("#amount", Field).error == "Valor do aporte deve ser maior que zero, recebido 0."

    @pytest.mark.asyncio
    async def test_r_recalculates_the_same_amount(self, spy: SuggestSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            screen.query_one(DataTable).focus()  # sai do campo para o atalho valer
            await pilot.press("r")
            await settle(pilot)
            assert spy.amounts == [Decimal("1500"), Decimal("1500")]


class TestSplit:
    @pytest.mark.asyncio
    async def test_columns_and_rows_match_the_command(self, spy: SuggestSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            assert table_columns(screen) == [
                "Ticker",
                "Preco",
                "Valor sugerido",
                "Qtde papeis",
                "Custo efetivo",
                "Peso apos aporte",
            ]
            assert table_rows(screen)[0] == ["AUVP11", "126.25", "1,010.00", "8", "1,010.00", "28.40%"]

    @pytest.mark.asyncio
    async def test_fixed_income_has_no_share_count(self, spy: SuggestSpy) -> None:
        # Renda fixa entra por valor, nao por cota inteira.
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            assert table_rows(screen)[1][3] == "-"

    @pytest.mark.asyncio
    async def test_the_totals_close_the_account(self, spy: SuggestSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            assert "Total alocado 1,499.50" in screen.totals
            assert "Aporte 1,500.00" in screen.totals
            assert "Sobra (caixa) 0.50" in screen.totals

    @pytest.mark.asyncio
    async def test_warnings_from_the_engine_are_shown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        warning = "Aporte em renda fixa privada (CDB-XP-2027) cria um novo contrato"
        monkeypatch.setattr(
            services, "load_suggestion", lambda amount, **_: make_suggestion(amount=amount, warnings=[warning])
        )
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            assert warning in screen.note

    @pytest.mark.asyncio
    async def test_the_note_says_the_cycle_was_evaluated(self, spy: SuggestSpy) -> None:
        # Sugerir aporte e a avaliacao do ciclo (issue #24), como no comando.
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            assert "conta como avaliacao do ciclo" in screen.note


class TestHiddenAmounts:
    @pytest.mark.asyncio
    async def test_h_also_takes_the_amount_out_of_the_header(self, spy: SuggestSpy) -> None:
        # O subtitulo carrega o valor do aporte: mascarar a tabela e deixa-lo no
        # cabecalho esconderia o aporte no lugar menos visivel da tela.
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            assert screen.sub_title == "aporte - 1,500.00"
            screen.query_one(DataTable).focus()
            await pilot.press("h")
            await pilot.pause()
            assert screen.sub_title == f"aporte - {MASK}"

    @pytest.mark.asyncio
    async def test_h_masks_the_amounts_but_keeps_the_weights(self, spy: SuggestSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            screen.query_one(DataTable).focus()
            await pilot.press("h")
            await pilot.pause()
            assert table_rows(screen)[0] == ["AUVP11", MASK, MASK, MASK, MASK, "28.40%"]
            assert f"Total alocado {MASK}" in screen.totals


class TestFailures:
    @pytest.mark.asyncio
    async def test_an_unpriced_portfolio_is_reported_and_keeps_the_screen(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(services, "load_suggestion", SuggestSpy(error=MissingPriceError(["CDB01"])))
        toasts = ToastSpy()
        toasts.install(monkeypatch, SuggestScreen)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            assert isinstance(app.screen, SuggestScreen)
            assert "Sem preco atual para: CDB01" in screen.note
            assert toasts.severity_of("Sem preco atual") == "error"

    @pytest.mark.asyncio
    async def test_an_empty_portfolio_says_so(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            services,
            "load_suggestion",
            SuggestSpy(error=ValidationError("Nenhuma posicao ativa para sugerir aporte.")),
        )
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            assert screen.note == "Nenhuma posicao ativa para sugerir aporte."
            assert table_rows(screen) == []
