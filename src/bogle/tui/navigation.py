"""Screen-stack helpers (issue #74).

Lives apart from :mod:`bogle.tui.app` so a screen can navigate without importing
the App that mounts it (which would close an import cycle).
"""

from __future__ import annotations

from typing import Any

from textual.app import App


def back_to_home(app: App[Any]) -> None:
    """Drop every screen above Home — the Home screen is always the bottom one."""
    while len(app.screen_stack) > 1:
        app.pop_screen()
