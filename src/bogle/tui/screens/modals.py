"""Modal dialogs (issue #74).

Two of them: confirm before writing, and ask what to do after writing. The
second one exists because the common case is recording several tickers on the
same day — going back to Home after each entry would be busywork.

Titles and bodies are rendered as plain text (``markup=False``): they quote user
data — a ticker, a provider's error message — which must never be read as markup.
"""

from __future__ import annotations

from typing import ClassVar, override

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label

NEW_ENTRY = "new"
GO_HOME = "home"


class ConfirmModal(ModalScreen[bool]):
    """Yes/no over a summary of what is about to happen."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Cancelar")]

    def __init__(self, title: str, body: str, *, confirm_label: str = "Confirmar") -> None:
        super().__init__()
        self.dialog_title = title
        self.body = body
        self.confirm_label = confirm_label

    @override
    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self.dialog_title, id="dialog-title", markup=False)
            yield Label(self.body, id="dialog-body", markup=False)
            with Horizontal(id="dialog-buttons"):
                yield Button(self.confirm_label, id="confirm", variant="primary")
                yield Button("Cancelar", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#confirm", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")

    def action_cancel(self) -> None:
        self.dismiss(False)


class NextStepModal(ModalScreen[str]):
    """After recording: another entry of the same kind, or back to Home."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "home", "Voltar a Home")]

    def __init__(self, recorded: str) -> None:
        super().__init__()
        self.recorded = recorded

    @override
    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Lancamento registrado", id="dialog-title")
            yield Label(self.recorded, id="dialog-body", markup=False)
            yield Label("O que fazer agora?", id="dialog-question")
            with Horizontal(id="dialog-buttons"):
                yield Button("Novo lancamento", id=NEW_ENTRY, variant="primary")
                yield Button("Voltar a Home", id=GO_HOME)

    def on_mount(self) -> None:
        self.query_one(f"#{NEW_ENTRY}", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id or GO_HOME)

    def action_home(self) -> None:
        self.dismiss(GO_HOME)
