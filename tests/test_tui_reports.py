"""Tests for the reports submenu and the flow every report screen inherits
(issue #75): loading state, failure handling, refresh and the cancellation guard.
"""

from __future__ import annotations

import threading
from decimal import Decimal
from typing import Any

import pytest
from textual.widgets import DataTable

from bogle.domain.errors import QuoteNotFoundError, ValidationError
from bogle.reports.compare import CompareSeries
from bogle.tui import services
from bogle.tui.screens.compare import CompareScreen
from bogle.tui.screens.history import HistoryScreen
from bogle.tui.screens.home import HomeScreen
from bogle.tui.screens.income import IncomeScreen
from bogle.tui.screens.profit import ProfitScreen
from bogle.tui.screens.reports import MENU_ITEMS, ReportsScreen
from bogle.tui.screens.returns import ReturnsScreen
from tests.tui_fakes import (
    ToastSpy,
    make_app,
    make_compare,
    open_screen,
    settle,
    stub_services,
    table_rows,
)


@pytest.fixture(autouse=True)
def _services(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_services(monkeypatch)


class CompareSpy:
    """Serves a compare report and records the window each load asked for."""

    def __init__(self) -> None:
        self.report = make_compare()
        self.periods: list[str] = []

    def __call__(self, *, period: str, **_: Any) -> Any:
        self.periods.append(period)
        return self.report


class TestSubmenu:
    def test_lists_the_five_reports_of_the_cli(self) -> None:
        assert [(item.key, item.id, item.label) for item in MENU_ITEMS] == [
            ("1", "returns", "Rentabilidade"),
            ("2", "compare", "Comparar"),
            ("3", "history", "Historico"),
            ("4", "profit", "Lucro"),
            ("5", "income", "Proventos"),
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("key", "screen_class"),
        [
            ("1", ReturnsScreen),
            ("2", CompareScreen),
            ("3", HistoryScreen),
            ("4", ProfitScreen),
            ("5", IncomeScreen),
        ],
    )
    async def test_each_number_opens_its_screen(self, key: str, screen_class: type) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, ReportsScreen())
            await pilot.press(key)
            await settle(pilot)
            assert isinstance(app.screen, screen_class)

    @pytest.mark.asyncio
    async def test_enter_opens_the_highlighted_report(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, ReportsScreen())
            await pilot.press("down", "enter")  # segundo item: comparar
            await settle(pilot)
            assert isinstance(app.screen, CompareScreen)

    @pytest.mark.asyncio
    async def test_reachable_from_the_home_menu(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            await pilot.press("5")
            await settle(pilot)
            assert isinstance(app.screen, ReportsScreen)

    @pytest.mark.asyncio
    async def test_escape_goes_back_to_home(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, ReportsScreen())
            await pilot.press("escape")
            await settle(pilot)
            assert isinstance(app.screen, HomeScreen)


class TestLoadingFlow:
    @pytest.mark.asyncio
    async def test_table_shows_its_loading_state_while_fetching(self, monkeypatch: pytest.MonkeyPatch) -> None:
        release = threading.Event()

        def slow(**_: Any) -> Any:
            release.wait(timeout=5)
            return make_compare()

        monkeypatch.setattr(services, "load_compare", slow)
        app = make_app()
        async with app.run_test() as pilot:
            screen = CompareScreen()
            await pilot.app.push_screen(screen)
            await pilot.pause()
            assert screen.query_one(DataTable).loading is True
            release.set()
            await settle(pilot)
            assert screen.query_one(DataTable).loading is False

    @pytest.mark.asyncio
    async def test_r_refetches_the_same_window(self, monkeypatch: pytest.MonkeyPatch) -> None:
        spy = CompareSpy()
        monkeypatch.setattr(services, "load_compare", spy)
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, CompareScreen())
            await pilot.press("r")
            await settle(pilot)
            assert spy.periods == ["12m", "12m"]

    @pytest.mark.asyncio
    async def test_a_replaced_load_cannot_overwrite_the_newer_view(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Trocar de janela enquanto a carga anterior ainda roda: a thread lenta
        # nao para, e sem o guard de cancelamento ela sobrescreveria a tabela nova.
        release = threading.Event()
        slow = make_compare()  # carteira + IBOV
        fast = make_compare(series=[CompareSeries("Carteira", [Decimal("100"), Decimal("103")])])

        def load(*, period: str, **_: Any) -> Any:
            if period == "12m":
                release.wait(timeout=5)
                return slow
            return fast

        monkeypatch.setattr(services, "load_compare", load)
        app = make_app()
        async with app.run_test() as pilot:
            screen = CompareScreen()
            await pilot.app.push_screen(screen)
            await pilot.pause()
            await pilot.press("t")  # 12m -> 2y: cancela a carga lenta
            await settle(pilot)
            assert [row[0] for row in table_rows(screen)] == ["Carteira"]

            release.set()  # a carga cancelada termina agora
            await settle(pilot)
            assert [row[0] for row in table_rows(screen)] == ["Carteira"]
            assert screen.sub_title == "comparar - 2y"


class TestFailures:
    @pytest.mark.asyncio
    async def test_failure_keeps_the_screen_and_explains(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(**_: Any) -> Any:
            raise QuoteNotFoundError("PETR4", provider="brapi")

        monkeypatch.setattr(services, "load_compare", boom)
        toasts = ToastSpy()
        toasts.install(monkeypatch, CompareScreen)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, CompareScreen())
            assert screen.query_one(DataTable).row_count == 0
            assert screen.query_one(DataTable).loading is False
            assert "PETR4" in screen.note
            assert toasts.severity_of("PETR4") == "error"

    @pytest.mark.asyncio
    async def test_a_portfolio_without_history_is_a_message_not_a_crash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # compute_compare recusa uma carteira sem serie de precos; na CLI isso e
        # um erro na saida, aqui e uma nota na tela.
        def boom(**_: Any) -> Any:
            raise ValidationError("Nenhuma posicao com historico de precos para comparar.")

        monkeypatch.setattr(services, "load_compare", boom)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, CompareScreen())
            assert screen.note == "Nenhuma posicao com historico de precos para comparar."

    @pytest.mark.asyncio
    async def test_a_failure_leaves_nothing_to_redraw(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # O toggle de privacidade redesenha do relatorio guardado; depois de uma
        # falha nao existe relatorio, e a tela nao pode ressuscitar o anterior.
        reports = [make_compare(), None]

        def load(**_: Any) -> Any:
            report = reports.pop(0)
            if report is None:
                raise QuoteNotFoundError("PETR4", provider="brapi")
            return report

        monkeypatch.setattr(services, "load_compare", load)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, CompareScreen())
            assert screen.query_one(DataTable).row_count == 2
            await pilot.press("r")
            await settle(pilot)
            await pilot.press("h")
            await pilot.pause()
            assert screen.query_one(DataTable).row_count == 0
