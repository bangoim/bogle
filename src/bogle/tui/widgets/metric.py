"""A headline number (issue #73).

Caption above, value below. The value arrives as Rich markup (so the shared
:func:`bogle.format.signed` colors it exactly like the CLI does) and the plain
text is kept on the widget — it is what the tests read, and it does not depend
on Textual internals.
"""

from __future__ import annotations

from typing import override

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Label

PLACEHOLDER = "..."
"""Shown while the worker is still computing."""


class Metric(Vertical):
    def __init__(self, caption: str, *, id: str) -> None:
        super().__init__(id=id, classes="metric")
        self.caption = caption
        self.value = PLACEHOLDER

    @override
    def compose(self) -> ComposeResult:
        yield Label(self.caption, classes="metric-caption")
        yield Label(PLACEHOLDER, classes="metric-value")

    def set_caption(self, caption: str) -> None:
        """Relabel the metric (a partial patrimony must not read as the total)."""
        self.caption = caption
        self.query_one(".metric-caption", Label).update(caption)

    def show(self, markup: str) -> None:
        """Render ``markup`` as the value."""
        rendered = Text.from_markup(markup)
        self.value = rendered.plain
        self.query_one(".metric-value", Label).update(rendered)

    def reset(self) -> None:
        self.value = PLACEHOLDER
        self.query_one(".metric-value", Label).update(PLACEHOLDER)
