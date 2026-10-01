"""Interactive full-screen interface (epic 13, issue #73).

A second frontend over the same domain layers the CLI uses (``domain/``,
``repositories/``, ``reports/``, ``position.py``): no command, no rendering and
no business rule is reached through ``cli/``. The single exception is
``cli/parsing.py``, a leaf module with the format rules (no typer involved),
reused by the forms so both frontends accept and reject exactly the same input.

``bogle`` with no arguments lands here; ``bogle <comando>`` is untouched.

Everything that blocks (psycopg, price APIs) runs in Textual worker threads
through :mod:`bogle.tui.services`, so the interface never freezes.
"""

from __future__ import annotations

__all__ = ["run_tui"]


def run_tui() -> None:
    """Open the interface. Entry point called by the Typer callback."""
    # Import tardio: manter o custo do textual fora dos comandos diretos.
    from bogle import format as fmt
    from bogle.db import migrated_notice
    from bogle.tui import services
    from bogle.tui.app import BogleApp

    # Schema primeiro: as preferencias e todo worker que vem depois leem o que
    # ele deixou. O que foi aplicado vira toast assim que houver tela para isso.
    applied = services.update_schema()
    preferences = services.load_preferences()
    fmt.configure(preferences.decimal_separator)
    fmt.hide_amounts(preferences.hide_amounts)
    notices = [migrated_notice(applied)] if applied else []
    BogleApp(theme=preferences.theme, notices=notices).run()
