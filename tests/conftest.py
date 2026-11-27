from __future__ import annotations

import os
from collections.abc import Iterator
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import DictRow

from bogle import format as fmt
from bogle.db import get_connection, run_migrations
from bogle.repositories.assets import AssetRepository
from bogle.repositories.holdings import HoldingRepository
from bogle.repositories.transactions import TransactionRepository

BASE_TEST_DATABASE_URL = os.environ.get("BOGLE_TEST_DATABASE_URL", "postgresql://localhost/bogle_test")

assert "_test" in BASE_TEST_DATABASE_URL, (
    "Recusando rodar testes contra um DB sem '_test' no nome — risco de truncar produção."
)


def database_name(url: str) -> str:
    """The database in a ``postgresql://`` URL (the path, without the slash)."""
    return urlsplit(url).path.lstrip("/")


def with_database(url: str, name: str) -> str:
    """The same URL (credentials, host, query) pointing at another database."""
    return urlunsplit(urlsplit(url)._replace(path="/" + name))


# Com pytest-xdist cada worker (gw0, gw1, ...) tem o seu banco, porque o `conn`
# trunca as tabelas a cada teste. Sem xdist a variavel nao existe e tudo roda no
# banco base, como sempre.
XDIST_WORKER = os.environ.get("PYTEST_XDIST_WORKER")

TEST_DATABASE_URL = (
    with_database(BASE_TEST_DATABASE_URL, f"{database_name(BASE_TEST_DATABASE_URL)}_{XDIST_WORKER}")
    if XDIST_WORKER
    else BASE_TEST_DATABASE_URL
)
TEST_DATABASE_NAME = database_name(TEST_DATABASE_URL)

assert "_test" in TEST_DATABASE_NAME, (
    f"Recusando rodar testes contra o banco {TEST_DATABASE_NAME!r}, sem '_test' no nome."
)


def _create_worker_database() -> None:
    """Create this worker's database, if missing, through the base one."""
    with psycopg.connect(BASE_TEST_DATABASE_URL, autocommit=True) as admin:
        exists = admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DATABASE_NAME,)).fetchone()
        if exists is None:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TEST_DATABASE_NAME)))


@pytest.fixture(scope="session", autouse=True)
def _setup_test_db() -> None:
    if XDIST_WORKER:
        _create_worker_database()
    os.environ["BOGLE_DATABASE_URL"] = TEST_DATABASE_URL
    run_migrations(TEST_DATABASE_URL)


@pytest.fixture(autouse=True)
def _canonical_number_format() -> Iterator[None]:
    """Reset the process-wide display preferences (format, privacy) per test."""
    fmt.configure(fmt.CANONICAL_DECIMAL)
    fmt.hide_amounts(False)
    yield
    fmt.configure(fmt.CANONICAL_DECIMAL)
    fmt.hide_amounts(False)


@pytest.fixture
def conn() -> Iterator[psycopg.Connection[DictRow]]:
    c = get_connection(TEST_DATABASE_URL)
    with c.cursor() as cur:
        cur.execute("TRUNCATE transactions, assets, user_settings, price_history CASCADE")
    c.commit()
    yield c
    c.close()


@pytest.fixture
def repo(conn: psycopg.Connection[DictRow]) -> AssetRepository:
    return AssetRepository(conn)


@pytest.fixture
def trepo(conn: psycopg.Connection[DictRow]) -> TransactionRepository:
    return TransactionRepository(conn)


@pytest.fixture
def hrepo(conn: psycopg.Connection[DictRow]) -> HoldingRepository:
    return HoldingRepository(conn)
