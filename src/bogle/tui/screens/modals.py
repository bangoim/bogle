"""Modal dialogs (issue #74).

Confirm before writing, edit one value, ask what to do after writing, and report
a change the app made on its own. The "what next" one exists because the common
case is recording several tickers on the same day — going back to Home after each
entry would be busywork.

Titles and bodies are rendered as plain text (``markup=False``): they quote user
data — a ticker, a provider's error message — which must never be read as markup.

Every dialog here is a row of buttons, so the arrows walk it: ``tab`` alone is
the kind of thing that has to be guessed, and a two-button question is exactly
where the hand reaches for a direction.
"""

from __future__ import annotations

from typing import ClassVar, override

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label

from bogle.tui.navigation import ARROW_FOCUS

# Prefixados com "dialog-": um id nao tem escopo em CSS, e um botao chamado
# "home" herdava as regras de `#home` (o scroll da tela inicial) — que e padding
# e centralizacao, e deixavam este botao mais alto e mais largo que o vizinho.
NEW_ENTRY = "dialog-new"
GO_HOME = "dialog-home"
REVERT = "dialog-revert"


class ButtonRowModal[T](ModalScreen[T]):
    """A dialog whose choices are a row of buttons, walkable with the arrows.

    The same :data:`~bogle.tui.navigation.ARROW_FOCUS` the forms use — a dialog
    and a form are the same problem from the keyboard's point of view.
    """

    BINDINGS: ClassVar[list[BindingType]] = list(ARROW_FOCUS)


class ConfirmModal(ButtonRowModal[bool]):
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


class EditModal(ButtonRowModal[str | None]):
    """One value, editable in place: ``Enter`` confirms, ``Esc`` cancels.

    Hands back the raw string, never a parsed value: whoever opened it is the one
    that knows the type (for a setting, ``settings.set_setting`` both parses and
    refuses), and its error message is what the user needs to see.
    """

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Cancelar")]

    def __init__(self, title: str, body: str, *, value: str = "", placeholder: str = "") -> None:
        super().__init__()
        self.dialog_title = title
        self.body = body
        self.value = value
        self.placeholder = placeholder

    @override
    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self.dialog_title, id="dialog-title", markup=False)
            yield Label(self.body, id="dialog-body", markup=False)
            yield Input(value=self.value, placeholder=self.placeholder, compact=True, id="dialog-input")
            with Horizontal(id="dialog-buttons"):
                yield Button("Salvar", id="confirm", variant="primary")
                yield Button("Cancelar", id="cancel")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    @property
    def typed(self) -> str:
        return self.query_one(Input).value

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.dismiss(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(self.typed if event.button.id == "confirm" else None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ClearedTargetModal(ButtonRowModal[bool]):
    """Something the app decided by itself, with the way back in the same breath.

    Dismisses ``True`` when the user wants it undone. "Manter assim" is the
    focused button and what ``Esc`` does: the change is already applied and it is
    the expected outcome of the sale — reverting is the deliberate choice, so it
    is the one that has to be reached for.
    """

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "keep", "Manter")]

    def __init__(self, notice: str) -> None:
        super().__init__()
        self.notice = notice

    @override
    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Target removido", id="dialog-title")
            yield Label(self.notice, id="dialog-body", markup=False)
            yield Label("Reverter e devolver o peso-alvo ao ativo.", id="dialog-question")
            with Horizontal(id="dialog-buttons"):
                yield Button("Manter assim", id="confirm", variant="primary")
                yield Button("Reverter", id=REVERT)

    def on_mount(self) -> None:
        self.query_one("#confirm", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == REVERT)

    def action_keep(self) -> None:
        self.dismiss(False)


class NextStepModal(ButtonRowModal[str]):
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
