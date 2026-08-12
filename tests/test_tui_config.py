"""Tests for the TUI's settings screen (issue #76): the table, editing in place,
reverting to the default, and the three preferences that must take effect at once.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from textual.widgets import DataTable, Input

from bogle import format as fmt
from bogle.domain.errors import ValidationError
from bogle.settings import DECIMAL_SEPARATOR, HIDE_VALUES, SETTINGS, THEME
from bogle.tui import services
from bogle.tui.app import BogleApp
from bogle.tui.screens.config import ConfigScreen
from bogle.tui.screens.modals import EditModal
from tests.tui_fakes import (
    ToastSpy,
    make_app,
    make_settings,
    open_screen,
    settle,
    stub_services,
    table_columns,
    table_rows,
)


class SettingsSpy:
    """Serves the settings and records what was written."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.changed: dict[str, Any] = {}
        self.saved: list[tuple[str, str]] = []
        self.reset: list[str] = []
        self.error = error

    def load(self) -> Any:
        return make_settings(**self.changed)

    def save(self, key: str, raw: str) -> Any:
        if self.error is not None:
            raise self.error
        self.saved.append((key, raw))
        value = _typed(key, raw)
        self.changed[key] = value
        return value

    def revert(self, key: str) -> Any:
        self.reset.append(key)
        self.changed.pop(key, None)
        return SETTINGS[key].default


def _typed(key: str, raw: str) -> Any:
    """What ``set_setting`` would give back for the keys these tests touch."""
    if key == HIDE_VALUES:
        return raw == "true"
    if key == "weight_drift_threshold":
        return Decimal(raw)
    return raw


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> SettingsSpy:
    stub_services(monkeypatch)
    settings = SettingsSpy()
    monkeypatch.setattr(services, "load_settings", settings.load)
    monkeypatch.setattr(services, "save_setting", settings.save)
    monkeypatch.setattr(services, "reset_setting", settings.revert)
    return settings


def row_of(screen: ConfigScreen, key: str) -> int:
    keys = [entry.key for entry in screen.report or []]
    return keys.index(key)


async def edit(pilot: Any, screen: ConfigScreen, key: str, value: str) -> None:
    screen.query_one(DataTable).move_cursor(row=row_of(screen, key))
    await pilot.pause()
    await pilot.press("e")
    await settle(pilot)
    pilot.app.screen.query_one(Input).value = value
    await pilot.press("enter")
    await settle(pilot)


class TestTable:
    @pytest.mark.asyncio
    async def test_lists_every_key_with_its_value_and_provenance(self, spy: SettingsSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ConfigScreen())
            assert table_columns(screen) == ["Chave", "Valor", "Tipo", "Atualizado em", "Descricao"]
            first = table_rows(screen)[0]
            assert first[:4] == ["decimal_separator", ".", "str", "(default)"]

    @pytest.mark.asyncio
    async def test_an_unset_key_reads_as_undefined(self, spy: SettingsSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ConfigScreen())
            assert table_rows(screen)[row_of(screen, "last_rebalance_date")][1] == "(nao definido)"


