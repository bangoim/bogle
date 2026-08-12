"""Form fields (issue #74).

A field is a label, an input and its own error line. Validation runs as the user
types (Textual validates the ``Input`` and hands the result to the ``Changed``
message), so a bad value is called out next to the field that caused it instead
of after a round trip to the database — the whole reason the forms exist.
"""

from __future__ import annotations

from typing import override

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.validation import Validator
from textual.widget import Widget
from textual.widgets import Input, Label


class ControlRow(Horizontal):
    """A labeled row for a control that is not an ``Input`` (a Select, a Checkbox).

    Same label column as :class:`Field`, so a form mixing the two still lines up.
    """

    def __init__(self, label: str, control: Widget, *, id: str) -> None:
        super().__init__(id=id, classes="field-row")
        self.label = label
        self._control = control

    @override
    def compose(self) -> ComposeResult:
        yield Label(self.label, classes="field-label")
        yield self._control


class Field(Vertical):
    """One labeled input with inline validation."""

    def __init__(
        self,
        label: str,
        *,
        id: str,
        value: str = "",
        placeholder: str = "",
        validators: list[Validator] | None = None,
    ) -> None:
        super().__init__(id=id, classes="field")
        self.label = label
        self._initial = value
        self._placeholder = placeholder
        self._validators = validators or []
        self._error = ""

    @override
    def compose(self) -> ComposeResult:
        # Label e input na mesma linha (input `compact`, sem borda) para o
        # formulario inteiro caber em 24 linhas; a mensagem de erro fica embaixo,
        # sempre ocupando a linha, para o layout nao pular quando ela aparece.
        with Horizontal(classes="field-row"):
            yield Label(self.label, classes="field-label")
            yield Input(
                value=self._initial,
                placeholder=self._placeholder,
                validators=self._validators,
                validate_on=("changed", "blur"),
                compact=True,
                classes="field-input",
            )
        # markup=False: a mensagem cita o que o usuario digitou ("recebido
        # '[/i]'"), e o parser de markup estouraria justo no fluxo que o epico
        # existe para proteger.
        yield Label("", classes="field-error", markup=False)

    # --- valor ----------------------------------------------------------

    @property
    def input(self) -> Input:
        return self.query_one(Input)

    @property
    def value(self) -> str:
        return self.input.value.strip()

    def set_value(self, value: str) -> None:
        self.input.value = value

    def reset(self) -> None:
        """Back to the value the field opened with."""
        self.set_value(self._initial)
        self.clear_error()

    def set_enabled(self, enabled: bool, *, placeholder: str = "") -> None:
        """Enable or disable the input, clearing it on the way out.

        Used by the income form: the withheld-tax field does not apply to
        RENDIMENTO (exempt for individuals), so it is disabled and emptied.
        """
        self.input.disabled = not enabled
        self.input.placeholder = placeholder or self._placeholder
        if not enabled:
            self.set_value("")
            # O textual guarda a validade num reactive proprio, que pinta a borda
            # do input: sem revalidar (o validador ja foi relaxado antes de
            # desabilitar), a borda vermelha do tipo anterior fica na tela.
            self.input.validate("")
            self.clear_error()

    @property
    def enabled(self) -> bool:
        return not self.input.disabled

    def set_applicable(self, applicable: bool, *, placeholder: str = "") -> None:
        """Show or hide the field, per the field table of the type being registered.

        Hidden means "does not apply to this type": the input is disabled as well,
        so :meth:`check` skips it and its value is cleared — a field the type does
        not accept must not reach the service with something in it.
        """
        self.display = applicable
        self.set_enabled(applicable, placeholder=placeholder)

    # --- erros ----------------------------------------------------------

    @property
    def error(self) -> str:
        """The message currently shown under the field ("" when valid)."""
        return self._error

    def show_error(self, message: str) -> None:
        self._error = message
        self.query_one(".field-error", Label).update(message)
        self.input.add_class("-invalid")

    def clear_error(self) -> None:
        self._error = ""
        self.query_one(".field-error", Label).update("")
        self.input.remove_class("-invalid")

    def check(self) -> str | None:
        """Validate now, render the outcome and return the failure, if any."""
        if not self.enabled:
            self.clear_error()
            return None
        result = self.input.validate(self.value)
        if result is None or result.is_valid:
            self.clear_error()
            return None
        failure = result.failure_descriptions[0]
        self.show_error(failure)
        return failure

    def on_input_changed(self, event: Input.Changed) -> None:
        """Render the validation Textual already ran for this keystroke.

        A disabled field has no error to show: clearing its value (as the income
        form does when the type stops accepting withheld tax) still fires this
        event, and the old validators would light up an inapplicable field.
        """
        if self.input.disabled:
            self.clear_error()
            return
        result = event.validation_result
        if result is None or result.is_valid:
            self.clear_error()
            return
        self.show_error(result.failure_descriptions[0])
