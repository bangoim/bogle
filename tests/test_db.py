"""Tests for the connection itself (``bogle.db``).

The suite's other tests share one long-lived ``conn`` fixture that is never
closed mid-test, so none of them can see whether a write *survives* the close —
which is exactly where a whole class of silently lost writes lived. These open
their own connections and read back through a second one.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from psycopg import errors as pg_errors
from psycopg.rows import DictRow
from yoyo import read_migrations

from bogle import db
from bogle.db import get_connection, migrate_if_pending, pending_migrations
from bogle.domain.assets import AssetType
from bogle.domain.errors import WeightSumExceededError
from bogle.repositories.assets import AssetRepository
from bogle.settings import LAST_REBALANCE_DATE, REBALANCE_PERIOD_MONTHS, get_setting, set_value
from tests.conftest import TEST_DATABASE_URL


def weight_of(ticker: str) -> Decimal | None:
    """Read a weight through a connection of its own."""
    conn = get_connection(TEST_DATABASE_URL)
    try:
        asset = AssetRepository(conn).get(ticker)
        return asset.target_weight if asset is not None else None
    finally:
        conn.close()


class TestWritesSurviveTheClose:
    """A read before the write must not swallow it.

    Without autocommit, the read opened an implicit transaction, psycopg
    downgraded the write's ``conn.transaction()`` to a SAVEPOINT (which commits
    nothing), and ``close()`` rolled everything back — while the caller reported
    success, because it read the new value back inside the doomed transaction.
    """

    @pytest.fixture
    def seeded(self, conn: psycopg.Connection[DictRow]) -> None:
        # Dois ativos somando 0.8: subir PETR4 para 0.6 estoura a soma sem
        # estourar o CHECK da coluna, que e o unico jeito de exercitar o guard.
        repo = AssetRepository(conn)
        repo.add("PETR4", Decimal("0.3"))
        repo.add("VALE3", Decimal("0.5"))

    def test_a_write_after_a_read_on_the_same_connection_persists(self, seeded: None) -> None:
        writer = get_connection(TEST_DATABASE_URL)
        try:
            repo = AssetRepository(writer)
            assert repo.get("PETR4") is not None  # a leitura que abria a transacao
            repo.update_weight("PETR4", Decimal("0.2"))
        finally:
            writer.close()
        assert weight_of("PETR4") == Decimal("0.2")

    def test_a_setting_written_after_a_read_persists(self, seeded: None) -> None:
        # O mesmo caminho de `bogle suggest`, que le a carteira inteira antes de
        # gravar `last_rebalance_date` — e por isso nunca gravava.
        writer = get_connection(TEST_DATABASE_URL)
        try:
            AssetRepository(writer).list()
            set_value(writer, REBALANCE_PERIOD_MONTHS, 6)
        finally:
            writer.close()
        reader = get_connection(TEST_DATABASE_URL)
        try:
            assert get_setting(reader, REBALANCE_PERIOD_MONTHS) == 6
            assert get_setting(reader, LAST_REBALANCE_DATE) is None  # nao escrito, segue no default
        finally:
            reader.close()

    def test_a_failed_write_inside_a_transaction_still_rolls_back(self, seeded: None) -> None:
        # Autocommit nao pode custar a atomicidade declarada: o guard da soma dos
        # pesos roda dentro do bloco, e estourar ali tem de desfazer o UPDATE.
        writer = get_connection(TEST_DATABASE_URL)
        try:
            with pytest.raises(WeightSumExceededError):
                AssetRepository(writer).update_weight("PETR4", Decimal("0.6"))
        finally:
            writer.close()
        assert weight_of("PETR4") == Decimal("0.3")

    def test_two_writes_in_one_transaction_are_all_or_nothing(self, seeded: None) -> None:
        # O que `update_asset` (tipo + peso) declara: metade da alteracao aplicada
        # seria pior que nenhuma.
        writer = get_connection(TEST_DATABASE_URL)
        try:
            with pytest.raises(WeightSumExceededError), writer.transaction():
                repo = AssetRepository(writer)
                repo.update_type("PETR4", AssetType.ETF)
                repo.update_weight("PETR4", Decimal("0.6"))  # estoura a soma
        finally:
            writer.close()
        reader = get_connection(TEST_DATABASE_URL)
        try:
            asset = AssetRepository(reader).get("PETR4")
            assert asset is not None
            assert asset.asset_type is AssetType.STOCK  # o primeiro write voltou atras
            assert asset.target_weight == Decimal("0.3")
        finally:
            reader.close()


class TestPendingMigrations:
    """The start-up check: one query, and the apply only when it finds something."""

    def test_a_migrated_database_has_nothing_pending(self) -> None:
        # bogle_test recebeu tudo no conftest.
        assert pending_migrations(TEST_DATABASE_URL) == []
        assert migrate_if_pending(TEST_DATABASE_URL) == []

    def test_a_new_file_is_pending_until_applied(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, conn: psycopg.Connection[DictRow]
    ) -> None:
        # Um diretorio so com a migracao nova: o yoyo nao se importa com as
        # antigas que estao na tabela e nao no diretorio.
        (tmp_path / "999_probe.sql").write_text("SELECT 1;\n")
        monkeypatch.setattr(db, "_migrations_path", lambda: tmp_path)
        try:
            assert pending_migrations(TEST_DATABASE_URL) == ["999_probe"]
            assert migrate_if_pending(TEST_DATABASE_URL) == ["999_probe"]
            assert pending_migrations(TEST_DATABASE_URL) == []
            assert migrate_if_pending(TEST_DATABASE_URL) == []
        finally:
            conn.execute("DELETE FROM migrations.yoyo_migration WHERE migration_id = '999_probe'")
            conn.execute("DELETE FROM migrations.yoyo_log WHERE migration_id = '999_probe'")

    def test_a_database_without_the_schema_has_everything_pending(self, conn: psycopg.Connection[DictRow]) -> None:
        # O primeiro `bogle` em um banco recem-criado: nem a tabela do yoyo existe.
        fresh = "bogle_test_fresh"
        try:
            conn.execute(f"DROP DATABASE IF EXISTS {fresh} WITH (FORCE)")
            conn.execute(f"CREATE DATABASE {fresh}")
        except pg_errors.InsufficientPrivilege:
            pytest.skip("precisa de CREATEDB para simular um banco recem-criado")
        url = TEST_DATABASE_URL.rsplit("/", 1)[0] + "/" + fresh
        everything = [migration.id for migration in read_migrations(str(db._migrations_path()))]
        try:
            assert pending_migrations(url) == everything
            assert migrate_if_pending(url) == everything
            assert pending_migrations(url) == []
        finally:
            conn.execute(f"DROP DATABASE {fresh} WITH (FORCE)")
