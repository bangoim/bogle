"""The numbered menu (issue #73).

Three ways into the same action, mole/htop style: the arrows plus Enter, or
typing the item's number. The digits are bound by the *screen* (a screen binding
fires no matter which widget has focus), so both paths converge on one handler.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from rich.text import Text
from textual.binding import Binding
from textual.widgets import OptionList
from textual.widgets.option_list import Option

_LABEL_GAP = 2
"""Spaces between the widest label and the descriptions column."""


@dataclass(frozen=True, slots=True)
class MenuItem:
    key: str
    """Digit shown next to the item and bound as its shortcut."""
    id: str
    """Identifies the action; matches the option's DOM id."""
    label: str
    description: str


def _prompt(item: MenuItem, width: int) -> Text:
    return Text.assemble(
        (item.key, "bold cyan"),
        "  ",
        (item.label.ljust(width), "bold"),
        (item.description, "dim"),
    )


def menu_bindings(items: Sequence[MenuItem]) -> list[Binding]:
    """``BINDINGS`` entries that make each item's number work on the whole screen.

    Hidden from the footer on purpose: the numbers are already spelled out in
    the menu itself, and the footer has better uses for its width.
    """
    return [Binding(item.key, f"open('{item.id}')", item.label, show=False) for item in items]


class Menu(OptionList):
    """Navigable list of :class:`MenuItem`."""

    def __init__(self, items: Sequence[MenuItem], *, id: str | None = None) -> None:
        # A coluna acompanha o rotulo mais largo do proprio menu: com largura
        # fixa, um rotulo maior que ela (por exemplo "Rentabilidade") encostava na
        # descricao e as duas viravam uma palavra so.
        width = max((len(item.label) for item in items), default=0) + _LABEL_GAP
        super().__init__(*(Option(_prompt(item, width), id=item.id) for item in items), id=id)
