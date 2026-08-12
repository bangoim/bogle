"""Tests for the TUI's history screen (issue #75): the patrimony table, the
chart, and what the privacy mode does to a chart drawn in reais.
"""

from __future__ import annotations

from typing import Any

import pytest
from textual.widgets import Static

from bogle.format import MASK
from bogle.tui import services
from bogle.tui.screens.history import HistoryScreen
from bogle.tui.widgets.chart import LineChart
from tests.tui_fakes import (
    ToastSpy,
    make_app,
    make_history,
    open_screen,
    settle,
    stub_services,
    table_columns,
    table_rows,
)


class ChartSpy:
    """Records what was plotted, without going through plotext."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[tuple[str, list[str], list[tuple[str, list[float]]]]] = []
        self.cleared = 0
        monkeypatch.setattr(LineChart, "draw", self._draw)
        monkeypatch.setattr(LineChart, "clear", self._clear)

    def _draw(self, title: str, x_labels: Any, series: Any) -> None:
        self.calls.append((title, list(x_labels), [(name, list(values)) for name, values in series]))

    def _clear(self) -> None:
        self.cleared += 1


class ExportSpy:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return kwargs["path"]


@pytest.fixture(autouse=True)
def _services(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_services(monkeypatch)


@pytest.fixture
def chart(monkeypatch: pytest.MonkeyPatch) -> ChartSpy:
    return ChartSpy(monkeypatch)


@pytest.fixture
def export(monkeypatch: pytest.MonkeyPatch) -> ExportSpy:
    spy = ExportSpy()
    monkeypatch.setattr(services, "export_chart", spy)
    return spy


class TestTable:
    @pytest.mark.asyncio
    async def test_columns_and_rows_match_the_cli(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, HistoryScreen())
            assert table_columns(screen) == ["Data", "Patrimonio", "Variacao", "Variacao %"]
            assert table_rows(screen) == [
                ["2025-08-12", "7,000.00", "-", "-"],  # primeiro ponto: nao ha anterior
                ["2025-12-31", "7,350.00", "+350.00", "+5.00%"],
                ["2026-04-30", "7,600.00", "+250.00", "+3.40%"],
                ["2026-08-12", "7,866.20", "+266.20", "+3.50%"],
            ]

    @pytest.mark.asyncio
    async def test_the_note_says_how_the_series_was_sampled(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, HistoryScreen())
            assert screen.note == "4 pontos, amostragem mensal"

    @pytest.mark.asyncio
    async def test_excluded_tickers_are_noted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(services, "load_history", lambda **_: make_history(excluded=["TESOURO-IPCA-2035"]))
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, HistoryScreen())
            assert "patrimonio nao considera TESOURO-IPCA-2035" in screen.note


class TestWindow:
    @pytest.mark.asyncio
    async def test_t_cycles_the_windows_of_the_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        periods: list[str] = []

        def load(*, period: str, **_: Any) -> Any:
            periods.append(period)
            return make_history()

        monkeypatch.setattr(services, "load_history", load)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, HistoryScreen())
            for _ in range(5):
                await pilot.press("t")
                await settle(pilot)
            assert periods == ["12m", "2y", "5y", "10y", "all", "12m"]
            assert screen.sub_title == "historico - 12m"


class TestChart:
    @pytest.mark.asyncio
    async def test_plots_the_patrimony_over_the_grid(self, chart: ChartSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, HistoryScreen())
            title, labels, series = chart.calls[-1]
            assert title == "Evolucao do patrimonio"
            assert labels == ["2025-08-12", "2025-12-31", "2026-04-30", "2026-08-12"]
            assert series == [("Patrimonio", [7000.0, 7350.0, 7600.0, 7866.2])]


class TestHiddenAmounts:
    @pytest.mark.asyncio
    async def test_h_masks_the_money_columns(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, HistoryScreen())
            await pilot.press("h")
            await pilot.pause()
            assert table_rows(screen)[1] == ["2025-12-31", MASK, MASK, "+5.00%"]

    @pytest.mark.asyncio
    async def test_h_takes_the_chart_off_the_screen(self, chart: ChartSpy) -> None:
        # Uma curva e um valor desenhado: mascarar a tabela e deixar o eixo Y em
        # milhares na tela nao esconderia nada.
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, HistoryScreen())
            drawn = len(chart.calls)
            await pilot.press("h")
            await pilot.pause()
            assert screen.query_one(LineChart).display is False
            assert screen.query_one("#chart-hidden", Static).display is True
            assert len(chart.calls) == drawn  # nada novo desenhado
            assert chart.cleared == 1

    @pytest.mark.asyncio
    async def test_h_again_brings_the_chart_back(self, chart: ChartSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, HistoryScreen())
            await pilot.press("h")
            await pilot.pause()
            await pilot.press("h")
            await pilot.pause()
            assert screen.query_one(LineChart).display is True
            assert screen.query_one("#chart-hidden", Static).display is False
            assert table_rows(screen)[1][1] == "7,350.00"

    @pytest.mark.asyncio
    async def test_a_load_while_hidden_does_not_draw_the_chart(self, chart: ChartSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, HistoryScreen())
            await pilot.press("h")
            await pilot.pause()
            drawn = len(chart.calls)
            await pilot.press("r")
            await settle(pilot)
            assert len(chart.calls) == drawn
            assert screen.query_one(LineChart).display is False


class TestExport:
    @pytest.mark.asyncio
    async def test_o_exports_the_patrimony_in_reais(self, export: ExportSpy, monkeypatch: pytest.MonkeyPatch) -> None:
        toasts = ToastSpy()
        toasts.install(monkeypatch, HistoryScreen)
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, HistoryScreen())
            await pilot.press("o")
            await settle(pilot)
            call = export.calls[-1]
            assert call["title"] == "Evolucao do patrimonio"
            assert call["y_title"] == "R$"
            assert call["series"] == [("Patrimonio", [7000.0, 7350.0, 7600.0, 7866.2])]
            assert call["path"] == services.chart_path("history-12m")
            assert str(call["path"]) in toasts.messages[-1]

    @pytest.mark.asyncio
    async def test_hidden_amounts_block_the_export(self, export: ExportSpy, monkeypatch: pytest.MonkeyPatch) -> None:
        # Abrir o navegador com os numeros reais derrotaria o modo privacidade.
        toasts = ToastSpy()
        toasts.install(monkeypatch, HistoryScreen)
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, HistoryScreen())
            await pilot.press("h")
            await pilot.pause()
            await pilot.press("o")
            await settle(pilot)
            assert export.calls == []
            assert toasts.severity_of("valores ocultos") == "warning"
