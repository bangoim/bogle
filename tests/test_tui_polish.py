"""Tests for the polish pass (issue #77): the help overlay and the narrow
terminal.

The screen sweep here is the one guard that covers *every* screen at once: it
opens each of them at 80x24 and checks the two properties that break silently —
a footer wider than the terminal (Textual clips it, so a key disappears
mid-word) and a screen that cannot render at that size at all.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from rich.text import Text
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Checkbox, Footer, Select, Static

from bogle.domain.assets import AssetType
from bogle.domain.transactions import TransactionType
from bogle.tui.screens.assets import AssetFormScreen, AssetsScreen, AssetUpdateScreen
from bogle.tui.screens.compare import CompareScreen
from bogle.tui.screens.config import ConfigScreen
from bogle.tui.screens.help import HelpModal, shortcuts_of
from bogle.tui.screens.history import HistoryScreen
from bogle.tui.screens.home import HomeScreen
from bogle.tui.screens.income import IncomeScreen
from bogle.tui.screens.position import PositionScreen
from bogle.tui.screens.profit import ProfitScreen
from bogle.tui.screens.register import IncomeFormScreen, RegisterScreen, TradeFormScreen
from bogle.tui.screens.reports import MENU_ITEMS as REPORT_ITEMS
from bogle.tui.screens.reports import ReportsScreen
from bogle.tui.screens.returns import ReturnsScreen
from bogle.tui.screens.status import StatusScreen
from bogle.tui.screens.suggest import SuggestScreen
from bogle.tui.screens.transactions import TransactionsScreen
from bogle.tui.widgets.menu import Menu
from tests.tui_fakes import make_app, make_assets, open_screen, settle, stub_services

NARROW = (80, 24)
"""The size the plan committed to: the position table has eleven columns."""

SCREENS: dict[str, Callable[[], Screen[None]]] = {
    "posicao": PositionScreen,
    "registrar": RegisterScreen,
    "compra": lambda: TradeFormScreen(kind=TransactionType.BUY),
    "venda": lambda: TradeFormScreen(kind=TransactionType.SELL),
    "provento": IncomeFormScreen,
    "transacoes": TransactionsScreen,
    "aporte": SuggestScreen,
    "relatorios": ReportsScreen,
    "rentabilidade": ReturnsScreen,
    "comparar": CompareScreen,
    "historico": HistoryScreen,
    "lucro": ProfitScreen,
    "proventos": IncomeScreen,
    "ativos": AssetsScreen,
    "cadastrar ativo": AssetFormScreen,
    "alterar ativo": lambda: AssetUpdateScreen(make_assets()[1]),
    "status": StatusScreen,
    "config": ConfigScreen,
}


@pytest.fixture(autouse=True)
def _services(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_services(monkeypatch)


def footer_width(screen: Screen[Any]) -> int:
    """Columns the footer takes, as Textual laid it out.

    The footer is a scrollable container: when it does not fit it is clipped, not
    reflowed, so the sum of what it drew is what has to fit the terminal.
    """
    footer = screen.query_one(Footer)
    return sum(key.region.width for key in footer.walk_children(Widget) if key.display)


class TestNarrowTerminal:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", list(SCREENS))
    async def test_the_footer_fits_in_eighty_columns(self, name: str) -> None:
        app = make_app()
        async with app.run_test(size=NARROW) as pilot:
            screen = await open_screen(pilot, SCREENS[name]())
            width = footer_width(screen)
            assert width <= NARROW[0], f"{name}: footer com {width} colunas"

    @pytest.mark.asyncio
    async def test_the_home_footer_fits_too(self) -> None:
        app = make_app()
        async with app.run_test(size=NARROW) as pilot:
            await settle(pilot)
            assert footer_width(app.screen) <= NARROW[0]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", list(SCREENS))
    async def test_every_screen_renders_at_eighty_columns(self, name: str) -> None:
        # Uma tela que nao consegue se desenhar nesse tamanho estoura aqui (o
        # grafico do plotext, por exemplo, precisa de altura minima).
        app = make_app()
        async with app.run_test(size=NARROW) as pilot:
            screen = await open_screen(pilot, SCREENS[name]())
            assert screen.is_mounted
            assert app.screen is screen

    @pytest.mark.asyncio
    async def test_a_short_terminal_drops_the_logo_to_keep_the_menu(self) -> None:
        app = make_app()
        async with app.run_test(size=(80, 20)) as pilot:
            await settle(pilot)
            home = app.screen
            assert isinstance(home, HomeScreen)
            assert home.has_class("-short")
            assert home.query_one("#logo", Static).display is False


class TestHelpOverlay:
    @pytest.mark.asyncio
    async def test_question_mark_opens_the_shortcuts_of_the_current_screen(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, CompareScreen())
            await pilot.press("question_mark")
            await settle(pilot)
            modal = app.screen
            assert isinstance(modal, HelpModal)
            assert modal.subject == "comparar - 12m"
            keys = dict(modal.shortcuts)
            assert keys["t"] == "Periodo"
            assert keys["i"] == "Indices"
            assert keys["o"] == "Exportar"
            assert keys["esc"] == "Voltar"

    @pytest.mark.asyncio
    async def test_the_global_shortcuts_are_listed_too(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, StatusScreen())
            await pilot.press("question_mark")
            await settle(pilot)
            modal = app.screen
            assert isinstance(modal, HelpModal)
            keys = dict(modal.shortcuts)
            assert keys["h"] == "Valores"
            # f1 e ? sao a mesma acao: uma linha com as duas teclas.
            assert keys["f1 ?"] == "Ajuda"

    @pytest.mark.asyncio
    async def test_the_framework_own_keys_are_left_out(self) -> None:
        # tab/shift+tab/copy vem das bases do textual e estariam em toda tela,
        # afogando os quatro atalhos que a tela realmente tem.
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, StatusScreen())
            await pilot.press("question_mark")
            await settle(pilot)
            modal = app.screen
            assert isinstance(modal, HelpModal)
            assert "tab" not in dict(modal.shortcuts)

    @pytest.mark.asyncio
    async def test_the_home_lists_the_menu_numbers(self) -> None:
        # Os numeros nao aparecem no footer (o menu ja os mostra), mas sao
        # exatamente o que quem pede ajuda quer ver.
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            await pilot.press("question_mark")
            await settle(pilot)
            modal = app.screen
            assert isinstance(modal, HelpModal)
            keys = dict(modal.shortcuts)
            assert keys["1"] == "Posicao"
            assert keys["8"] == "Config"
            assert keys["q"] == "Sair"
            assert modal.subject == ""  # a Home nao tem subtitulo proprio

    @pytest.mark.asyncio
    async def test_question_mark_again_closes_it(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, StatusScreen())
            await pilot.press("question_mark")
            await settle(pilot)
            assert isinstance(app.screen, HelpModal)
            await pilot.press("question_mark")
            await settle(pilot)
            assert isinstance(app.screen, StatusScreen)

    @pytest.mark.asyncio
    async def test_escape_closes_it_and_leaves_the_screen_underneath_alone(self) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, StatusScreen())
            panel = screen.panel
            await pilot.press("question_mark")
            await settle(pilot)
            await pilot.press("escape")
            await settle(pilot)
            assert app.screen is screen
            assert screen.panel == panel  # nada recarregado por causa da ajuda

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", list(SCREENS))
    async def test_every_screen_has_help(self, name: str) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, SCREENS[name]())
            await pilot.press("f1")
            await settle(pilot)
            modal = app.screen
            assert isinstance(modal, HelpModal), name
            assert modal.shortcuts, name


class TestShortcutConsistency:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", list(SCREENS))
    async def test_escape_always_goes_back(self, name: str) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, SCREENS[name]())
            assert dict(shortcuts_of(app.screen)).get("esc") == "Voltar", name

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", list(SCREENS))
    async def test_no_two_shortcuts_on_a_screen_read_the_same(self, name: str) -> None:
        # Duas linhas do footer dizendo "Atualizar" nao diriam nada.
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, SCREENS[name]())
            descriptions = [description for _, description in shortcuts_of(app.screen)]
            assert len(descriptions) == len(set(descriptions)), f"{name}: {descriptions}"

    @pytest.mark.asyncio
    async def test_the_asset_form_type_selector_accepts_every_type(self) -> None:
        # Um tipo fora do seletor so poderia ser cadastrado pela CLI; o textual
        # recusa um valor que nao esta entre as opcoes, entao percorrer todos vale
        # como cobertura da lista.
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, AssetFormScreen())
            for kind in AssetType:
                screen.query_one("#asset-type", Select).value = kind
                await pilot.pause()
                assert screen.asset_type is kind


class TestFormLayout:
    def test_the_menu_label_column_follows_the_widest_label(self) -> None:
        # "Rentabilidade" tem 13 caracteres: com a coluna fixa em 12 ela encostava
        # na descricao e as duas viravam uma palavra so.
        menu = Menu(REPORT_ITEMS)
        prompts = [menu.get_option_at_index(index).prompt for index in range(menu.option_count)]
        plain = [prompt.plain if isinstance(prompt, Text) else str(prompt) for prompt in prompts]
        assert "Rentabilidade  TWR total, 12m e ultimo mes" in plain[0]

    @pytest.mark.asyncio
    async def test_a_checkbox_row_shows_its_whole_marker(self) -> None:
        # Um Checkbox sem rotulo mede 3 celulas em largura automatica e corta o
        # proprio marcador ("▐X▌" virava "▐X…").
        app = make_app()
        async with app.run_test(size=NARROW) as pilot:
            screen = await open_screen(pilot, AssetFormScreen())
            screen.query_one("#asset-type", Select).value = AssetType.CDB
            await pilot.pause()
            assert screen.query_one("#prefixed", Checkbox).size.width >= 4
