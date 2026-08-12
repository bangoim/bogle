"""The Textual application (issue #73).

Owns the global wiring only — theme, the screen stack and the two helpers every
screen needs (report an expected failure as a toast, jump back to Home). All
content lives in :mod:`bogle.tui.screens`.
"""

from __future__ import annotations

from pathlib import Path
from typing import override

from textual.app import App
from textual.screen import Screen

from bogle.tui.errors import message_for
from bogle.tui.screens.home import HomeScreen


class BogleApp(App[None]):
    """``bogle`` with no arguments."""

    CSS_PATH = Path(__file__).with_name("app.tcss")
    TITLE = "bogle"
    SUB_TITLE = "rebalanceamento passivo"

    def __init__(self) -> None:
        super().__init__()
        # Classes CSS por tamanho de terminal: um terminal baixo esconde o logo
        # (para o menu nao sumir) e um estreito empilha o resumo em uma coluna.
        # Definidos aqui, e nao no corpo da classe, porque a anotacao da base e
        # `ClassVar[...] | None` — que nenhum dos dois linters aceita como valida.
        self.VERTICAL_BREAKPOINTS = [(0, "-short"), (24, "-tall")]
        self.HORIZONTAL_BREAKPOINTS = [(0, "-narrow"), (72, "-normal")]

    @override
    def get_default_screen(self) -> Screen[None]:
        return HomeScreen()

    def on_mount(self) -> None:
        self.theme = "textual-dark"

    def report(self, exc: BaseException, *, title: str = "erro") -> None:
        """Surface an expected failure as a toast, keeping the user in place.

        ``markup=False`` because the message can carry user data (a ticker, a
        provider's own error text) that must not be read as Rich markup.
        """
        self.notify(message_for(exc), title=title, severity="error", timeout=10, markup=False)

    def go_home(self) -> None:
        """Drop every screen above Home (used after recording a transaction)."""
        while len(self.screen_stack) > 1:
            self.pop_screen()
