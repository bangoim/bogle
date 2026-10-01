"""Navigation helpers (issue #74): walking the screen stack, and walking the focus.

Lives apart from :mod:`bogle.tui.app` so a screen can navigate without importing
the App that mounts it (which would close an import cycle).
"""

from __future__ import annotations

from typing import Any

from textual.app import App
from textual.binding import Binding, BindingType

ARROW_FOCUS: list[BindingType] = [
    Binding("left", "app.focus_previous", "Anterior", show=False),
    Binding("up", "app.focus_previous", "Anterior", show=False),
    Binding("right", "app.focus_next", "Proximo", show=False),
    Binding("down", "app.focus_next", "Proximo", show=False),
]
"""Arrows walking the controls of a dialog or a form, on top of ``tab``.

``tab`` is the only way out of the box, and it is the kind of thing that has to
be guessed — in front of two buttons the hand reaches for a direction. Bound on
the *screen*, which is what keeps it from stealing anything: a key reaches the
focused widget first, so an ``Input`` still owns left/right for its cursor and a
collapsed ``Select`` still owns up/down to open its list. Only what the focused
widget ignores bubbles up to here.
"""


def back_to_home(app: App[Any]) -> None:
    """Drop every screen above Home — the Home screen is always the bottom one."""
    while len(app.screen_stack) > 1:
        app.pop_screen()
