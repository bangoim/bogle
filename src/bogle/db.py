from __future__ import annotations

import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from yoyo import get_backend, read_migrations

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


def run_migrations(database_url: str | None = None) -> None:
    """Apply any pending migrations from ``src/bogle/migrations/``.

    Idempotent: yoyo records applied migrations in ``_yoyo_migration`` and
    skips them on subsequent runs.
    """
    if database_url is None:
        database_url = get_database_url()

    backend = get_backend(_yoyo_url(database_url))
    migrations = read_migrations(str(_migrations_path()))
    with backend.lock():
        backend.apply_migrations(backend.to_apply(migrations))
