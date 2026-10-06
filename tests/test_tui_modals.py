"""Tests for the shared behaviour of the dialogs (issue #74).

Two things every dialog owes the user: buttons that look like siblings, and a way
to pick between them that does not have to be guessed.
"""

from __future__ import annotations

from typing import Any

import pytest
from textual.widgets import Button, Input

from bogle.tui.screens.home import HomeScreen
from bogle.tui.screens.modals import ConfirmModal, EditModal, NextStepModal
from tests.tui_fakes import make_app, settle, stub_services


@pytest.fixture(autouse=True)
def _services(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_services(monkeypatch)


def buttons(screen: Any) -> list[Button]:
    return list(screen.query(Button))


def focused_label(app: Any) -> str:
    return str(app.focused.label) if isinstance(app.focused, Button) else repr(app.focused)


class TestButtonRow:
    @pytest.mark.asyncio
    async def test_the_two_buttons_have_the_same_height(self) -> None:
        # Um id nao tem escopo em CSS: o botao "Voltar à Home" chamava-se "home" e
        # herdava `#home` (o scroll da tela inicial), ganhando padding e ficando
        # duas linhas mais alto e quatro colunas mais largo que o vizinho.
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.app.push_screen(NextStepModal("transação 14 registrada: BUY B5P211."))
            await settle(pilot)
            heights = {button.outer_size.height for button in buttons(app.screen)}
            assert heights == {3}

    @pytest.mark.asyncio
    async def test_the_home_screen_keeps_its_own_padding(self) -> None:
        # A regra virou `HomeScreen #home`; escopar de menos apagaria o respiro da
        # tela inicial em vez de so poupar o botao.
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            assert isinstance(app.screen, HomeScreen)
            assert app.screen.query_one("#home").styles.padding.top == 1


class TestArrowNavigation:
    @pytest.mark.asyncio
    async def test_arrows_walk_the_buttons_of_the_next_step_dialog(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.app.push_screen(NextStepModal("transação 14 registrada: BUY B5P211."))
            await settle(pilot)
            assert focused_label(app) == "Novo lançamento"
            for key, expected in (
                ("right", "Voltar à Home"),
                ("left", "Novo lançamento"),
                ("down", "Voltar à Home"),
                ("up", "Novo lançamento"),
            ):
                await pilot.press(key)
                await pilot.pause()
                assert focused_label(app) == expected, key

    @pytest.mark.asyncio
    async def test_arrows_walk_the_confirmation_too(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.app.push_screen(ConfirmModal("Confirmar compra", "3 AUVP11 a 126.25"))
            await settle(pilot)
            assert focused_label(app) == "Confirmar"
            await pilot.press("right")
            await pilot.pause()
            assert focused_label(app) == "Cancelar"

    @pytest.mark.asyncio
    async def test_tab_still_works(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.app.push_screen(NextStepModal("transação 14 registrada."))
            await settle(pilot)
            await pilot.press("tab")
            await pilot.pause()
            assert focused_label(app) == "Voltar à Home"

    @pytest.mark.asyncio
    async def test_a_focused_field_keeps_the_horizontal_arrows(self) -> None:
        # As setas estao ligadas na tela, e a tela so as ve se o widget focado nao
        # as consumir: dentro do campo, esquerda e direita movem o cursor.
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.app.push_screen(EditModal("Preço de VWRA11", "Mercado: 114.84", value="114.86"))
            await settle(pilot)
            field = app.screen.query_one(Input)
            assert app.focused is field
            await pilot.press("right")
            await pilot.pause()
            assert app.focused is field
            await pilot.press("down")  # a vertical, sim, sai do campo
            await pilot.pause()
            assert focused_label(app) == "Salvar"
