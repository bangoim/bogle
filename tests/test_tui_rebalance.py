"""Tests for the TUI's annual rebalance screen (issue #81): the plan, the totals and
the key that records the year — the same as ``bogle rebalance`` and its ``--done``.
"""

from __future__ import annotations

from typing import Any

import pytest
from textual.widgets import DataTable

from bogle.domain.errors import MissingPriceError, ValidationError
from bogle.format import MASK
from bogle.rebalancing import AnnualRebalance
from bogle.tui import services
from bogle.tui.screens.home import HomeScreen
from bogle.tui.screens.rebalance import RebalanceScreen
from tests.tui_fakes import (
    ToastSpy,
    make_annual_rebalance,
    make_app,
    open_screen,
    settle,
    stub_services,
    table_columns,
    table_rows,
)


class PlanSpy:
    """Serves a plan and records how it was asked for."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.error = error

    def __call__(self, **kwargs: Any) -> AnnualRebalance:
        if self.error is not None:
            raise self.error
        self.calls.append(kwargs)
        return make_annual_rebalance()


@pytest.fixture(autouse=True)
def _services(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_services(monkeypatch)


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> PlanSpy:
    loader = PlanSpy()
    monkeypatch.setattr(services, "load_annual_rebalance", loader)
    return loader


class TestPlan:
    @pytest.mark.asyncio
    async def test_opens_on_the_plan_of_the_year(self, spy: PlanSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, RebalanceScreen())
            assert len(spy.calls) == 1
            assert screen.sub_title == "rebalanceamento anual de 2061 (tolerância 5.00%)"
            assert app.focused is screen.query_one(DataTable)

    @pytest.mark.asyncio
    async def test_columns_and_rows_match_the_command(self, spy: PlanSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, RebalanceScreen())
            assert table_columns(screen) == [
                "Ticker",
                "Operação",
                "Preço",
                "Qtde",
                "Valor",
                "IR (est.)",
                "Target",
                "Peso atual",
                "Peso após",
            ]
            assert table_rows(screen) == [
                ["NB1011", "Vender", "98.40", "46", "4,526.40", "120.96", "10.00%", "15.20%", "10.00%"],
                ["CDB-XP-2027", "Vender", "811.20", "-", "500.00", "15.30", "10.00%", "16.00%", "15.00%"],
                ["B5P211", "Comprar", "101.50", "47", "4,770.50", "-", "50.00%", "43.80%", "48.70%"],
                ["MUND11", "Manter", "120.00", "-", "-", "-", "30.00%", "25.00%", "26.30%"],
            ]

    @pytest.mark.asyncio
    async def test_totals_follow_the_money(self, spy: PlanSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, RebalanceScreen())
            first = screen.totals.splitlines()[0]
            assert first == (
                "Vendas 5,026.40   IR (est.) 136.26   Para compras 4,890.14   "
                "Compras 4,770.50   Taxa B3 (est.) 1.53   Sobra (caixa) 118.11"
            )
            assert "Fonte(s) brapi, calculado" in screen.totals

    @pytest.mark.asyncio
    async def test_the_warnings_are_numbered_under_the_table(self, spy: PlanSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, RebalanceScreen())
            assert screen.note == "Atenção:\n1. NB1011 em saída na trajetória: vendido até o alvo"

    @pytest.mark.asyncio
    async def test_r_asks_again_with_fresh_quotes(self, spy: PlanSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, RebalanceScreen())
            await pilot.press("r")
            await settle(pilot)
            assert spy.calls == [{"refresh": False}, {"refresh": True}]

    @pytest.mark.asyncio
    async def test_h_hides_the_amounts(self, spy: PlanSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, RebalanceScreen())
            await pilot.press("h")
            await settle(pilot)
            assert table_rows(screen)[0][4] == MASK
            assert "4,526.40" not in screen.totals


class TestFailures:
    @pytest.mark.asyncio
    async def test_outside_the_phase_it_says_why(self, monkeypatch: pytest.MonkeyPatch) -> None:
        refusal = ValidationError("Antes da aposentadoria: o rebalanceamento anual começa em 2061.")
        monkeypatch.setattr(services, "load_annual_rebalance", PlanSpy(error=refusal))
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, RebalanceScreen())
            assert table_rows(screen) == []
            assert screen.note == "Antes da aposentadoria: o rebalanceamento anual começa em 2061."
            assert screen.totals == ""

    @pytest.mark.asyncio
    async def test_a_missing_price_clears_the_table(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(services, "load_annual_rebalance", PlanSpy(error=MissingPriceError(["NB1011"])))
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, RebalanceScreen())
            assert "Sem preço atual para: NB1011" in screen.note


class TestDone:
    @pytest.mark.asyncio
    async def test_f_records_the_year(self, spy: PlanSpy, monkeypatch: pytest.MonkeyPatch) -> None:
        recorded: list[int] = []

        def record(**_: Any) -> int:
            recorded.append(2061)
            return 2061

        monkeypatch.setattr(services, "record_annual_rebalance", record)
        toasts = ToastSpy()
        toasts.install(monkeypatch, RebalanceScreen)
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, RebalanceScreen())
            assert recorded == []  # ver o plano nao conta como feito
            await pilot.press("f")
            await settle(pilot)
            assert recorded == [2061]
            assert toasts.messages == ["Rebalanceamento anual de 2061 registrado."]

    @pytest.mark.asyncio
    async def test_a_refusal_is_an_error_toast(self, spy: PlanSpy, monkeypatch: pytest.MonkeyPatch) -> None:
        def refuse(**_: Any) -> int:
            raise ValidationError("Antes da aposentadoria: o rebalanceamento anual começa em 2061.")

        monkeypatch.setattr(services, "record_annual_rebalance", refuse)
        toasts = ToastSpy()
        toasts.install(monkeypatch, RebalanceScreen)
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, RebalanceScreen())
            await pilot.press("f")
            await settle(pilot)
            assert toasts.severity_of("Antes da aposentadoria") == "error"


class TestMenu:
    @pytest.mark.asyncio
    async def test_nine_opens_the_rebalance_from_the_home(self, spy: PlanSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            assert isinstance(app.screen, HomeScreen)
            await pilot.press("9")
            await settle(pilot)
            assert isinstance(app.screen, RebalanceScreen)
