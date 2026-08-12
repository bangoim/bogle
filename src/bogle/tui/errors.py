"""Turning exceptions into messages the user can act on (issue #73).

The CLI's ``_run`` shim maps two failures to friendly one-liners and lets real
bugs surface as a traceback. The TUI needs the same mapping, only routed to a
toast (or an inline message) instead of stderr — the whole point of the
interface is that a mistake keeps you on the screen, ready to fix it.

Database errors are handled as a family, not just ``OperationalError``: a schema
that never got its migrations raises ``UndefinedTable``, which is a foreseeable
state and not a bug worth tearing the interface down for (a worker's unhandled
exception exits the app with a traceback over the alt-screen).
"""

from __future__ import annotations

import psycopg
from psycopg import errors as pg_errors

from bogle.domain.errors import BogleError

DATABASE_HINT = (
    "nao foi possivel conectar ao banco de dados. Verifique BOGLE_DATABASE_URL e se o PostgreSQL esta rodando."
)
MIGRATIONS_HINT = (
    "o banco existe mas nao tem o schema do bogle. Aplique as migracoes "
    "(python -c 'from bogle.db import run_migrations; run_migrations()')."
)

HANDLED = (BogleError, psycopg.Error)
"""Exceptions a worker is expected to hit; anything else is a bug and propagates."""


def message_for(exc: BaseException) -> str:
    """A short, actionable message for an expected failure."""
    if isinstance(exc, psycopg.OperationalError):
        return DATABASE_HINT
    if isinstance(exc, pg_errors.UndefinedTable | pg_errors.UndefinedColumn):
        return MIGRATIONS_HINT
    if isinstance(exc, psycopg.Error):
        return f"erro no banco de dados: {exc}"
    return str(exc)
