"""Tests for the TUI's comparison screen (issue #75): the series table, the
window and index selectors, the embedded chart and the HTML export.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from bogle.domain.errors import ValidationError
from bogle.reports.compare import CompareSeries
from bogle.tui import services
from bogle.tui.screens.compare import CompareScreen
from bogle.tui.widgets.chart import LineChart
from bogle.tui.widgets.indices import IndicesInput
from tests.tui_fakes import (
    ToastSpy,
    make_app,
    make_compare,
    open_screen,
    settle,
    stub_services,
    table_rows,
)


class ChartSpy:
    """Records what was plotted, without going through plotext."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[tuple[str, list[str], list[tuple[str, list[float]]]]] = []
        self.cleared = 0
        monkeypatch.setattr(LineChart, "draw", self._draw)
        monkeypatch.setattr(LineChart, "clear", self._clear)

    # Metodos ligados a este spy: o textual chama `chart.draw(...)`, e o widget
    # nao chega como primeiro argumento porque o atributo da classe ja e um bound
    # method deste objeto.
    def _draw(self, title: str, x_labels: Any, series: Any) -> None:
        self.calls.append((title, list(x_labels), [(name, list(values)) for name, values in series]))

    def _clear(self) -> None:
        self.cleared += 1


class ExportSpy:
    """Records the export calls (no file written, no browser opened)."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.error = error

    def __call__(self, **kwargs: Any) -> Path:
        if self.error is not None:
            raise self.error
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
    async def test_one_row_per_series_with_its_accumulated_return(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, CompareScreen())
            assert table_rows(screen) == [["Carteira", "+12.75%"], ["IBOV", "+5.00%"]]

    @pytest.mark.asyncio
    async def test_the_note_carries_the_window_and_the_data_date(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, CompareScreen())
            assert "Janela 2025-08-12 a 2026-08-12 (base 100 no inicio)" in screen.note
            assert "Dados ate 2026-08-11" in screen.note

    @pytest.mark.asyncio
    async def test_excluded_tickers_and_index_failures_are_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            services,
            "load_compare",
            lambda **_: make_compare(excluded=["TESOURO-IPCA-2035"], index_errors={"IPCA": "sem serie"}),
        )
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, CompareScreen())
            assert "nao considera TESOURO-IPCA-2035" in screen.note
            assert "IPCA: sem serie" in screen.note


class TestWindow:
    @pytest.mark.asyncio
    async def test_t_cycles_the_windows_of_the_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        periods: list[str] = []

        def load(*, period: str, **_: Any) -> Any:
            periods.append(period)
            return make_compare()

        monkeypatch.setattr(services, "load_compare", load)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, CompareScreen())
            for _ in range(6):
                await pilot.press("t")
                await settle(pilot)
            assert periods == ["12m", "2y", "5y", "10y", "ytd", "all", "12m"]
            assert screen.sub_title == "comparar - 12m"


class TestChart:
    @pytest.mark.asyncio
    async def test_plots_every_series_on_the_grid(self, chart: ChartSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, CompareScreen())
            title, labels, series = chart.calls[-1]
            assert title == "Base 100 no inicio do periodo"
            assert labels == ["2025-08-12", "2025-12-31", "2026-04-30", "2026-08-12"]
            assert [name for name, _ in series] == ["Carteira", "IBOV"]
            assert series[0][1] == [100.0, 104.0, 109.5, 112.75]

    @pytest.mark.asyncio
    async def test_base_100_stays_visible_while_amounts_are_hidden(self, chart: ChartSpy) -> None:
        # Base 100 e um nivel de retorno, nao um valor em reais: nao ha o que ocultar.
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, CompareScreen())
            await pilot.press("h")
            await pilot.pause()
            assert screen.query_one(LineChart).display is True
            assert table_rows(screen) == [["Carteira", "+12.75%"], ["IBOV", "+5.00%"]]


class TestExport:
    @pytest.mark.asyncio
    async def test_o_writes_the_accumulated_return_and_says_where(
        self, export: ExportSpy, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        toasts = ToastSpy()
        toasts.install(monkeypatch, CompareScreen)
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, CompareScreen())
            await pilot.press("o")
            await settle(pilot)
            call = export.calls[-1]
            assert call["title"] == "Carteira v. Índices"
            assert call["y_suffix"] == "%"
            # Base 100 -> retorno acumulado com baseline zero, como no --output.
            assert call["series"][0] == ("Carteira", [0.0, 4.0, 9.5, 12.75])
            assert call["path"] == services.chart_path("compare-12m")
            assert str(services.chart_path("compare-12m")) in toasts.messages[-1]

    @pytest.mark.asyncio
    async def test_the_exported_file_follows_the_window(self, export: ExportSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, CompareScreen())
            await pilot.press("t")  # 2y
            await settle(pilot)
            await pilot.press("o")
            await settle(pilot)
            assert export.calls[-1]["path"] == services.chart_path("compare-2y")

    @pytest.mark.asyncio
    async def test_nothing_loaded_is_a_warning_not_an_export(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Depois de uma falha de carga nao existe relatorio: exportar tem de avisar,
        # nao estourar num `None`.
        def boom(**_: Any) -> Any:
            raise ValidationError("Nenhuma posicao com historico de precos para comparar.")

        monkeypatch.setattr(services, "load_compare", boom)
        export = ExportSpy()
        monkeypatch.setattr(services, "export_chart", export)
        toasts = ToastSpy()
        toasts.install(monkeypatch, CompareScreen)
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, CompareScreen())
            await pilot.press("o")
            await settle(pilot)
            assert export.calls == []
            assert toasts.severity_of("nada para exportar") == "warning"

    @pytest.mark.asyncio
    async def test_a_failed_export_is_reported_and_keeps_the_screen(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(services, "export_chart", ExportSpy(error=OSError("disco cheio")))
        toasts = ToastSpy()
        toasts.install(monkeypatch, CompareScreen)
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, CompareScreen())
            await pilot.press("o")
            await settle(pilot)
            assert toasts.severity_of("disco cheio") == "error"
            assert isinstance(app.screen, CompareScreen)


class TestIndices:
    @pytest.mark.asyncio
    async def test_applying_a_new_list_reloads_the_series(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[tuple[str, ...]] = []

        def load(*, indices: tuple[str, ...], **_: Any) -> Any:
            calls.append(indices)
            return make_compare(series=[CompareSeries("Carteira", [Decimal("100"), Decimal("110")])])

        monkeypatch.setattr(services, "load_compare", load)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, CompareScreen())
            field = screen.query_one(IndicesInput)
            field.input.value = "cdi"
            field.input.focus()
            await pilot.press("enter")
            await settle(pilot)
            assert calls == [("IBOV", "CDI"), ("CDI",)]
