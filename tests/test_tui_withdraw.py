"""Tests for the TUI's withdrawal screen (issue #80): the amount, the sales and the
totals — the same as ``bogle withdraw``, and nothing written.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from textual.widgets import DataTable

from bogle.domain.errors import MissingPriceError
from bogle.format import MASK
from bogle.rebalancing import WithdrawalSuggestion
from bogle.tui import services
from bogle.tui.screens.home import HomeScreen
from bogle.tui.screens.withdraw import WithdrawScreen
from bogle.tui.widgets.form import Field
from tests.tui_fakes import make_app, make_withdrawal, open_screen, settle, stub_services, table_columns, table_rows


class WithdrawSpy:
    """Serves a withdrawal and records what was asked for."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls: list[tuple[Decimal, dict[str, Any]]] = []
        self.error = error

    def __call__(self, amount: Decimal, **kwargs: Any) -> WithdrawalSuggestion:
        if self.error is not None:
            raise self.error
        self.calls.append((amount, kwargs))
        return make_withdrawal(amount=amount)

    @property
    def amounts(self) -> list[Decimal]:
        return [amount for amount, _ in self.calls]


@pytest.fixture(autouse=True)
def _services(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_services(monkeypatch)


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> WithdrawSpy:
    loader = WithdrawSpy()
    monkeypatch.setattr(services, "load_withdrawal", loader)
    return loader


async def ask(pilot: Any, screen: WithdrawScreen, amount: str) -> None:
    field = screen.query_one("#withdraw-amount", Field)
    field.set_value(amount)
    field.input.focus()
    await pilot.press("enter")
    await settle(pilot)


class TestAmount:
    @pytest.mark.asyncio
    async def test_opens_asking_for_the_amount_without_fetching(self, spy: WithdrawSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, WithdrawScreen())
            assert spy.amounts == []
            assert screen.note == "Informe o valor bruto do saque e pressione Enter."
            assert app.focused is screen.query_one("#withdraw-amount", Field).input
            assert screen.sub_title == "saque"

    @pytest.mark.asyncio
    async def test_enter_asks_for_the_sales(self, spy: WithdrawSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, WithdrawScreen())
            await ask(pilot, screen, "5000")
            assert spy.amounts == [Decimal("5000")]
            # Com a sugestao na tela o foco vai para a tabela, onde os atalhos valem.
            assert app.focused is screen.query_one(DataTable)

    @pytest.mark.asyncio
    async def test_an_invalid_amount_never_reaches_the_service(self, spy: WithdrawSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, WithdrawScreen())
            await ask(pilot, screen, "0")
            assert spy.amounts == []
            assert screen.query_one("#withdraw-amount", Field).error is not None

    @pytest.mark.asyncio
    async def test_r_asks_again_with_fresh_quotes(self, spy: WithdrawSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, WithdrawScreen())
            await ask(pilot, screen, "5000")
            await pilot.press("r")
            await settle(pilot)
            assert spy.amounts == [Decimal("5000"), Decimal("5000")]
            assert spy.calls[-1][1] == {"refresh": True}


class TestSales:
    @pytest.mark.asyncio
    async def test_columns_and_rows_match_the_command(self, spy: WithdrawSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, WithdrawScreen())
            await ask(pilot, screen, "5000")
            assert table_columns(screen) == [
                "Ticker",
                "Preço",
                "Qtde",
                "Bruto",
                "IR (est.)",
                "Líquido",
                "Target",
                "Peso atual",
                "Peso após",
            ]
            assert table_rows(screen) == [
                ["NB1011", "98.40", "46", "4,526.40", "120.96", "4,405.44", "10.00%", "15.20%", "12.80%"],
                ["CDB-XP-2027", "811.20", "-", "500.00", "15.30", "484.70", "10.00%", "11.00%", "10.50%"],
            ]

    @pytest.mark.asyncio
    async def test_totals_compare_the_net_with_the_amount(self, spy: WithdrawSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, WithdrawScreen())
            await ask(pilot, screen, "5000")
            first = screen.totals.splitlines()[0]
            assert first == (
                "Bruto 5,026.40   IR (est.) 136.26   Líquido 4,890.14   "
                "Resíduo (cotas inteiras) 26.40   Líquido vs saque -109.86"
            )
            assert "Fonte(s) brapi, calculado" in screen.totals

    @pytest.mark.asyncio
    async def test_the_warnings_are_numbered_under_the_table(self, spy: WithdrawSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, WithdrawScreen())
            await ask(pilot, screen, "5000")
            assert screen.note == "Atenção:\n1. NB1011 em saída na trajetória: vendido primeiro"

    @pytest.mark.asyncio
    async def test_h_hides_the_amounts(self, spy: WithdrawSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, WithdrawScreen())
            await ask(pilot, screen, "5000")
            await pilot.press("h")
            await settle(pilot)
            nb = table_rows(screen)[0]
            assert nb[3] == MASK
            assert "4,526.40" not in screen.totals

    @pytest.mark.asyncio
    async def test_a_failure_clears_the_table_and_says_why(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(services, "load_withdrawal", WithdrawSpy(error=MissingPriceError(["NB1011"])))
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, WithdrawScreen())
            await ask(pilot, screen, "5000")
            assert table_rows(screen) == []
            assert "Sem preço atual para: NB1011" in screen.note
            assert screen.totals == ""


class TestMenu:
    @pytest.mark.asyncio
    async def test_eight_opens_the_withdrawal_from_the_home(self, spy: WithdrawSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            assert isinstance(app.screen, HomeScreen)
            await pilot.press("8")
            await settle(pilot)
            assert isinstance(app.screen, WithdrawScreen)
