"""The Textual application (issue #73).

Owns the global wiring: theme, size breakpoints, the default screen and the
privacy toggle — global because it applies to every screen that shows an amount.
All content lives in :mod:`bogle.tui.screens`.

Both preferences the interface can change from the inside (privacy mode, theme)
are written back to ``user_settings`` as they change, so the next session opens
the way the last one was left.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Protocol, override, runtime_checkable

from textual import work
from textual.app import App
from textual.binding import Binding, BindingType
from textual.screen import Screen

from bogle import format as fmt
from bogle.settings import DEFAULT_THEME
from bogle.tui import services
from bogle.tui.errors import HANDLED, message_for
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

    def __init__(self, *, theme: str = DEFAULT_THEME) -> None:
        super().__init__()
        # Classes CSS por tamanho de terminal: um terminal baixo esconde o logo
        # (para o menu nao sumir) e um estreito empilha o resumo em uma coluna.
        # Definidos aqui, e nao no corpo da classe, porque a anotacao da base e
        # `ClassVar[...] | None` — que nenhum dos dois linters aceita como valida.
        self.VERTICAL_BREAKPOINTS = [(0, "-short"), (24, "-tall")]
        self.HORIZONTAL_BREAKPOINTS = [(0, "-narrow"), (72, "-normal")]
        self._preferred_theme = theme
        self._saved_theme = theme
        """The theme currently in ``user_settings``; guards a pointless write."""

    @override
    def get_default_screen(self) -> Screen[None]:
        return HomeScreen()

    def on_mount(self) -> None:
        if self._preferred_theme in self.available_themes:
            self.theme = self._preferred_theme
            return
        # So acontece se um tema sair do textual entre duas sessoes: `config set`
        # valida o nome contra a lista da versao instalada.
        self.notify(
            f"tema '{self._preferred_theme}' nao existe nesta versao; usando {self.theme}.",
            severity="warning",
            markup=False,
        )

    # --- preferencias que a interface muda por dentro --------------------

    def action_toggle_amounts(self) -> None:
        """Hide or show every amount, and remember the choice.

        Redraws from the data already loaded — no round trip for the toggle
        itself; only the preference is written, in a worker.
        """
        hidden = not fmt.amounts_hidden()
        fmt.hide_amounts(hidden)
        screen = self.screen
        if isinstance(screen, ShowsAmounts):
            screen.render_amounts()
        self.notify("Valores ocultos." if hidden else "Valores visiveis.", timeout=3)
        self._remember_hidden(hidden)

    def watch_theme(self, theme_name: str) -> None:
        """Remember a theme picked in the command palette (``ctrl+p``)."""
        if theme_name == self._saved_theme:
            return
        self._saved_theme = theme_name
        self._remember_theme(theme_name)

    @work(thread=True, group="preferences")
    def _remember_hidden(self, hidden: bool) -> None:
        try:
            services.save_hide_amounts(hidden)
        except HANDLED as exc:
            self._warn_not_remembered(message_for(exc))

    @work(thread=True, group="preferences")
    def _remember_theme(self, theme: str) -> None:
        try:
            services.save_theme(theme)
        except HANDLED as exc:
            self._warn_not_remembered(message_for(exc))

    def _warn_not_remembered(self, reason: str) -> None:
        # A preferencia vale nesta sessao; so nao vai valer na proxima.
        self.call_from_thread(
            self.notify,
            f"a preferencia vale nesta sessao, mas nao foi salva: {reason}",
            severity="warning",
            timeout=8,
            markup=False,
        )
