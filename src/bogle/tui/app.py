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
from typing import Any, ClassVar, Protocol, override, runtime_checkable

from textual import work
from textual.app import App
from textual.binding import Binding, BindingType
from textual.screen import Screen

from bogle import format as fmt
from bogle.settings import DECIMAL_SEPARATOR, DEFAULT_THEME, HIDE_VALUES, THEME
from bogle.tui import services
from bogle.tui.errors import HANDLED, message_for
from bogle.tui.screens.config import ConfigScreen
from bogle.tui.screens.help import HelpModal, shortcuts_of
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
        # `f1` e o atalho anunciado porque funciona em qualquer tela: enquanto um
        # Input tem foco, ele consome as teclas imprimiveis, e o `?` viraria texto
        # digitado no campo. O `?` fica como apelido, para quem tenta o obvio.
        Binding("f1", "help", "Ajuda", tooltip="Atalhos desta tela"),
        Binding("question_mark", "help", "Ajuda", tooltip="Atalhos desta tela", show=False),
    ]

    def __init__(self, *, theme: str = DEFAULT_THEME) -> None:
        super().__init__()
        # Classes CSS por tamanho de terminal: um terminal baixo esconde o logo
        # (para o menu nao sumir) e um estreito empilha o resumo em uma coluna.
        # Definidos aqui, e nao no corpo da classe, porque a anotacao da base e
        # `ClassVar[...] | None` — que nenhum dos dois linters aceita como valida.
        self.VERTICAL_BREAKPOINTS = [(0, "-short"), (24, "-tall")]
        # `-wide` e onde o menu da Home cabe em duas colunas sem quebrar as
        # descricoes: duas colunas de ~50 mais as bordas e o respiro da tela.
        self.HORIZONTAL_BREAKPOINTS = [(0, "-narrow"), (72, "-normal"), (108, "-wide")]
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
        self._warn_unknown_theme(self._preferred_theme)

    def _warn_unknown_theme(self, theme: str) -> None:
        # So acontece se um tema sair do textual entre duas sessoes: `config set`
        # valida o nome contra a lista da versao instalada.
        self.notify(
            f"tema '{theme}' nao existe nesta versao; usando {self.theme}.",
            severity="warning",
            markup=False,
        )

    # --- ajuda ----------------------------------------------------------

    def action_help(self) -> None:
        """Show (or dismiss) the shortcuts of the screen underneath."""
        screen = self.screen
        if isinstance(screen, HelpModal):
            screen.dismiss()
            return
        # A Home nao define subtitulo proprio: nela `sub_title` vem vazio ou cai no
        # da App, que nomeia o programa, nao a tela.
        own = bool(screen.sub_title) and screen.sub_title != self.sub_title
        subject = str(screen.sub_title) if own else ""
        self.push_screen(HelpModal(shortcuts_of(screen), subject=subject))

    # --- preferencias que a interface muda por dentro --------------------

    def action_toggle_amounts(self) -> None:
        """Hide or show every amount, and remember the choice.

        Redraws from the data already loaded — no round trip for the toggle
        itself; only the preference is written, in a worker.
        """
        hidden = not fmt.amounts_hidden()
        fmt.hide_amounts(hidden)
        self.redraw_amounts()
        self.notify("Valores ocultos." if hidden else "Valores visiveis.", timeout=3)
        self._remember_hidden(hidden)

    def redraw_amounts(self) -> None:
        """Redraw the current screen's amounts from the data it already holds."""
        screen = self.screen
        if isinstance(screen, ShowsAmounts):
            screen.render_amounts()

    def on_config_screen_preference_changed(self, event: ConfigScreen.PreferenceChanged) -> None:
        self.apply_preference(event.key, event.value)

    def apply_preference(self, key: str, value: Any) -> None:
        """Make a setting written from the Config screen take effect right away.

        Three of the keys are read once, at startup (theme, decimal separator,
        privacy mode). Without this, editing them from the inside would only show
        up in the next session — which reads as the edit not having worked.
        """
        if key == THEME and isinstance(value, str):
            self._show_theme(value)
        elif key == HIDE_VALUES:
            fmt.hide_amounts(bool(value))
            self.redraw_amounts()
        elif key == DECIMAL_SEPARATOR and isinstance(value, str):
            fmt.configure(value)
            self.redraw_amounts()

    def _show_theme(self, theme: str) -> None:
        if theme not in self.available_themes:
            self._warn_unknown_theme(theme)
            return
        # A tela de Config acabou de gravar: marcar como salvo evita a segunda
        # escrita que o watcher faria.
        self._saved_theme = theme
        self.theme = theme

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
