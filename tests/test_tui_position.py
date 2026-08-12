"""Tests for the TUI's Position screen (issue #73): the table, the totals, the
no-prices toggle and the refresh.
"""

from __future__ import annotations

import threading
from decimal import Decimal
from typing import Any

import pytest
from rich.text import Text
from textual.widgets import DataTable

from bogle.domain.assets import AssetType
from bogle.domain.errors import QuoteNotFoundError
from bogle.tui import services
from bogle.tui.screens.position import PositionScreen
from tests.tui_fakes import (
    ToastSpy,
    empty_snapshot,
    make_app,
    make_snapshot,
    make_unpriced_position,
    settle,
    snapshot_of,
    stub_services,
)


class SnapshotSpy:
    """Serves a snapshot and records how each load was requested."""

    def __init__(self, *, snapshot: Any = None) -> None:
        self.snapshot = snapshot if snapshot is not None else make_snapshot()
        self.calls: list[bool] = []

    def __call__(self, *, with_prices: bool, **_: Any) -> Any:
        self.calls.append(with_prices)
        return self.snapshot


async def open_position(pilot: Any) -> PositionScreen:
    await pilot.app.push_screen(PositionScreen())
    await settle(pilot)
    screen = pilot.app.screen
    assert isinstance(screen, PositionScreen)
    return screen


def row(screen: PositionScreen, index: int) -> list[str]:
    cells = screen.query_one(DataTable).get_row_at(index)
    return [cell.plain if isinstance(cell, Text) else str(cell) for cell in cells]


