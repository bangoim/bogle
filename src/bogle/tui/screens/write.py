"""Shared shape of a form that writes to the database (issues #74, #76).

The ledger forms and the asset form do the same five things: validate every field
(focusing the first one that fails), show a summary and ask before writing, write
in a worker thread, keep the user on the form when the write fails — with
everything typed still there, which is the whole gain over the CLI — and refuse to
leave mid-write. That flow lives here; what differs is what gets written
(:meth:`WriteScreen.write`) and what happens afterwards
(:meth:`WriteScreen.written`).
"""

from __future__ import annotations

from typing import Any, ClassVar

from textual import work
from textual.binding import Binding, BindingType
from textual.screen import Screen
from textual.widgets import Button, Input

from bogle.tui.errors import HANDLED, message_for
from bogle.tui.screens.modals import ConfirmModal
from bogle.tui.widgets.form import Field

Entry = dict[str, Any]
"""The validated values, already shaped as the service call's keyword arguments."""


class WriteScreen[T](Screen[None]):
    """A form whose successful submit produces a ``T`` (a row that was written)."""

    CONFIRM_TITLE: ClassVar[str] = "Confirmar"
    CONFIRM_LABEL: ClassVar[str] = "Confirmar"
    WRITING_MESSAGE: ClassVar[str] = "gravando; um instante."
    """Shown when the user tries to leave while the write is still in flight."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "back", "Voltar"),
        Binding("ctrl+s", "submit", "Gravar"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.writing = False
        """True between the confirmation and the answer from the database."""

    # --- a implementar por cada formulario -------------------------------

    def collect(self) -> Entry | None:
        """Validated values, or ``None`` when the form is not ready."""
        raise NotImplementedError

    def describe(self, entry: Entry) -> str:
        """What the confirmation modal shows."""
        raise NotImplementedError

    def write(self, entry: Entry) -> T:
        """Persist. Runs in a worker thread."""
        raise NotImplementedError

    def written(self, result: T, /) -> None:
        """What happens after a successful write. Runs on the main thread.

        Positional-only so each form can name the thing it wrote (a transaction,
        an asset) instead of inheriting a name that means nothing.
        """
        raise NotImplementedError

    # --- validacao ------------------------------------------------------

    def field(self, field_id: str) -> Field:
        return self.query_one(f"#{field_id}", Field)

    def check_fields(self) -> bool:
        """Validate every field, focusing the first one that fails."""
        failed = [field for field in self.query(Field) if field.check() is not None]
        if failed:
            failed[0].input.focus()
            return False
        return True

    # --- fluxo ----------------------------------------------------------

    def action_submit(self) -> None:
        if self.writing:
            # Sem isso, um segundo ctrl+s (ou Enter) durante a escrita abre outro
            # modal e grava o mesmo lancamento duas vezes: o worker exclusivo
            # cancela o primeiro, mas uma thread ja em voo termina o que comecou.
            self.notify(self.WRITING_MESSAGE, severity="warning")
            return
        entry = self.collect()
        if entry is None:
            return
        self.app.push_screen(
            ConfirmModal(self.CONFIRM_TITLE, self.describe(entry), confirm_label=self.CONFIRM_LABEL),
            lambda confirmed: self._on_confirmed(entry, confirmed),
        )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Enter em qualquer campo tenta gravar; o modal ainda pede confirmacao.
        event.stop()
        self.action_submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "submit":
            self.action_submit()
        elif event.button.id == "back":
            self.action_back()

    def action_back(self) -> None:
        # Sair no meio da gravacao deixaria a escrita feita sem confirmacao
        # nenhuma na tela (o worker morre com a tela), e o usuario poderia
        # gravar de novo achando que falhou.
        if self.writing:
            self.notify(self.WRITING_MESSAGE, severity="warning")
            return
        self.dismiss()

    def _on_confirmed(self, entry: Entry, confirmed: bool | None) -> None:
        if confirmed:
            self.writing = True
            self._persist(entry)

    @work(thread=True, exclusive=True, group="write")
    def _persist(self, entry: Entry) -> None:
        try:
            result = self.write(entry)
        except HANDLED as exc:
            # Erro de validacao ou de banco mantem o usuario no formulario, com
            # os valores preenchidos, para corrigir — o ganho sobre a CLI.
            self.app.call_from_thread(self._failed, message_for(exc))
            return
        self.app.call_from_thread(self._succeeded, result)

    def _failed(self, message: str) -> None:
        self.writing = False
        self.notify(message, title="erro", severity="error", timeout=10, markup=False)

    def _succeeded(self, result: T) -> None:
        self.writing = False
        self.written(result)
