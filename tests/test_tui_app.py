"""Tests for the app-level wiring (issues #73/#74): theme and privacy mode.

Both are preferences the interface changes from the inside, so both have to be
written back — the complaint they answer is "eu oculto, fecho, reabro e esta
visivel de novo".
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest

from bogle import format as fmt
from bogle.settings import DEFAULT_THEME
from bogle.tui import services
from bogle.tui.app import BogleApp
from tests.tui_fakes import ToastSpy, make_app, settle, stub_services


@pytest.fixture(autouse=True)
def _services(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_services(monkeypatch)


class TestTheme:
    @pytest.mark.asyncio
    async def test_defaults_to_the_dark_theme(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            assert app.theme == DEFAULT_THEME

    @pytest.mark.asyncio
    async def test_opens_with_the_configured_theme(self) -> None:
        app = BogleApp(theme="ansi-dark")
        async with app.run_test() as pilot:
            await settle(pilot)
            assert app.theme == "ansi-dark"

    @pytest.mark.asyncio
    async def test_opening_does_not_rewrite_the_theme_it_just_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        saved: list[str] = []
        monkeypatch.setattr(services, "save_theme", saved.append)
        app = BogleApp(theme="nord")
        async with app.run_test() as pilot:
            await settle(pilot)
            assert saved == []

    @pytest.mark.asyncio
    async def test_a_theme_picked_in_the_session_is_remembered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A paleta de comandos (ctrl+p) escreve em App.theme; e por ali que a
        # escolha tem de chegar ao banco.
        saved: list[str] = []
        monkeypatch.setattr(services, "save_theme", saved.append)
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            app.theme = "gruvbox"
            await settle(pilot)
            assert saved == ["gruvbox"]

    @pytest.mark.asyncio
    async def test_going_back_to_the_opening_theme_is_remembered_too(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Guardar "o tema inicial" e nao regravar seria errado: o banco ficaria
        # com o intermediario.
        saved: list[str] = []
        monkeypatch.setattr(services, "save_theme", saved.append)
        app = BogleApp(theme="nord")
        async with app.run_test() as pilot:
            await settle(pilot)
            app.theme = "gruvbox"
            await settle(pilot)
            app.theme = "nord"
            await settle(pilot)
            assert saved == ["gruvbox", "nord"]

    @pytest.mark.asyncio
    async def test_a_theme_that_no_longer_exists_falls_back_and_says_so(self, monkeypatch: pytest.MonkeyPatch) -> None:
        toasts = ToastSpy()
        toasts.install(monkeypatch, BogleApp)
        app = BogleApp(theme="tema-que-saiu-do-textual")
        async with app.run_test() as pilot:
            await settle(pilot)
            assert app.theme == DEFAULT_THEME
            assert toasts.severity_of("nao existe nesta versao") == "warning"

    @pytest.mark.asyncio
    async def test_a_failed_save_warns_without_losing_the_theme(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(theme: str) -> None:
            raise psycopg.OperationalError("connection refused")

        monkeypatch.setattr(services, "save_theme", boom)
        toasts = ToastSpy()
        toasts.install(monkeypatch, BogleApp)
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            app.theme = "gruvbox"
            await settle(pilot)
            # O tema vale nesta sessao; so nao vai valer na proxima.
            assert app.theme == "gruvbox"
            assert toasts.severity_of("nao foi salva") == "warning"


class TestPrivacyToggle:
    @pytest.mark.asyncio
    async def test_the_app_opens_with_whatever_format_configured(self) -> None:
        # `run_tui` aplica as preferencias antes de subir a app; aqui so a app.
        fmt.hide_amounts(True)
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            assert fmt.amounts_hidden()

    @pytest.mark.asyncio
    async def test_the_toggle_writes_both_ways(self, monkeypatch: pytest.MonkeyPatch) -> None:
        saved: list[bool] = []
        monkeypatch.setattr(services, "save_hide_amounts", saved.append)
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            await pilot.press("h")
            await settle(pilot)
            await pilot.press("h")
            await settle(pilot)
            assert saved == [True, False]
            assert not fmt.amounts_hidden()

    @pytest.mark.asyncio
    async def test_the_binding_is_available_on_every_screen(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # E atalho do App justamente para valer em Posicao e Transacoes tambem.
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            for key in ("1", "3"):  # Posicao, Transacoes
                await pilot.press("escape")
                await settle(pilot)
                await pilot.press(key)
                await settle(pilot)
                keys = {binding.key for (_, binding, _, _) in app.screen.active_bindings.values()}
                assert "h" in keys, f"tela {type(app.screen).__name__} sem o atalho"


class TestLoadPreferences:
    def test_defaults_when_nothing_is_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("sem banco")

        monkeypatch.setattr(services, "get_connection", boom)
        preferences = services.load_preferences()
        assert preferences.decimal_separator == fmt.CANONICAL_DECIMAL
        assert preferences.hide_amounts is False
        assert preferences.theme == DEFAULT_THEME
