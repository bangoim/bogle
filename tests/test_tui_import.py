"""Tests for the Importar screen (issue #34): preview, confirm, write."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from textual.widgets import Button, Checkbox

from bogle import format as fmt
from bogle.domain.errors import ImportFileError, ImportStaleError
from bogle.imports.plan import ImportPlan
from bogle.tui import services
from bogle.tui.screens.import_ import COLUMNS, ImportScreen, typed_path
from bogle.tui.screens.modals import ConfirmModal, NextStepModal
from bogle.tui.screens.register import RegisterScreen
from tests.tui_fakes import (
    ToastSpy,
    make_app,
    make_import_plan,
    open_screen,
    settle,
    stub_services,
    table_columns,
    table_rows,
)

PATH = "~/Downloads/negociacao.xlsx"


@pytest.fixture(autouse=True)
def _services(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_services(monkeypatch)


class PreviewSpy:
    """Records what the screen asked to preview, and can fail on demand."""

    def __init__(self, *, error: Exception | None = None, new: bool = True) -> None:
        self.calls: list[tuple[Path, Decimal | None, bool]] = []
        self.error = error
        self.new = new

    def __call__(self, path: Path, *, fee_rate: Decimal | None = None, replace: bool = False) -> ImportPlan:
        self.calls.append((path, fee_rate, replace))
        if self.error is not None:
            raise self.error
        return make_import_plan(fee_rate=fee_rate, replace=replace, new=self.new)


class RunSpy:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls: list[ImportPlan] = []
        self.error = error

    def __call__(self, plan: ImportPlan) -> ImportPlan:
        self.calls.append(plan)
        if self.error is not None:
            raise self.error
        return plan


def fill(screen: ImportScreen, **values: str) -> None:
    for field_id, value in values.items():
        screen.field(field_id).set_value(value)


async def preview(pilot: Any, screen: ImportScreen, path: str = PATH, fee: str = "") -> None:
    fill(screen, path=path, fee=fee)
    screen.field("path").input.focus()
    await pilot.press("enter")
    await settle(pilot)


def submit_disabled(screen: ImportScreen) -> bool:
    return screen.query_one("#submit", Button).disabled


def test_typed_path_takes_what_the_finder_drops() -> None:
    home = Path.home()
    assert typed_path("  ~/Downloads/negociacao.xlsx ") == home / "Downloads" / "negociacao.xlsx"
    assert typed_path("'/Users/x/My File.xlsx'") == Path("/Users/x/My File.xlsx")
    assert typed_path('"/Users/x/My File.xlsx"') == Path("/Users/x/My File.xlsx")
    assert typed_path(r"/Users/x/My\ File\ \(1\).xlsx") == Path("/Users/x/My File (1).xlsx")


class TestMenu:
    @pytest.mark.asyncio
    async def test_item_four_of_registrar_opens_it(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            await pilot.press("2")  # Registrar, na Home
            await settle(pilot)
            assert isinstance(app.screen, RegisterScreen)
            await pilot.press("4")
            await settle(pilot)
            assert isinstance(app.screen, ImportScreen)
            assert app.screen.focused is app.screen.field("path").input


class TestPreview:
    @pytest.mark.asyncio
    async def test_enter_previews_into_the_table_and_the_note(self, monkeypatch: pytest.MonkeyPatch) -> None:
        spy = PreviewSpy()
        monkeypatch.setattr(services, "preview_import", spy)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ImportScreen())
            assert submit_disabled(screen)
            await preview(pilot, screen)
            assert spy.calls == [(Path.home() / "Downloads" / "negociacao.xlsx", None, False)]
            assert table_columns(screen) == list(COLUMNS)
            assert table_rows(screen) == [
                ["AUVP11", "novas", "3", "0", "zerada, target 30.00%"],
                ["MUND11", "valor difere", "0", "2", "2026-01-09 compra: arquivo 1,920.21 × ledger 1,920.24"],  # noqa: RUF001
                ["NB1011", "linha com erro", "0", "0", "linha 8"],
                ["VWRA11", "diverge do ledger", "0", "0", "2026-01-09 compra: arquivo 72 × ledger 70"],  # noqa: RUF001
                ["XPTO11", "sem cadastro", "0", "0", "bogle add XPTO11"],
            ]
            assert screen.note.startswith("Atenção:\n1. Novas: 3 (AUVP11 3)\n2. Duplicadas: 2 (MUND11 2)\n")
            assert (
                "; marque Substituir divergentes para trocar pelos lançamentos da B3" in screen.note
            )  # a dica fala da caixa, nao da opcao da CLI
            assert "3 operações importadas sem taxas" in screen.note
            assert not submit_disabled(screen)
            assert screen.focused is screen.query_one("#submit", Button)

    @pytest.mark.asyncio
    async def test_the_fee_field_reaches_the_preview(self, monkeypatch: pytest.MonkeyPatch) -> None:
        spy = PreviewSpy()
        monkeypatch.setattr(services, "preview_import", spy)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ImportScreen())
            await preview(pilot, screen, fee="0,00032")
            assert spy.calls[0][1] == Decimal("0.00032")
            assert "sem taxas" not in screen.note

    @pytest.mark.asyncio
    async def test_a_bad_fee_is_refused_next_to_the_field(self, monkeypatch: pytest.MonkeyPatch) -> None:
        spy = PreviewSpy()
        monkeypatch.setattr(services, "preview_import", spy)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ImportScreen())
            await preview(pilot, screen, fee="2")
            assert spy.calls == []
            assert screen.field("fee").error == "Taxa deve estar em [0, 1), recebido 2."

    @pytest.mark.asyncio
    async def test_a_failed_preview_keeps_the_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        toasts = ToastSpy()
        toasts.install(monkeypatch, ImportScreen)
        message = "negociacao.csv não é um .xlsx válido: use o .xlsx baixado"
        monkeypatch.setattr(services, "preview_import", PreviewSpy(error=ImportFileError(message)))
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ImportScreen())
            await preview(pilot, screen, path="~/negociacao.csv")
            assert screen.field("path").value == "~/negociacao.csv"
            assert screen.note == message
            assert table_rows(screen) == []
            assert toasts.severity_of("não é um .xlsx válido") == "error"
            assert submit_disabled(screen)
            assert screen.plan is None

    @pytest.mark.asyncio
    async def test_nothing_new_leaves_importar_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(services, "preview_import", PreviewSpy(new=False))
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ImportScreen())
            await preview(pilot, screen)
            assert screen.note.startswith("Atenção:\n1. Novas: 0\n2. Duplicadas: 5 (AUVP11 3, MUND11 2)")
            assert submit_disabled(screen)

    @pytest.mark.asyncio
    async def test_privacy_masks_the_quantities(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ImportScreen())
            await preview(pilot, screen)
            fmt.hide_amounts(True)
            screen.render_amounts()
            await pilot.pause()
            detail = table_rows(screen)[3][4]
            assert "72" not in detail and fmt.MASK in detail
            assert "arquivo 72" not in screen.note


class TestWrite:
    @pytest.mark.asyncio
    async def test_importar_confirms_and_writes_the_previewed_plan(self, monkeypatch: pytest.MonkeyPatch) -> None:
        toasts = ToastSpy()
        toasts.install(monkeypatch, ImportScreen)
        run = RunSpy()
        monkeypatch.setattr(services, "run_import", run)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ImportScreen())
            await preview(pilot, screen)
            await pilot.press("enter")  # Importar, focado depois da previa
            await settle(pilot)
            modal = app.screen
            assert isinstance(modal, ConfirmModal)
            assert modal.body == "3 operações de negociacao.xlsx\n2 compras, 1 venda, 1 ticker"
            await pilot.press("enter")
            await settle(pilot)
            assert run.calls == [screen.plan]
            assert "3 operações importadas." in toasts.messages
            done = app.screen
            assert isinstance(done, NextStepModal)
            assert done.dialog_title == "Importação concluída"
            assert done.again_label == "Importar outro"

    @pytest.mark.asyncio
    async def test_importar_outro_empties_the_screen(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ImportScreen())
            await preview(pilot, screen)
            await pilot.press("enter")
            await settle(pilot)
            await pilot.press("enter")  # confirma
            await settle(pilot)
            await pilot.press("enter")  # Importar outro
            await settle(pilot)
            assert app.screen is screen
            assert screen.field("path").value == ""
            assert table_rows(screen) == []
            assert screen.note == ""
            assert submit_disabled(screen)

    @pytest.mark.asyncio
    async def test_a_failed_write_keeps_everything(self, monkeypatch: pytest.MonkeyPatch) -> None:
        toasts = ToastSpy()
        toasts.install(monkeypatch, ImportScreen)
        monkeypatch.setattr(services, "run_import", RunSpy(error=ImportStaleError()))
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ImportScreen())
            await preview(pilot, screen)
            await pilot.press("ctrl+s")
            await settle(pilot)
            await pilot.press("enter")
            await settle(pilot)
            assert app.screen is screen
            assert screen.field("path").value == PATH
            assert len(table_rows(screen)) == 5
            assert toasts.severity_of("o ledger mudou desde a prévia") == "error"

    @pytest.mark.asyncio
    async def test_a_path_changed_after_the_preview_is_not_written(self, monkeypatch: pytest.MonkeyPatch) -> None:
        toasts = ToastSpy()
        toasts.install(monkeypatch, ImportScreen)
        run = RunSpy()
        monkeypatch.setattr(services, "run_import", run)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ImportScreen())
            await preview(pilot, screen)
            fill(screen, path="~/Downloads/outro.xlsx")
            await pilot.pause()
            assert submit_disabled(screen)
            await pilot.press("ctrl+s")
            await settle(pilot)
            assert app.screen is screen
            assert run.calls == []
            assert ImportScreen.CHANGED in toasts.messages

    @pytest.mark.asyncio
    async def test_a_fee_changed_after_the_preview_is_not_written(self, monkeypatch: pytest.MonkeyPatch) -> None:
        toasts = ToastSpy()
        toasts.install(monkeypatch, ImportScreen)
        run = RunSpy()
        monkeypatch.setattr(services, "run_import", run)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ImportScreen())
            await preview(pilot, screen)
            fill(screen, fee="0.00032")
            await pilot.pause()
            await pilot.press("ctrl+s")
            await settle(pilot)
            assert run.calls == []
            assert ImportScreen.CHANGED in toasts.messages

    @pytest.mark.asyncio
    async def test_ctrl_s_before_a_preview_asks_for_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        toasts = ToastSpy()
        toasts.install(monkeypatch, ImportScreen)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ImportScreen())
            fill(screen, path=PATH)
            await pilot.press("ctrl+s")
            await settle(pilot)
            assert app.screen is screen
            assert ImportScreen.NOT_PREVIEWED in toasts.messages


class TestReplace:
    @pytest.mark.asyncio
    async def test_the_checkbox_reaches_the_preview_and_the_table(self, monkeypatch: pytest.MonkeyPatch) -> None:
        spy = PreviewSpy()
        monkeypatch.setattr(services, "preview_import", spy)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ImportScreen())
            screen.query_one("#replace", Checkbox).value = True
            await preview(pilot, screen)
            assert spy.calls[0][2] is True
            assert table_rows(screen)[1] == ["MUND11", "substituídas", "0", "0", "2026-01-09 compra: 1 → 2 lançamentos"]
            assert "Substituídas: 1 (MUND11 2026-01-09 compra: 1 → 2 lançamentos)" in screen.note
            assert "Valor do dia difere" not in screen.note

    @pytest.mark.asyncio
    async def test_the_confirmation_says_what_leaves(self, monkeypatch: pytest.MonkeyPatch) -> None:
        run = RunSpy()
        monkeypatch.setattr(services, "run_import", run)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ImportScreen())
            screen.query_one("#replace", Checkbox).value = True
            await preview(pilot, screen)
            await pilot.press("ctrl+s")
            await settle(pilot)
            modal = app.screen
            assert isinstance(modal, ConfirmModal)
            assert (
                modal.body == "5 operações de negociacao.xlsx, no lugar de 1 lançamento\n4 compras, 1 venda, 2 tickers"
            )
            await pilot.press("enter")
            await settle(pilot)
            assert len(run.calls) == 1 and run.calls[0].replace

    @pytest.mark.asyncio
    async def test_toggling_it_after_the_preview_asks_for_another(self, monkeypatch: pytest.MonkeyPatch) -> None:
        toasts = ToastSpy()
        toasts.install(monkeypatch, ImportScreen)
        run = RunSpy()
        monkeypatch.setattr(services, "run_import", run)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ImportScreen())
            await preview(pilot, screen)
            screen.query_one("#replace", Checkbox).value = True
            await pilot.pause()
            assert submit_disabled(screen)
            await pilot.press("ctrl+s")
            await settle(pilot)
            assert run.calls == []
            assert ImportScreen.CHANGED in toasts.messages

    @pytest.mark.asyncio
    async def test_importar_outro_unchecks_it(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, ImportScreen())
            screen.query_one("#replace", Checkbox).value = True
            await preview(pilot, screen)
            await pilot.press("ctrl+s")
            await settle(pilot)
            await pilot.press("enter")  # confirma
            await settle(pilot)
            await pilot.press("enter")  # Importar outro
            await settle(pilot)
            assert screen.query_one("#replace", Checkbox).value is False
