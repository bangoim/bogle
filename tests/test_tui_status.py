"""Tests for the TUI's cycle screen (issue #76): the same reading ``bogle status``
gives, including the never-evaluated and overdue cases.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from bogle.tui import services
from bogle.tui.screens.status import StatusScreen
from tests.tui_fakes import ToastSpy, make_app, make_cycle, open_screen, stub_services


@pytest.fixture(autouse=True)
def _services(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_services(monkeypatch)


def serve(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> None:
    monkeypatch.setattr(services, "load_cycle", lambda **_: make_cycle(**overrides))


class TestPanel:
    @pytest.mark.asyncio
    async def test_shows_the_period_and_both_dates(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, StatusScreen())
            assert "Ciclo de avaliação 12 meses" in screen.panel
            assert "Última avaliação   2026-02-10" in screen.panel
            assert "Próxima avaliação  2027-02-10 (em 182 dia(s))" in screen.panel

    @pytest.mark.asyncio
    async def test_an_overdue_cycle_says_for_how_long(self, monkeypatch: pytest.MonkeyPatch) -> None:
        serve(monkeypatch, last_evaluation=date(2025, 2, 10), next_evaluation=date(2026, 2, 10), days=-183)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, StatusScreen())
            assert "Avaliação vencida  desde 2026-02-10 (há 183 dia(s))" in screen.panel
            assert "Uma sugestão de aporte conta como avaliação" in screen.note

    @pytest.mark.asyncio
    async def test_never_evaluated_points_at_the_aporte_screen(self, monkeypatch: pytest.MonkeyPatch) -> None:
        serve(monkeypatch, last_evaluation=None, next_evaluation=None, days=None)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, StatusScreen())
            assert screen.panel == "Ciclo de avaliação 12 meses"
            assert "Nenhuma avaliação registrada ainda" in screen.note
            assert "tela de Aporte" in screen.note

    @pytest.mark.asyncio
    async def test_the_period_follows_the_setting(self, monkeypatch: pytest.MonkeyPatch) -> None:
        serve(monkeypatch, period_months=6)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, StatusScreen())
            assert "Ciclo de avaliação 6 meses" in screen.panel


class TestFailures:
    @pytest.mark.asyncio
    async def test_a_database_failure_is_a_message_not_a_crash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import psycopg

        def boom(**_: Any) -> Any:
            raise psycopg.OperationalError

        monkeypatch.setattr(services, "load_cycle", boom)
        toasts = ToastSpy()
        toasts.install(monkeypatch, StatusScreen)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, StatusScreen())
            assert isinstance(app.screen, StatusScreen)
            assert screen.panel == ""
            assert "não foi possível conectar ao banco" in screen.note
            assert toasts.severity_of("banco de dados") == "error"