@pytest.fixture(autouse=True)
def _services(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_services(monkeypatch)


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> SnapshotSpy:
    loader = SnapshotSpy()
    monkeypatch.setattr(services, "load_snapshot", loader)
    return loader


class TestTable:
    @pytest.mark.asyncio
    async def test_one_row_per_position_with_the_cli_columns(self, spy: SnapshotSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_position(pilot)
            table = screen.query_one(DataTable)
            assert table.row_count == 2
            assert [str(column.label) for column in table.columns.values()] == [
                "Ticker",
                "Tipo",
                "Qtd",
                "Preco",
                "Valor",
                "Peso atual",
                "Target",
                "Drift",
                "PnL R$",
                "PnL %",
                "TWR",
            ]
            assert row(screen, 0) == [
                "PETR4",
                "STOCK",
                "100",
                "41.15",
                "4115.00",
                "52.30%",
                "50.00%",
                "+2.30%",
                "+365.00",
                "+9.74%",
                "+12.75%",
            ]

    @pytest.mark.asyncio
    async def test_unpriced_cells_are_dashes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Renda fixa privada sem preco disponivel: as colunas de mercado somem,
        # as da base (Qtd, Target) continuam.
        unpriced = make_unpriced_position(
            "CDB01", AssetType.CDB, quantity=Decimal("1"), total_invested=Decimal("1000"),
            target_weight=Decimal("0.4"), dividends=Decimal("0"),
        )  # fmt: skip
        monkeypatch.setattr(services, "load_snapshot", lambda **_: snapshot_of(unpriced))
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_position(pilot)
            assert row(screen, 0) == ["CDB01", "CDB", "1", "-", "-", "-", "40.00%", "-", "-", "-", "-"]

    @pytest.mark.asyncio
    async def test_empty_portfolio_says_so(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(services, "load_snapshot", lambda **_: empty_snapshot())
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_position(pilot)
            assert screen.query_one(DataTable).row_count == 0
            assert screen.note == "Nenhuma posicao ativa."


class TestTotals:
    @pytest.mark.asyncio
    async def test_totals_carry_the_same_figures_as_the_cli_footer(self, spy: SnapshotSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_position(pilot)
            assert "Total investido 4550.00" in screen.totals
            assert "Patrimonio total 4926.20" in screen.totals
            assert "Variacao +376.20 (+8.27%)" in screen.totals
            assert "Lucro do mes +82.40" in screen.totals
            assert "Proventos (12m) +145.00" in screen.totals

    @pytest.mark.asyncio
    async def test_nothing_priced_shows_dashes_not_a_zero_portfolio(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Modo sem precos (ou todas as cotacoes falhando): os totais de mercado
        # somam zero, o que nao e o mesmo que a carteira valer zero.
        monkeypatch.setattr(
            services,
            "load_snapshot",
            lambda **_: snapshot_of(
                make_unpriced_position("PETR4", AssetType.STOCK, total_invested=Decimal("4550")),
                month_profit=None,
            ),
        )
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_position(pilot)
            assert "Total investido 4550.00" in screen.totals
            assert "Patrimonio total -" in screen.totals
            assert "Variacao - (-)" in screen.totals

    @pytest.mark.asyncio
    async def test_price_provenance_is_listed(self, spy: SnapshotSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_position(pilot)
            assert "Fonte(s) de preco brapi, calculado" in screen.totals
            assert "Cotacao mais recente 2026-08-11 18:28" in screen.totals

    @pytest.mark.asyncio
    async def test_excluded_tickers_are_noted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(services, "load_snapshot", lambda **_: make_snapshot(excluded=["TESOURO-SELIC-2029"]))
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_position(pilot)
            assert "lucro do mes nao considera TESOURO-SELIC-2029" in screen.note


class TestLoading:
    @pytest.mark.asyncio
    async def test_table_shows_its_loading_state_while_fetching(self, monkeypatch: pytest.MonkeyPatch) -> None:
        release = threading.Event()

        def slow(**_: Any) -> Any:
            release.wait(timeout=5)
            return make_snapshot()

        monkeypatch.setattr(services, "load_snapshot", slow)
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.app.push_screen(PositionScreen())
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, PositionScreen)
            assert screen.query_one(DataTable).loading is True
            release.set()
            await settle(pilot)
            assert screen.query_one(DataTable).loading is False


class TestActions:
    @pytest.mark.asyncio
    async def test_a_slow_load_cannot_overwrite_the_view_it_was_replaced_by(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Alternar para "sem precos" enquanto a carga com precos ainda roda: a
        # thread lenta nao para, e sem o guard de cancelamento ela sobrescreveria
        # a tabela nova com os dados velhos.
        release = threading.Event()
        priced = make_snapshot()
        unpriced = snapshot_of(make_unpriced_position("CDB01", AssetType.CDB))

        def load(*, with_prices: bool, **_: Any) -> Any:
            if with_prices:
                release.wait(timeout=5)
                return priced
            return unpriced

        monkeypatch.setattr(services, "load_snapshot", load)
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.app.push_screen(PositionScreen())
            await pilot.pause()
            await pilot.press("p")  # cancela a carga com precos, pede a sem precos
            await settle(pilot)
            screen = app.screen
            assert isinstance(screen, PositionScreen)
            assert [r[0] for r in [row(screen, i) for i in range(screen.query_one(DataTable).row_count)]] == ["CDB01"]

            release.set()  # a carga cancelada termina agora
            await settle(pilot)
            assert [r[0] for r in [row(screen, i) for i in range(screen.query_one(DataTable).row_count)]] == ["CDB01"]
            assert screen.sub_title == "posicao - sem precos"

    @pytest.mark.asyncio
    async def test_opens_with_live_prices(self, spy: SnapshotSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_position(pilot)
            assert spy.calls == [True]
            assert screen.sub_title == "posicao - precos ao vivo"

    @pytest.mark.asyncio
    async def test_p_switches_to_the_no_prices_view(self, spy: SnapshotSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_position(pilot)
            await pilot.press("p")
            await settle(pilot)
            assert spy.calls == [True, False]
            assert screen.sub_title == "posicao - sem precos"
            await pilot.press("p")
            await settle(pilot)
            assert spy.calls == [True, False, True]

    @pytest.mark.asyncio
    async def test_r_refetches_in_the_current_mode(self, spy: SnapshotSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await open_position(pilot)
            await pilot.press("r")
            await settle(pilot)
            assert spy.calls == [True, True]


class TestFailures:
    @pytest.mark.asyncio
    async def test_price_failure_keeps_the_screen_and_explains(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(**_: Any) -> Any:
            raise QuoteNotFoundError("PETR4", provider="brapi")

        monkeypatch.setattr(services, "load_snapshot", boom)
        toasts = ToastSpy()
        toasts.install(monkeypatch, PositionScreen)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_position(pilot)
            assert isinstance(app.screen, PositionScreen)  # continua na tela
            assert screen.query_one(DataTable).row_count == 0
            assert screen.query_one(DataTable).loading is False
            assert toasts.severity_of("PETR4") == "error"
