"""The indices to measure the portfolio against (issue #75).

Mirrors ``--index``/``--vs``: a comma-separated list, upper-cased, opening with
whatever ``default_compare_indices`` holds. Editing it in place is the point —
comparing against the CDI and then against the IBOV is a keystroke, not a new
command. An empty list is a valid answer: it means the portfolio on its own.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import override

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Input, Label


def parse_indices(raw: str) -> tuple[str, ...]:
    """``" cdi , ibov "`` -> ``("CDI", "IBOV")``; blank -> ``()``."""
    return tuple(part.strip().upper() for part in raw.split(",") if part.strip())


class IndicesInput(Horizontal):
    """One labeled input; ``Enter`` posts :class:`IndicesInput.Applied`."""

    class Applied(Message):
        """The user asked for these indices."""

        def __init__(self, indices: tuple[str, ...]) -> None:
            super().__init__()
            self.indices = indices

    @override
    def compose(self) -> ComposeResult:
        yield Label("Indices", classes="field-label")
        yield Input(placeholder="ex: IBOV,CDI (Enter aplica)", compact=True, classes="field-input")

    @property
    def input(self) -> Input:
        return self.query_one(Input)

    def show(self, indices: Sequence[str]) -> None:
        """Display the indices in use, without asking for a reload."""
        self.input.value = ",".join(indices)

    def focus_input(self) -> None:
        self.input.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Para no widget: o Enter aqui e "aplicar estes indices", nao o Enter da
        # tela (que nos formularios significa gravar).
        event.stop()
        self.post_message(self.Applied(parse_indices(event.value)))
