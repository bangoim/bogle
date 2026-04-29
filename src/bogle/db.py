from __future__ import annotations

import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
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


def get_connection(database_url: str | None = None) -> psycopg.Connection:
    """Open a connection to PostgreSQL and configure the session.

    The session timezone is set to ``America/Sao_Paulo`` and rows are returned
    as ``dict``-like mappings.
    """
    if database_url is None:
        database_url = get_database_url()

    conn = psycopg.connect(database_url, row_factory=dict_row)
    with conn.cursor() as cur:
        cur.execute(f"SET TIME ZONE '{DEFAULT_TIMEZONE}'")
    conn.commit()
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

    def quote_identifier(self, s: str) -> str:
        # PostgreSQL requires each part of a schema-qualified identifier
        # to be quoted independently (`"schema"."table"`), not as a single
        # identifier (`"schema.table"`) which the default implementation
        # would produce.
        if "." in s:
            return ".".join(f'"{p}"' for p in s.split("."))
        return f'"{s}"'

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
