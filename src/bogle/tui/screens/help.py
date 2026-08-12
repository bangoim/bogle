"""Contextual help overlay (issue #77).

``?`` lists the shortcuts of the screen you are on. The list is read from the
screens' own ``BINDINGS`` (walking the class hierarchy) rather than from what
Textual reports as *active*, because the active set also carries the focused
widget's internals — a ``DataTable`` alone contributes a dozen cursor keys, which
would bury the four keys the screen is actually about.

Hidden bindings are included on purpose: the menu digits do not belong in the
footer (the menu already spells them out) but they are exactly what someone
asking for help wants to see.
"""

from __future__ import annotations

from typing import Any, ClassVar, override

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Label, Static

_FOOTER = "Setas e Enter navegam. ctrl+p abre a paleta de comandos; ctrl+q sai."

_KEY_LABELS = {"escape": "esc", "question_mark": "?", "ctrl+s": "^s"}
"""Long key names as the footer writes them."""


def _readable(key: str) -> str:
    return _KEY_LABELS.get(key, key)


def shortcuts_of(screen: Screen[Any]) -> list[tuple[str, str]]:
    """Key/description pairs declared by ``screen``, its bases and the App.

    Most specific first (the screen's own keys before the global ones), each key
    listed once — a subclass that rebinds a key wins, which is what the footer
    shows too. Only classes from this project are read: Textual's own bases would
    add tab/shift+tab/copy to every screen, which is noise here (the framework
    keys that are worth knowing are in the footer line instead).
    """
    pairs: list[tuple[str, str]] = []
    rows: dict[str, int] = {}
    seen: set[str] = set()
    for owner in (*type(screen).__mro__, type(screen.app)):
        if not owner.__module__.startswith("bogle."):
            continue
        for binding in owner.__dict__.get("BINDINGS", ()):
            key, description = _describe(binding)
            if key is None or key in seen or not description:
                continue
            seen.add(key)
            if description in rows:
                # Apelido da mesma acao (f1 e ?): uma linha com as duas teclas,
                # em vez de duas linhas dizendo a mesma coisa.
                index = rows[description]
                pairs[index] = (f"{pairs[index][0]} {_readable(key)}", description)
                continue
            rows[description] = len(pairs)
            pairs.append((_readable(key), description))
    return pairs


def _describe(binding: BindingType) -> tuple[str | None, str]:
    """``(key, description)`` of a binding written either way Textual allows."""
    if isinstance(binding, Binding):
        return binding.key, binding.description
    if isinstance(binding, tuple) and len(binding) >= 3:
        return str(binding[0]), str(binding[2])
    return None, ""


class HelpModal(ModalScreen[None]):
    """The shortcuts of the screen underneath."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "dismiss_help", "Fechar"),
        # As duas teclas que abrem tambem fecham. Precisam estar aqui: um
        # ModalScreen nao deixa a tecla chegar ao binding da App.
        Binding("f1", "dismiss_help", "Fechar", show=False),
        Binding("question_mark", "dismiss_help", "Fechar", show=False),
    ]

    def __init__(self, shortcuts: list[tuple[str, str]], *, subject: str = "") -> None:
        super().__init__()
        self.shortcuts = shortcuts
        self.subject = subject

    @override
    def compose(self) -> ComposeResult:
        with Vertical(id="help"):
            # markup=False: o subtitulo pode citar um ticker ("atualizar TES[/]").
            yield Label(
                f"Atalhos - {self.subject}" if self.subject else "Atalhos desta tela",
                id="help-title",
                markup=False,
            )
            with VerticalScroll(id="help-keys"):
                yield Static(_table(self.shortcuts), id="help-table")
            yield Static(_FOOTER, id="help-footer")

    def action_dismiss_help(self) -> None:
        self.dismiss()


def _table(shortcuts: list[tuple[str, str]]) -> Text:
    if not shortcuts:
        return Text("Esta tela nao tem atalhos proprios.")
    width = max(len(key) for key, _ in shortcuts)
    lines = Text()
    for index, (key, description) in enumerate(shortcuts):
        if index:
            lines.append("\n")
        lines.append(key.rjust(width), style="bold")
        lines.append(f"  {description}")
    return lines
