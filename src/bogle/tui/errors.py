"""Turning exceptions into messages the user can act on (issue #73).

The CLI's ``_run`` shim maps two failures to friendly one-liners and lets real
bugs surface as a traceback. The TUI needs the same mapping, only routed to a
toast (or an inline message) instead of stderr — the whole point of the
interface is that a mistake keeps you on the screen, ready to fix it.
"""

from __future__ import annotations

import psycopg

from bogle.domain.errors import BogleError

DATABASE_HINT = (
    "nao foi possivel conectar ao banco de dados. Verifique BOGLE_DATABASE_URL e se o PostgreSQL esta rodando."
)

HANDLED = (BogleError, psycopg.OperationalError)
"""Exceptions a worker is expected to hit; anything else is a bug and propagates."""


def message_for(exc: BaseException) -> str:
    """A short, actionable message for an expected failure."""
    if isinstance(exc, psycopg.OperationalError):
        return DATABASE_HINT
    return str(exc)
