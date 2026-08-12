"""Tests for the TUI's profitability screen (issue #75): the panel of windows,
the per-index columns and the editable index list.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from bogle.reports.returns import PeriodReturn
from bogle.tui import services
from bogle.tui.screens.returns import ReturnsScreen
from bogle.tui.widgets.indices import IndicesInput
from tests.tui_fakes import (
    INDICES,
    make_app,
    make_returns,
    open_screen,
    settle,
    stub_services,
    table_columns,
    table_rows,
)


class ReturnsSpy:
    """Serves the report and records the indices each load asked for."""

    def __init__(self) -> None:
        self.report = make_returns()
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *, indices: tuple[str, ...] = (), **_: Any) -> Any:
        self.calls.append(indices)
        return self.report


@pytest.fixture(autouse=True)
def _services(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_services(monkeypatch)


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> ReturnsSpy:
    loader = ReturnsSpy()
    monkeypatch.setattr(services, "load_returns", loader)
    return loader


class TestPanel:
    @pytest.mark.asyncio
    async def test_one_row_per_window_with_a_pair_of_columns_per_index(self, spy: ReturnsSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ReturnsScreen())
            assert table_columns(screen) == [
                "Periodo",
                "Janela",
                "Carteira (TWR)",
                "IBOV",
                "vs IBOV",
                "CDI",
                "vs CDI",
            ]
            assert table_rows(screen)[0] == [
                "Total",
                "desde 2024-03-01",
                "+18.40%",
                "+11.00%",
                "+7.40 p.p.",
                "+20.50%",
                "-2.10 p.p.",
            ]

    @pytest.mark.asyncio
    async def test_the_difference_is_in_points_not_in_percent(self, spy: ReturnsSpy) -> None:
        # 18.40% de carteira contra 11.00% do IBOV e 7.4 p.p. de vantagem, nao
        # "7.4%" — que seria outra afirmacao (e muito menor).
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ReturnsScreen())
            assert table_rows(screen)[0][4] == "+7.40 p.p."

    @pytest.mark.asyncio
    async def test_the_last_month_shows_a_closed_window(self, spy: ReturnsSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ReturnsScreen())
            assert table_rows(screen)[2][:2] == ["Ultimo mes", "2026-07-12 a 2026-08-12"]

    @pytest.mark.asyncio
    async def test_an_index_without_data_is_a_dash_on_both_columns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        report = make_returns(
            rows=[
                PeriodReturn(
                    period="total",
                    start=date(2024, 3, 1),
                    end=date(2026, 8, 12),
                    twr=Decimal("0.184"),
                    index_returns={"IBOV": None, "CDI": Decimal("0.2")},
                )
            ]
        )
        monkeypatch.setattr(services, "load_returns", lambda **_: report)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ReturnsScreen())
            assert table_rows(screen)[0][3:5] == ["-", "-"]

    @pytest.mark.asyncio
    async def test_a_portfolio_without_price_history_reports_no_twr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        report = make_returns(
            rows=[
                PeriodReturn(
                    period="total",
                    start=date(2024, 3, 1),
                    end=date(2026, 8, 12),
                    twr=None,
                    index_returns={},
                )
            ],
            excluded=["TESOURO-SELIC-2029"],
        )
        monkeypatch.setattr(services, "load_returns", lambda **_: report)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ReturnsScreen())
            assert table_rows(screen)[0][2] == "-"
            assert "TWR nao considera TESOURO-SELIC-2029" in screen.note


class TestIndices:
    @pytest.mark.asyncio
    async def test_opens_with_the_configured_default(self, spy: ReturnsSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ReturnsScreen())
            assert spy.calls == [INDICES]
            assert screen.query_one(IndicesInput).input.value == "IBOV,CDI"

    @pytest.mark.asyncio
    async def test_i_focuses_the_field_without_typing_into_it(self, spy: ReturnsSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ReturnsScreen())
            await pilot.press("i")
            await pilot.pause()
            field = screen.query_one(IndicesInput)
            assert app.focused is field.input
            assert field.input.value == "IBOV,CDI"

    @pytest.mark.asyncio
    async def test_enter_applies_what_was_typed(self, spy: ReturnsSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ReturnsScreen())
            field = screen.query_one(IndicesInput)
            field.input.value = "ipca, cdi"
            field.input.focus()
            await pilot.press("enter")
            await settle(pilot)
            assert spy.calls[-1] == ("IPCA", "CDI")
            assert table_columns(screen)[3:] == ["IPCA", "vs IPCA", "CDI", "vs CDI"]

    @pytest.mark.asyncio
    async def test_an_empty_list_compares_against_nothing(self, spy: ReturnsSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ReturnsScreen())
            field = screen.query_one(IndicesInput)
            field.input.value = ""
            field.input.focus()
            await pilot.press("enter")
            await settle(pilot)
            assert spy.calls[-1] == ()
            assert table_columns(screen) == ["Periodo", "Janela", "Carteira (TWR)"]

    @pytest.mark.asyncio
    async def test_an_index_name_is_not_read_as_markup(self, spy: ReturnsSpy) -> None:
        # O nome vira rotulo de coluna, e o textual le markup numa string: "[/]"
        # digitado no campo derrubava a app de dentro do worker.
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ReturnsScreen())
            field = screen.query_one(IndicesInput)
            field.input.value = "[/]"
            field.input.focus()
            await pilot.press("enter")
            await settle(pilot)
            assert app.is_running
            assert table_columns(screen)[3:] == ["[/]", "vs [/]"]

    @pytest.mark.asyncio
    async def test_a_refresh_does_not_undo_what_the_user_typed(self, spy: ReturnsSpy) -> None:
        # O campo e preenchido uma unica vez, com o default; depois disso ele e do
        # usuario, e recarregar nao pode apagar o que ele digitou.
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ReturnsScreen())
            screen.query_one(IndicesInput).input.value = "SELIC"
            await pilot.press("r")
            await settle(pilot)
            assert screen.query_one(IndicesInput).input.value == "SELIC"


class TestNotes:
    @pytest.mark.asyncio
    async def test_the_twr_legend_is_always_there(self, spy: ReturnsSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ReturnsScreen())
            assert "exclui o efeito de aportes e retiradas" in screen.note

    @pytest.mark.asyncio
    async def test_an_index_that_could_not_be_resolved_is_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            services,
            "load_returns",
            lambda **_: make_returns(index_errors={"IPCA": "serie indisponivel no BCB"}),
        )
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ReturnsScreen())
            assert "IPCA: serie indisponivel no BCB" in screen.note
