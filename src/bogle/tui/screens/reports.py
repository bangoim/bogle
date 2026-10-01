"""Reports submenu (issue #75).

The five report commands, one screen each. They share the load-in-a-worker flow
(:class:`bogle.tui.screens.data.DataScreen`) and the numbers with the CLI — every
one of them calls the same ``reports/`` engine the command calls.
"""

from __future__ import annotations

from typing import ClassVar, override

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.widgets import Footer, Header

from bogle.tui.screens.compare import CompareScreen
from bogle.tui.screens.history import HistoryScreen
from bogle.tui.screens.income import IncomeScreen
from bogle.tui.screens.menu import Entries, MenuScreen, items_of
from bogle.tui.screens.profit import ProfitScreen
from bogle.tui.screens.returns import ReturnsScreen
from bogle.tui.widgets.menu import Menu, MenuItem, menu_bindings

_ENTRIES: Entries = (
    (MenuItem("1", "returns", "Rentabilidade", "TWR total, 12m e ultimo mes"), ReturnsScreen),
    (MenuItem("2", "compare", "Comparar", "carteira v. indices, base 100"), CompareScreen),
    (MenuItem("3", "history", "Historico", "evolucao do patrimonio"), HistoryScreen),
    (MenuItem("4", "profit", "Lucro", "ganho de capital + proventos"), ProfitScreen),
    (MenuItem("5", "income", "Proventos", "por mes ou por ticker"), IncomeScreen),
)

MENU_ITEMS = items_of(_ENTRIES)


class ReportsScreen(MenuScreen):
    """Which report to open."""

    SUB_TITLE = "relatorios"
    AUTO_FOCUS = "#reports-menu"
    ENTRIES = _ENTRIES
    MENU_TITLE = "Relatorios"
    MENU_FRAME = "#reports-menu"
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "app.pop_screen", "Voltar"),
        *menu_bindings(MENU_ITEMS),
    ]

    @override
    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="reports"):
            yield Menu(MENU_ITEMS, id="reports-menu")
        yield Footer()
