from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import override

import psycopg
from psycopg import errors as pg_errors
from psycopg.rows import DictRow, dict_row
from yoyo import read_migrations
from yoyo.backends.core.postgresql import PostgresqlPsycopgBackend
from yoyo.connections import parse_uri

DEFAULT_DATABASE_URL = "postgresql://localhost/bogle"
DEFAULT_TIMEZONE = "America/Sao_Paulo"


def get_database_url() -> str:
    """Return the PostgreSQL connection URL.

    Respects the ``BOGLE_DATABASE_URL`` environment variable; falls back to
    ``postgresql://localhost/bogle``.
    """
    return os.environ.get("BOGLE_DATABASE_URL", DEFAULT_DATABASE_URL)


def get_connection(database_url: str | None = None) -> psycopg.Connection[DictRow]:
    """Open a connection to PostgreSQL and configure the session.

    The session timezone is set to ``America/Sao_Paulo`` and rows are returned
    as ``dict``-like mappings.

    **Autocommit**, and that is load-bearing. Every caller here opens a
    connection, does one operation and closes it, declaring atomicity with
    ``conn.transaction()`` where it needs it. Without autocommit, a *read* before
    the write (``repo.get(ticker)`` before ``repo.update_weight(...)``, the
    portfolio summary before stamping ``last_rebalance_date``) already opened an
    implicit transaction — and psycopg then downgrades ``conn.transaction()`` to a
    SAVEPOINT, which commits nothing on its own. Closing the connection rolled the
    whole thing back, so the write was silently lost while the caller happily
    reported success with the row it had just read back.

    With autocommit, a ``conn.transaction()`` block is always a real transaction
    (committed on exit, rolled back on exception) and a bare statement commits by
    itself. The cost is that two writes are only atomic together when a caller
    wraps them in one ``conn.transaction()`` — which is now a visible decision
    instead of an accident.
    """
    if database_url is None:
        database_url = get_database_url()

    conn = psycopg.Connection[DictRow].connect(database_url, row_factory=dict_row, autocommit=True)
    with conn.cursor() as cur:
        cur.execute(f"SET TIME ZONE '{DEFAULT_TIMEZONE}'")
    return conn


def _migrations_path() -> Path:
    return Path(__file__).parent / "migrations"


def _yoyo_url(database_url: str) -> str:
    # yoyo-migrations needs the explicit psycopg3 driver scheme.
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url


class _MigrationsSchemaBackend(PostgresqlPsycopgBackend):
    """yoyo backend whose bookkeeping tables live in a dedicated
    ``migrations`` schema, isolating them from the application tables in
    ``public``."""

    log_table = "migrations.yoyo_log"
    version_table = "migrations.yoyo_version"
    lock_table = "migrations.yoyo_lock"

    @override
    def quote_identifier(self, s: str) -> str:
        # PostgreSQL requires each part of a schema-qualified identifier
        # to be quoted independently (`"schema"."table"`), not as a single
        # identifier (`"schema.table"`) which the default implementation
        # would produce.
        if "." in s:
            return ".".join(f'"{p}"' for p in s.split("."))
        return f'"{s}"'

    @override
    def list_tables(self, **kwargs) -> list[str]:
        # Return schema-qualified names so the internal-schema bookkeeping
        # logic in yoyo recognises tables we placed in the `migrations`
        # schema. The default impl filters by ``current_schema`` only,
        # which would always miss them.
        cursor = self.execute(
            "SELECT table_schema || '.' || table_name "
            "FROM information_schema.tables "
            "WHERE table_schema IN ('public', 'migrations')"
        )
        return [row[0] for row in cursor.fetchall()]


def run_migrations(database_url: str | None = None) -> None:
    """Apply any pending migrations from ``src/bogle/migrations/``.

    yoyo bookkeeping tables (``yoyo_migration``, ``yoyo_log``,
    ``yoyo_version``, ``yoyo_lock``) are created in a dedicated
    ``migrations`` schema. The schema is created on the fly if missing.

    Idempotent: yoyo records applied migrations and skips them on
    subsequent runs.
    """
    if database_url is None:
        database_url = get_database_url()

    with psycopg.connect(database_url) as setup_conn:
        with setup_conn.cursor() as cur:
            cur.execute("CREATE SCHEMA IF NOT EXISTS migrations")
        setup_conn.commit()

    parsed = parse_uri(_yoyo_url(database_url))
    backend = _MigrationsSchemaBackend(
        parsed,
        "migrations.yoyo_migration",
    )
    backend.init_database()
    migrations = read_migrations(str(_migrations_path()))
    with backend.lock():
        backend.apply_migrations(backend.to_apply(migrations))


def pending_migrations(database_url: str | None = None) -> list[str]:
    """Ids of the migrations on disk that the database has not applied, in order.

    The check every start-up pays, so it is one query against yoyo's bookkeeping
    (about 2 ms on a local server; the full yoyo run is five times that, and it
    creates schemas and takes locks even when there is nothing to do). Ids are
    what yoyo itself records — its "hash" is a digest of the id — so the two never
    disagree about what is pending. A database that never got the schema has no
    bookkeeping table either: everything is pending.
    """
    if database_url is None:
        database_url = get_database_url()
    available = [migration.id for migration in read_migrations(str(_migrations_path()))]
    with psycopg.connect(database_url, autocommit=True) as conn:
        try:
            rows = conn.execute("SELECT migration_id FROM migrations.yoyo_migration").fetchall()
        except pg_errors.UndefinedTable:
            return available  # sem schema ainda: a tabela do yoyo tampouco existe
    applied = {row[0] for row in rows}
    return [migration_id for migration_id in available if migration_id not in applied]


def migrate_if_pending(database_url: str | None = None) -> list[str]:
    """Apply the pending migrations, if any, and return their ids.

    What both frontends call before touching the database, so the schema follows
    the code the moment a new version runs — instead of a stale ``CHECK`` turning
    up as a database error on the one command that needed the change (the sale
    that empties a position, before 006). An empty result means the check found
    nothing to do, and the check was the only cost paid.
    """
    pending = pending_migrations(database_url)
    if pending:
        run_migrations(database_url)
    return pending


def migrated_notice(applied: Sequence[str]) -> str:
    """What both frontends tell the user when the schema changed under them.

    Announced because it is a change to the user's database made on the app's own
    initiative — small and automatic, and still not something to do in silence.
    """
    return f"banco de dados atualizado: {', '.join(applied)}."
