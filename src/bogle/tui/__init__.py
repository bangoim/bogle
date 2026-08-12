"""Interactive full-screen interface (epic 13, issue #73).

A second frontend over the same domain layers the CLI uses (``domain/``,
``repositories/``, ``reports/``, ``position.py``) — it does *not* go through
``cli/``. ``bogle`` with no arguments lands here; ``bogle <comando>`` is
untouched.

Everything that blocks (psycopg, price APIs) runs in Textual worker threads
through :mod:`bogle.tui.services`, so the interface never freezes.
"""

from __future__ import annotations

__all__ = ["run_tui"]


def run_tui() -> None:
    """Open the interface. Entry point called by the Typer callback."""
    # Import tardio: manter o custo do textual fora dos comandos diretos.
    from bogle.tui.app import BogleApp

    BogleApp().run()