class TestEditing:
    @pytest.mark.asyncio
    async def test_e_opens_the_current_value_and_saves_what_was_typed(self, spy: SettingsSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ConfigScreen())
            screen.query_one(DataTable).move_cursor(row=row_of(screen, "weight_drift_threshold"))
            await pilot.pause()
            await pilot.press("e")
            await settle(pilot)
            modal = app.screen
            assert isinstance(modal, EditModal)
            assert modal.dialog_title == "Editar weight_drift_threshold"
            assert modal.typed == "0.05"
            modal.query_one(Input).value = "0.08"
            await pilot.press("enter")
            await settle(pilot)
            assert spy.saved == [("weight_drift_threshold", "0.08")]
            assert table_rows(screen)[row_of(screen, "weight_drift_threshold")][1] == "0.08"

    @pytest.mark.asyncio
    async def test_enter_on_a_row_edits_it_too(self, spy: SettingsSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, ConfigScreen())
            await pilot.press("enter")
            await settle(pilot)
            assert isinstance(app.screen, EditModal)

    @pytest.mark.asyncio
    async def test_a_never_set_key_opens_blank(self, spy: SettingsSpy) -> None:
        # "(nao definido)" e como a ausencia e mostrada, nao um valor que o
        # parser aceitaria de volta.
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ConfigScreen())
            screen.query_one(DataTable).move_cursor(row=row_of(screen, "last_rebalance_date"))
            await pilot.pause()
            await pilot.press("e")
            await settle(pilot)
            modal = app.screen
            assert isinstance(modal, EditModal)
            assert modal.typed == ""

    @pytest.mark.asyncio
    async def test_escape_cancels_without_writing(self, spy: SettingsSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, ConfigScreen())
            await pilot.press("e")
            await settle(pilot)
            await pilot.press("escape")
            await settle(pilot)
            assert spy.saved == []

    @pytest.mark.asyncio
    async def test_an_invalid_value_is_refused_with_the_same_message_as_the_command(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub_services(monkeypatch)
        spy = SettingsSpy(error=ValidationError("Periodo de rebalanceamento deve ser 6 ou 12 meses, recebido 7."))
        monkeypatch.setattr(services, "load_settings", spy.load)
        monkeypatch.setattr(services, "save_setting", spy.save)
        toasts = ToastSpy()
        toasts.install(monkeypatch, ConfigScreen)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ConfigScreen())
            await edit(pilot, screen, "rebalance_period_months", "7")
            assert toasts.severity_of("deve ser 6 ou 12 meses") == "error"
            # A tabela continua de pe: so a escrita falhou.
            assert table_rows(screen)[row_of(screen, "rebalance_period_months")][1] == "12"


class TestReverting:
    @pytest.mark.asyncio
    async def test_d_reverts_a_changed_key(self, spy: SettingsSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ConfigScreen())
            await edit(pilot, screen, "decimal_separator", ",")
            assert table_rows(screen)[row_of(screen, "decimal_separator")][3] != "(default)"
            screen.query_one(DataTable).move_cursor(row=row_of(screen, "decimal_separator"))
            await pilot.pause()
            await pilot.press("d")
            await settle(pilot)
            assert spy.reset == ["decimal_separator"]
            assert table_rows(screen)[row_of(screen, "decimal_separator")][3] == "(default)"

    @pytest.mark.asyncio
    async def test_reverting_a_key_that_is_already_default_writes_nothing(self, spy: SettingsSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, ConfigScreen())
            await pilot.press("d")
            await settle(pilot)
            assert spy.reset == []


class TestAppliedNow:
    @pytest.mark.asyncio
    async def test_the_decimal_separator_takes_effect_without_reopening(self, spy: SettingsSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ConfigScreen())
            await edit(pilot, screen, DECIMAL_SEPARATOR, ",")
            assert fmt.separators().decimal == ","
            assert fmt.money(Decimal("1234.5")) == "1.234,50"

    @pytest.mark.asyncio
    async def test_the_privacy_mode_takes_effect_without_reopening(self, spy: SettingsSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ConfigScreen())
            await edit(pilot, screen, HIDE_VALUES, "true")
            assert fmt.amounts_hidden() is True

    @pytest.mark.asyncio
    async def test_the_theme_takes_effect_and_is_not_saved_twice(self, spy: SettingsSpy) -> None:
        # A tela ja gravou: o watcher do tema nao pode gravar de novo o mesmo valor.
        saved_themes: list[str] = []
        app = make_app()
        async with app.run_test() as pilot:
            pilot.app._remember_theme = lambda theme: saved_themes.append(theme)  # type: ignore[method-assign]
            screen = await open_screen(pilot, ConfigScreen())
            await edit(pilot, screen, THEME, "nord")
            assert app.theme == "nord"
            assert saved_themes == []

    @pytest.mark.asyncio
    async def test_a_theme_this_version_does_not_have_is_a_warning_not_a_crash(
        self, spy: SettingsSpy, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # O aviso sai da App (e dela que o tema e), nao da tela: espionar a tela
        # deixaria o teste passar sem aviso nenhum.
        toasts = ToastSpy()
        toasts.install(monkeypatch, BogleApp)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ConfigScreen())
            before = app.theme
            await edit(pilot, screen, THEME, "tema-que-nao-existe")
            assert app.is_running
            assert app.theme == before
            assert toasts.severity_of("nao existe nesta versao") == "warning"
