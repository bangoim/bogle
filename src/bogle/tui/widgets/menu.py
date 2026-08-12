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

_LABEL_WIDTH = 12


@dataclass(frozen=True, slots=True)
class MenuItem:
    key: str
    """Digit shown next to the item and bound as its shortcut."""
    id: str
    """Identifies the action; matches the option's DOM id."""
    label: str
    description: str


def _prompt(item: MenuItem) -> Text:
    return Text.assemble(
        (item.key, "bold cyan"),
        "  ",
        (item.label.ljust(_LABEL_WIDTH), "bold"),
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
        super().__init__(*(Option(_prompt(item), id=item.id) for item in items), id=id)
