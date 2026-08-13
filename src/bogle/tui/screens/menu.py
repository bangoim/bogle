"""Screens whose body is a numbered menu (issues #73-#75).

Three of them — Home, Registrar and Relatorios — and each one used to carry its
own copy of the same two handlers (open by number, open by Enter on the list).
What differs between them is only the list of items and the screen each item
opens, so that pair is the class attribute :attr:`MenuScreen.ENTRIES` and the
handlers live here.

Item and destination stay in one list on purpose: there is no menu id without a
screen (nor the reverse) to fall out of sync.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar

from textual.screen import Screen

from bogle.tui.widgets.menu import Menu, MenuItem

Entries = tuple[tuple[MenuItem, Callable[[], Screen[None]]], ...]
"""Each menu item next to the factory of the screen it opens."""


def items_of(entries: Entries) -> tuple[MenuItem, ...]:
    """Just the items — what the widget renders and ``menu_bindings`` binds."""
    return tuple(item for item, _ in entries)


class MenuScreen(Screen[None]):
    """Opens one of :attr:`ENTRIES` by number, by Enter or by click."""

    ENTRIES: ClassVar[Entries] = ()
    MENU_TITLE: ClassVar[str] = "Menu"
    MENU_FRAME: ClassVar[str] = "#menu"
    """What carries the border and its title — the list itself on a screen with
    one column, the container around them when the menu is split in two."""

    def on_mount(self) -> None:
        self.query_one(self.MENU_FRAME).border_title = self.MENU_TITLE

    def action_open(self, item_id: str) -> None:
        screens = {item.id: factory for item, factory in self.ENTRIES}
        self.app.push_screen(screens[item_id]())

    def on_option_list_option_selected(self, event: Menu.OptionSelected) -> None:
        if event.option.id is not None:
            self.action_open(event.option.id)
