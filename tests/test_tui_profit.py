"""Tests for the TUI's profit screen (issue #75): the decomposition panel and
the income window.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from bogle.format import MASK
from bogle.tui import services
from bogle.tui.screens.profit import ProfitScreen
from tests.tui_fakes import make_app, make_profit, open_screen, settle, stub_services


@pytest.fixture(autouse=True)
def _services(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_services(monkeypatch)


class TestPanel:
    @pytest.mark.asyncio
    async def test_carries_the_same_figures_as_the_command(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ProfitScreen())
            assert "Lucro da carteira desde 2024-03-01" in screen.panel
            assert "Ganho de capital        +516.20" in screen.panel
            assert "  Realizado (vendas)    +120.50" in screen.panel
            assert "  Nao realizado         +395.70" in screen.panel
            assert "Proventos recebidos     +144.90" in screen.panel
            assert "  Dividendos            +45.50" in screen.panel
            assert "  JCP (liquido)         +17.00" in screen.panel
            assert "  FII rendimentos       +82.40" in screen.panel
            assert "  Renda fixa juros      +0.00" in screen.panel
            assert "Lucro total             +661.10" in screen.panel

    @pytest.mark.asyncio
    async def test_unpriced_tickers_are_noted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(services, "load_profit", lambda **_: make_profit(unpriced=["CDB-XP-2027"]))
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ProfitScreen())
            assert "ganho nao realizado nao considera CDB-XP-2027" in screen.note

    @pytest.mark.asyncio
    async def test_a_loss_keeps_its_sign(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            services,
            "load_profit",
            lambda **_: make_profit(realized=Decimal("-40"), unrealized=Decimal("-100.10")),
        )
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ProfitScreen())
            assert "Ganho de capital        -140.10" in screen.panel


class TestIncomeWindow:
    @pytest.mark.asyncio
    async def test_t_switches_the_window_and_says_which(self, monkeypatch: pytest.MonkeyPatch) -> None:
        periods: list[str] = []

        def load(*, period: str, **_: Any) -> Any:
            periods.append(period)
            return make_profit()

        monkeypatch.setattr(services, "load_profit", load)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ProfitScreen())
            assert screen.sub_title == "lucro - proventos all"
            await pilot.press("t")
            await settle(pilot)
            assert periods == ["all", "12m"]
            # O subtitulo qualifica a janela: o ganho de capital nao e de 12 meses.
            assert screen.sub_title == "lucro - proventos 12m"

    @pytest.mark.asyncio
    async def test_the_12m_view_omits_the_total_and_explains_why(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ProfitScreen())
            await pilot.press("t")
            await settle(pilot)
            assert "Proventos (12m)" in screen.panel
            # Somar ganho desde o inicio com proventos de 12 meses seria somar
            # duas janelas: o total sai, e a linha diz por que.
            assert "+661.10" not in screen.panel
            assert "Lucro total omitido: ganho de capital e desde o inicio" in screen.panel


class TestHiddenAmounts:
    @pytest.mark.asyncio
    async def test_h_masks_every_amount_in_the_panel(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ProfitScreen())
            await pilot.press("h")
            await pilot.pause()
            assert f"Ganho de capital        {MASK}" in screen.panel
            assert "516.20" not in screen.panel

    @pytest.mark.asyncio
    async def test_h_again_brings_them_back(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ProfitScreen())
            await pilot.press("h")
            await pilot.pause()
            await pilot.press("h")
            await pilot.pause()
            assert "Ganho de capital        +516.20" in screen.panel
