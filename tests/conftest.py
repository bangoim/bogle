from __future__ import annotations

import os
from collections.abc import Iterator

import psycopg
import pytest
from psycopg.rows import DictRow

from bogle import format as fmt
from bogle.db import get_connection, run_migrations
from bogle.repositories.assets import AssetRepository
from bogle.repositories.holdings import HoldingRepository
from bogle.repositories.transactions import TransactionRepository

TEST_DATABASE_URL = "postgresql://localhost/bogle_test"

assert "_test" in TEST_DATABASE_URL, (
    "Recusando rodar testes contra um DB sem '_test' no nome — risco de truncar produção."
)


@pytest.fixture(scope="session", autouse=True)
def _setup_test_db() -> None:
    os.environ["BOGLE_DATABASE_URL"] = TEST_DATABASE_URL
    run_migrations(TEST_DATABASE_URL)


@pytest.fixture(autouse=True)
def _canonical_number_format() -> Iterator[None]:
    """Reset the process-wide display format (bogle.format.configure) per test."""
    fmt.configure(fmt.CANONICAL_DECIMAL)
    yield
    fmt.configure(fmt.CANONICAL_DECIMAL)


@pytest.fixture
def conn() -> Iterator[psycopg.Connection[DictRow]]:
    c = get_connection(TEST_DATABASE_URL)
    with c.cursor() as cur:
        cur.execute("TRUNCATE transactions, assets, user_settings CASCADE")
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
