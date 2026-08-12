"""Tests for the TUI's Home screen (issue #73): the D-1 summary, the navigation
and how an expected failure is reported.
"""

from __future__ import annotations

import threading
from decimal import Decimal
from typing import Any

import psycopg
import pytest

from bogle.domain.errors import MarketDataError
from bogle.tui import services
from bogle.tui.screens.home import HomeScreen
from bogle.tui.screens.position import PositionScreen
from bogle.tui.screens.register import RegisterScreen
from bogle.tui.widgets.menu import Menu
from bogle.tui.widgets.metric import PLACEHOLDER, Metric
from tests.tui_fakes import (
    ToastSpy,
    empty_overview,
    make_app,
    make_overview,
    settle,
    stub_services,
)


@pytest.fixture(autouse=True)
def _services(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_services(monkeypatch)


def use_overview(monkeypatch: pytest.MonkeyPatch, overview: Any) -> None:
    monkeypatch.setattr(services, "load_overview", lambda **_: overview)


def metric(screen: HomeScreen, metric_id: str) -> str:
    return screen.query_one(f"#{metric_id}", Metric).value


class TestSummary:
    @pytest.mark.asyncio
    async def test_shows_the_four_headline_numbers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        use_overview(monkeypatch, make_overview())
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            home = app.screen
            assert isinstance(home, HomeScreen)
            assert metric(home, "patrimony") == "7866.20"
            assert metric(home, "variation") == "+516.20  (+7.02%)"
            assert metric(home, "twr-12m") == "+12.75%"
            assert metric(home, "twr-total") == "+18.40%"

    @pytest.mark.asyncio
    async def test_reference_close_is_in_the_panel_title(self, monkeypatch: pytest.MonkeyPatch) -> None:
        use_overview(monkeypatch, make_overview())
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            assert app.screen.query_one("#summary").border_title == "Carteira - fechamento de 2026-08-11"

    @pytest.mark.asyncio
    async def test_starts_with_a_placeholder_before_the_worker_answers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        release = threading.Event()

        def slow(**_: Any) -> Any:
            release.wait(timeout=5)
            return make_overview()

        monkeypatch.setattr(services, "load_overview", slow)
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert metric(app.screen, "patrimony") == PLACEHOLDER  # type: ignore[arg-type]
            release.set()
            await settle(pilot)
            assert metric(app.screen, "patrimony") == "7866.20"  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_twr_legend_is_shown_when_everything_is_priced(self, monkeypatch: pytest.MonkeyPatch) -> None:
        use_overview(monkeypatch, make_overview())
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            assert "TWR" in app.screen.note  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_empty_ledger_says_so(self, monkeypatch: pytest.MonkeyPatch) -> None:
        use_overview(monkeypatch, empty_overview())
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            home = app.screen
            assert isinstance(home, HomeScreen)
            assert home.note == "Nenhuma transacao registrada ainda."
            assert metric(home, "patrimony") == "-"
            assert metric(home, "variation") == "-"

    @pytest.mark.asyncio
    async def test_excluded_tickers_are_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        use_overview(monkeypatch, make_overview(excluded=["TESOURO-IPCA-2035"]))
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            note = app.screen.note  # type: ignore[attr-defined]
            assert "TESOURO-IPCA-2035" in note
            assert "sem historico de precos" in note

    @pytest.mark.asyncio
    async def test_variation_without_a_base_drops_the_percentage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Investido <= 0 (vendas devolveram mais do que entrou): a porcentagem
        # nao tem base, mas o valor em R$ continua valendo.
        use_overview(monkeypatch, make_overview(invested=Decimal("-100"), patrimony=Decimal("50")))
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            assert metric(app.screen, "variation") == "+150.00"  # type: ignore[arg-type]


class TestNavigation:
    @pytest.mark.asyncio
    async def test_number_opens_the_position_screen_and_escape_comes_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        use_overview(monkeypatch, make_overview())
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            await pilot.press("1")
            await pilot.pause()
            assert isinstance(app.screen, PositionScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, HomeScreen)

    @pytest.mark.asyncio
    async def test_enter_on_the_menu_opens_the_highlighted_item(self, monkeypatch: pytest.MonkeyPatch) -> None:
        use_overview(monkeypatch, make_overview())
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            assert app.screen.query_one(Menu).has_focus
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, PositionScreen)

    @pytest.mark.asyncio
    async def test_arrows_move_the_highlight(self, monkeypatch: pytest.MonkeyPatch) -> None:
        use_overview(monkeypatch, make_overview())
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            await pilot.press("down", "enter")  # segundo item: Registrar
            await settle(pilot)
            assert isinstance(app.screen, RegisterScreen)

    @pytest.mark.asyncio
    async def test_q_quits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        use_overview(monkeypatch, make_overview())
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            await pilot.press("q")
            await pilot.pause()
        assert app.return_value is None  # saiu sem erro

    @pytest.mark.asyncio
    async def test_coming_back_refreshes_the_summary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        loads = []

        def load(**_: Any) -> Any:
            loads.append(1)
            return make_overview()

        monkeypatch.setattr(services, "load_overview", load)
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            assert len(loads) == 1
            await pilot.press("1")
            await pilot.pause()
            await pilot.press("escape")
            await settle(pilot)
            assert len(loads) == 2  # um lancamento novo nao pode deixar o resumo velho


class TestFailures:
    @pytest.mark.asyncio
    async def test_provider_failure_becomes_a_toast_and_an_inline_note(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(**_: Any) -> Any:
            raise MarketDataError("Falha de rede ao acessar yfinance.", provider="yfinance")

        monkeypatch.setattr(services, "load_overview", boom)
        toasts = ToastSpy()
        toasts.install(monkeypatch, HomeScreen)
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            home = app.screen
            assert isinstance(home, HomeScreen)
            assert home.note == "Falha de rede ao acessar yfinance."
            assert metric(home, "patrimony") == "-"
            assert toasts.severity_of("yfinance") == "error"

    @pytest.mark.asyncio
    async def test_database_down_shows_the_same_hint_as_the_cli(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(**_: Any) -> Any:
            raise psycopg.OperationalError("connection refused")

        monkeypatch.setattr(services, "load_overview", boom)
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            assert "nao foi possivel conectar ao banco de dados" in app.screen.note  # type: ignore[attr-defined]


class TestRebalanceReminder:
    @pytest.mark.asyncio
    async def test_overdue_cycle_is_a_warning_toast(self, monkeypatch: pytest.MonkeyPatch) -> None:
        use_overview(monkeypatch, make_overview())
        monkeypatch.setattr(services, "rebalance_notice", lambda **_: "ciclo vencido desde 2026-07-01.")
        toasts = ToastSpy()
        toasts.install(monkeypatch, HomeScreen)
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            assert toasts.messages == ["ciclo vencido desde 2026-07-01."]
            assert toasts.severity_of("ciclo vencido") == "warning"

    @pytest.mark.asyncio
    async def test_nothing_due_stays_quiet(self, monkeypatch: pytest.MonkeyPatch) -> None:
        use_overview(monkeypatch, make_overview())
        toasts = ToastSpy()
        toasts.install(monkeypatch, HomeScreen)
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            assert toasts.calls == []
