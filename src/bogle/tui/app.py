"""The Textual application (issue #73).

Owns the global wiring only — theme, size breakpoints, the default screen and the
privacy toggle, which is global because it applies to every screen that shows an
amount. All content lives in :mod:`bogle.tui.screens`.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Protocol, override, runtime_checkable

from textual.app import App
from textual.binding import Binding, BindingType
from textual.screen import Screen

from bogle import format as fmt
from bogle.tui.screens.home import HomeScreen


@runtime_checkable
class ShowsAmounts(Protocol):
    """A screen that renders amounts and can redraw them from what it has loaded."""

    def render_amounts(self) -> None: ...


class BogleApp(App[None]):
    """``bogle`` with no arguments."""

    CSS_PATH = Path(__file__).with_name("app.tcss")
    TITLE = "bogle"
    SUB_TITLE = "rebalanceamento passivo"
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("h", "toggle_amounts", "Valores", tooltip="Ocultar ou mostrar os valores"),
    ]

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

    def action_toggle_amounts(self) -> None:
        """Hide or show every amount, for the rest of the session.

        Only for this session: ``hide_values`` decides how the interface *opens*,
        which is what actually protects a screen someone else may be looking at.
        Redraws from the data already loaded — no round trip.
        """
        hidden = not fmt.amounts_hidden()
        fmt.hide_amounts(hidden)
        screen = self.screen
        if isinstance(screen, ShowsAmounts):
            screen.render_amounts()
        self.notify("Valores ocultos." if hidden else "Valores visiveis.", timeout=3)
