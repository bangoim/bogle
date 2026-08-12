"""Tests for the TUI's income screen (issue #75): both groupings, the window and
the totals row.
"""

from __future__ import annotations

from typing import Any

import pytest

from bogle.format import MASK
from bogle.tui import services
from bogle.tui.screens.income import IncomeScreen
from tests.tui_fakes import (
    empty_income,
    make_app,
    make_income,
    open_screen,
    settle,
    stub_services,
    table_columns,
    table_rows,
)


class IncomeSpy:
    """Serves the report and counts how many times it was actually fetched."""

    def __init__(self) -> None:
        self.periods: list[str] = []

    def __call__(self, *, period: str, **_: Any) -> Any:
        self.periods.append(period)
        return make_income()


@pytest.fixture(autouse=True)
def _services(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_services(monkeypatch)


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> IncomeSpy:
    loader = IncomeSpy()
    monkeypatch.setattr(services, "load_income", loader)
    return loader


class TestByMonth:
    @pytest.mark.asyncio
    async def test_columns_and_rows_match_the_command(self, spy: IncomeSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, IncomeScreen())
            assert table_columns(screen) == ["Mes", "Dividendos", "JCP (liq)", "FII rend.", "Juros RF", "Total"]
            assert table_rows(screen)[0] == ["2026-07", "45.50", "17.00", "82.40", "0.00", "144.90"]

    @pytest.mark.asyncio
    async def test_the_last_row_totals_every_column(self, spy: IncomeSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, IncomeScreen())
            assert table_rows(screen)[-1] == ["TOTAL", "45.50", "17.00", "112.40", "12.10", "187.00"]

    @pytest.mark.asyncio
    async def test_opens_by_month_over_the_last_twelve(self, spy: IncomeSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, IncomeScreen())
            assert spy.periods == ["12m"]
            assert screen.sub_title == "proventos - 12m por mes"
            assert "De 2025-09-01 a 2026-08-12" in screen.note


class TestByTicker:
    @pytest.mark.asyncio
    async def test_g_regroups_without_another_round_trip(self, spy: IncomeSpy) -> None:
        # As duas visoes vieram na mesma leitura do ledger: trocar nao busca de novo.
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, IncomeScreen())
            await pilot.press("g")
            await pilot.pause()
            assert spy.periods == ["12m"]
            assert table_columns(screen) == ["Ticker", "Tipo", "Total"]
            assert table_rows(screen) == [
                ["MXRF11", "RENDIMENTO", "112.40"],
                ["PETR4", "DIVIDEND", "45.50"],
                ["TOTAL", "", "157.90"],
            ]
            assert screen.sub_title == "proventos - 12m por ticker"

    @pytest.mark.asyncio
    async def test_g_again_goes_back_to_months(self, spy: IncomeSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, IncomeScreen())
            await pilot.press("g")
            await pilot.pause()
            await pilot.press("g")
            await pilot.pause()
            assert table_columns(screen)[0] == "Mes"

    @pytest.mark.asyncio
    async def test_the_grouping_survives_a_window_change(self, spy: IncomeSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, IncomeScreen())
            await pilot.press("g")
            await pilot.pause()
            await pilot.press("t")
            await settle(pilot)
            assert spy.periods == ["12m", "all"]
            assert table_columns(screen)[0] == "Ticker"
            assert screen.sub_title == "proventos - all por ticker"


class TestEmpty:
    @pytest.mark.asyncio
    async def test_no_income_says_so_in_both_groupings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(services, "load_income", lambda **_: empty_income())
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, IncomeScreen())
            assert table_rows(screen) == []
            assert screen.note == "Nenhum provento no periodo."
            await pilot.press("g")
            await pilot.pause()
            assert table_rows(screen) == []
            assert screen.note == "Nenhum provento no periodo."


class TestHiddenAmounts:
    @pytest.mark.asyncio
    async def test_h_masks_the_amounts_but_keeps_the_months(self, spy: IncomeSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, IncomeScreen())
            await pilot.press("h")
            await pilot.pause()
            assert table_rows(screen)[0] == ["2026-07", MASK, MASK, MASK, MASK, MASK]
            assert table_rows(screen)[-1][0] == "TOTAL"

    @pytest.mark.asyncio
    async def test_h_again_brings_them_back(self, spy: IncomeSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, IncomeScreen())
            await pilot.press("h")
            await pilot.pause()
            await pilot.press("h")
            await pilot.pause()
            assert table_rows(screen)[0][1] == "45.50"
