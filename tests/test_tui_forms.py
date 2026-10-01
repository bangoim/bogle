"""Tests for the shared behaviour of the write forms (issues #74, #76).

Keyboard and legibility, not what gets written: whether the arrows walk the
controls, and whether a control that has the focus looks like it.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from textual.widgets import Button, Input, Select
from textual.widgets._select import SelectCurrent, SelectOverlay

from bogle.domain.assets import Asset, AssetType
from bogle.domain.transactions import TransactionType
from bogle.tui.screens.assets import AssetUpdateScreen
from bogle.tui.screens.register import TradeFormScreen
from tests.tui_fakes import make_app, open_screen, settle, stub_services

ASSET = Asset(ticker="B5P211", target_weight=Decimal("0.2"), asset_type=AssetType.ETF)


@pytest.fixture(autouse=True)
def _services(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_services(monkeypatch)


def where(app: Any) -> str:
    node = app.focused
    if isinstance(node, Button):
        return str(node.label)
    if isinstance(node, Select):
        return "Select"
    if isinstance(node, SelectOverlay):
        return "SelectOverlay"
    if isinstance(node, Input):
        return "Input"
    return type(node).__name__


class TestArrowNavigation:
    @pytest.mark.asyncio
    async def test_arrows_walk_from_the_field_to_the_buttons(self) -> None:
        # A queixa: com o Tab como unica saida, chegar nos botoes e algo que se
        # descobre por tentativa.
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, AssetUpdateScreen(ASSET))
            assert where(app) == "Input"  # abre no peso-alvo
            await pilot.press("down")
            await pilot.pause()
            assert where(app) == "Select"
            await pilot.press("right")
            await pilot.pause()
            assert where(app) == "Atualizar"
            await pilot.press("right")
            await pilot.pause()
            assert where(app) == "Voltar"
            await pilot.press("left")
            await pilot.pause()
            assert where(app) == "Atualizar"

    @pytest.mark.asyncio
    async def test_the_text_field_keeps_the_horizontal_arrows(self) -> None:
        # As setas estao ligadas na tela, que so as ve quando o widget focado nao
        # as quer: dentro do campo, esquerda e direita movem o cursor.
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, AssetUpdateScreen(ASSET))
            field = screen.field("weight").input
            assert app.focused is field
            await pilot.press("left")
            await pilot.pause()
            assert app.focused is field

    @pytest.mark.asyncio
    async def test_a_collapsed_select_keeps_the_vertical_arrows(self) -> None:
        # Baixo/cima num Select fechado e o gesto de abrir a lista, e continua
        # sendo dele.
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, AssetUpdateScreen(ASSET))
            select = screen.query_one(Select)
            select.focus()
            await pilot.pause()
            await pilot.press("down")
            await pilot.pause()
            assert select.expanded

    @pytest.mark.asyncio
    async def test_stepping_sideways_out_of_an_open_list_closes_it(self) -> None:
        # Nao ha nada horizontal para fazer numa lista aberta, entao a seta leva
        # ao controle seguinte — e a lista nao pode ficar pendurada na tela.
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, AssetUpdateScreen(ASSET))
            select = screen.query_one(Select)
            select.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert select.expanded
            await pilot.press("right")
            await pilot.pause()
            assert not select.expanded
            assert not select.query_one(SelectOverlay).display

    @pytest.mark.asyncio
    async def test_the_ledger_forms_walk_the_same_way(self) -> None:
        # Mesma base (WriteScreen), entao o registro de compra herda o mesmo
        # teclado: aqui os controles seguidos sao todos campos de texto, e o que
        # importa e a vertical andar entre eles.
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, TradeFormScreen(kind=TransactionType.BUY))
            first = app.focused
            assert isinstance(first, Input)
            await pilot.press("down")
            await pilot.pause()
            assert isinstance(app.focused, Input)
            assert app.focused is not first
            await pilot.press("up")
            await pilot.pause()
            assert app.focused is first


class TestSelectLegibility:
    @pytest.mark.asyncio
    async def test_the_focused_select_does_not_look_like_the_resting_one(self) -> None:
        # O compacto do textual apaga a borda com `!important`, o que apagava a
        # borda de foco: focado e em repouso ficavam identicos, e o Tab parecia
        # nao ter ido a lugar nenhum.
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, AssetUpdateScreen(ASSET))
            select = screen.query_one(Select)
            current = select.query_one(SelectCurrent)
            resting = (current.styles.background, current.styles.text_style)
            select.focus()
            await pilot.pause()
            focused = (current.styles.background, current.styles.text_style)
            assert focused != resting

    @pytest.mark.asyncio
    async def test_the_list_is_as_wide_as_the_options_not_as_the_form(self) -> None:
        # ETF, BDR, FII, STOCK numa lista da largura do formulario inteiro.
        app = make_app()
        async with app.run_test(size=(96, 24)) as pilot:
            screen = await open_screen(pilot, AssetUpdateScreen(ASSET))
            select = screen.query_one(Select)
            select.focus()
            await pilot.pause()
            await pilot.press("enter")
            await settle(pilot)
            overlay = select.query_one(SelectOverlay)
            assert overlay.outer_size.width < select.outer_size.width
            assert overlay.outer_size.width <= 16  # cabe "STOCK" com folga, e nada mais
