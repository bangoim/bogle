"""Tests for ``bogle backup`` and ``bogle restore`` (issues #32, #33).

Most of these run the real PostgreSQL client programs against databases created
for the test (``bogle_test_bk_*``) and dropped after it: what is worth pinning is
exactly what ``pg_dump``, ``pg_restore`` and ``psql`` do with what this code
hands them — a spec that only mocked them is how the April version of #33 would
have lost a ledger with exit code 0. Every URL goes through :func:`_guarded`, so
nothing here can reach a database without ``_test`` in its name.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import errors as pg_errors
from psycopg import sql
from typer.testing import CliRunner

from bogle import backup as backup_mod
from bogle import cli as cli_mod
from bogle import db
from bogle.backup import (
    TOOLS_ENV,
    BackupResult,
    DumpFormat,
    create_backup,
    default_backup_dir,
    describe_rows,
    find_tool,
    plan_restore,
    restore,
    target_of,
)
from bogle.cli import app
from bogle.cli import backup as backup_cli
from bogle.db import get_connection, pending_migrations, run_migrations
from bogle.domain.errors import BackupError
from bogle.repositories.assets import AssetRepository
from bogle.repositories.transactions import TransactionRepository
from bogle.settings import DECIMAL_SEPARATOR, set_value
from tests.conftest import TEST_DATABASE_URL

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BOGLE_BIN = PROJECT_ROOT / ".venv" / "bin" / "bogle"
WHEN = datetime(2026, 3, 10, 12, tzinfo=UTC)


def _guarded(url: str) -> str:
    assert "_test" in url, f"recusando tocar um banco sem '_test' no nome: {url}"
    return url


# ----------------------------------------------------------------- fixtures


@pytest.fixture(autouse=True)
def _data_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Backups (and safety backups) land in the test's own directory."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv(TOOLS_ENV, raising=False)


@pytest.fixture
def backups(tmp_path: Path) -> Path:
    return tmp_path / "data" / "bogle" / "backups"


@pytest.fixture
def scratch() -> Iterator[Callable[[], str]]:
    """A factory of empty databases, all dropped at the end of the test."""
    admin = psycopg.connect(TEST_DATABASE_URL, autocommit=True)
    created: list[str] = []

    def make() -> str:
        name = f"bogle_test_bk_{uuid4().hex[:10]}"
        try:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        except pg_errors.InsufficientPrivilege:
            pytest.skip("precisa de CREATEDB para criar bancos descartáveis")
        created.append(name)
        return _guarded(TEST_DATABASE_URL.rsplit("/", 1)[0] + "/" + name)

    yield make
    for name in created:
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
    admin.close()


def seed(url: str, *, ticker: str = "PETR4") -> str:
    """A small but complete bogle database: every table has rows."""
    run_migrations(_guarded(url))
    conn = get_connection(url)
    try:
        AssetRepository(conn).add(ticker, Decimal("0.5"))
        transactions = TransactionRepository(conn)
        transactions.add_buy(ticker, WHEN, shares=Decimal("10"), unit_price=Decimal("30"), fees=Decimal("1.5"))
        transactions.add_sale(ticker, WHEN.replace(month=4), shares=Decimal("4"), unit_price=Decimal("35"))
        transactions.add_dividend(ticker, WHEN.replace(month=5), Decimal("12.34"))
        set_value(conn, DECIMAL_SEPARATOR, ",")
        conn.execute(
            "INSERT INTO price_history (symbol, date, close, source, loaded_on) VALUES "
            "(%s, '2026-03-10', 30.10, 'yfinance', '2026-03-11'), (%s, '2026-03-11', 30.55, 'brapi', '2026-03-11')",
            (ticker, ticker),
        )
    finally:
        conn.close()
    return url


def counts(url: str) -> dict[str, int]:
    conn = get_connection(_guarded(url))
    try:
        return backup_mod.table_counts(conn)
    finally:
        conn.close()


def ledger(url: str) -> list[tuple[object, ...]]:
    conn = get_connection(_guarded(url))
    try:
        rows = conn.execute(
            "SELECT ticker, transaction_type, transaction_date, shares, unit_price, total_cost, tax_withheld "
            "FROM transactions ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return [tuple(row.values()) for row in rows]


def tickers(url: str) -> list[str]:
    conn = get_connection(_guarded(url))
    try:
        return [asset.ticker for asset in AssetRepository(conn).list()]
    finally:
        conn.close()


def has_table(url: str, qualified: str) -> bool:
    conn = get_connection(_guarded(url))
    try:
        return backup_mod._exists(conn, qualified)
    finally:
        conn.close()


def applied_migrations(url: str) -> list[str]:
    conn = get_connection(_guarded(url))
    try:
        rows = conn.execute("SELECT migration_id FROM migrations.yoyo_migration ORDER BY migration_id").fetchall()
    finally:
        conn.close()
    return [row["migration_id"] for row in rows]


def migrate_until(url: str, last: str, tmp_path: Path) -> None:
    """Apply the migrations up to ``last`` only: a database of an older bogle."""
    partial = tmp_path / f"migrations-until-{last}"
    partial.mkdir()
    for source in sorted(db._migrations_path().glob("*.sql")):
        if source.stem <= last:
            shutil.copy(source, partial / source.name)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(db, "_migrations_path", lambda: partial)
        run_migrations(_guarded(url))


def server_major() -> int:
    conn = get_connection(TEST_DATABASE_URL)
    try:
        return conn.info.server_version // 10000
    finally:
        conn.close()


def fake_tool(directory: Path, name: str, major: int, body: str = "exit 0") -> Path:
    """A shell script that answers ``--version`` like the real program."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(
        f'#!/bin/sh\nif [ "$1" = "--version" ]; then echo "{name} (PostgreSQL) {major}.1"; exit 0; fi\n{body}\n'
    )
    path.chmod(0o755)
    return path


# -------------------------------------------------------------- sem banco


class TestTarget:
    def test_the_password_leaves_the_connection_string_and_rides_in_the_environment(self) -> None:
        target = target_of("postgresql://ana:s3cr3t@db.local:5433/carteira?sslmode=disable")
        assert "s3cr3t" not in target.conninfo
        for part in ("host=db.local", "port=5433", "dbname=carteira", "user=ana", "sslmode=disable"):
            assert part in target.conninfo
        assert target.env()["PGPASSWORD"] == "s3cr3t"

    def test_no_password_adds_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PGPASSWORD", raising=False)
        target = target_of("postgresql://localhost/bogle")
        assert target.password is None
        assert "PGPASSWORD" not in target.env()


class TestDefaults:
    def test_backups_go_under_xdg_data_home(self, tmp_path: Path) -> None:
        assert default_backup_dir() == tmp_path / "data" / "bogle" / "backups"

    def test_without_xdg_they_go_under_local_share(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv("XDG_DATA_HOME")
        monkeypatch.setenv("HOME", str(tmp_path))
        assert default_backup_dir() == tmp_path / ".local" / "share" / "bogle" / "backups"

    def test_each_format_has_its_extension(self) -> None:
        assert DumpFormat.CUSTOM.extension == "dump"
        assert DumpFormat.PLAIN.extension == "sql"

    def test_rows_agree_in_number(self) -> None:
        rows = {"assets": 1, "transactions": 20, "user_settings": 0, "price_history": 773}
        assert describe_rows(rows) == "1 ativo, 20 transações, 0 configurações, 773 fechamentos"

    def test_rows_skip_the_tables_an_older_database_lacks(self) -> None:
        assert describe_rows({"assets": 2, "transactions": 1}) == "2 ativos, 1 transação"


class TestFindTool:
    def test_bogle_pg_bin_wins(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        tool = fake_tool(tmp_path / "bin", "pg_dump", 18)
        monkeypatch.setenv(TOOLS_ENV, str(tmp_path / "bin"))
        assert find_tool("pg_dump", server_major=18) == tool

    def test_bogle_pg_bin_without_the_program_is_an_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "empty").mkdir()
        monkeypatch.setenv(TOOLS_ENV, str(tmp_path / "empty"))
        with pytest.raises(BackupError, match=f"pg_dump não está em {TOOLS_ENV}"):
            find_tool("pg_dump", server_major=18)

    def test_an_explicit_program_older_than_the_server_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_tool(tmp_path / "bin", "pg_dump", 16)
        monkeypatch.setenv(TOOLS_ENV, str(tmp_path / "bin"))
        with pytest.raises(BackupError, match=r"pg_dump 16 .* é mais antigo que o servidor"):
            find_tool("pg_dump", server_major=18)

    def test_the_path_comes_before_the_known_directories(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        on_path = fake_tool(tmp_path / "path", "psql", 18)
        fake_tool(tmp_path / "known", "psql", 19)
        monkeypatch.setenv("PATH", str(tmp_path / "path"))
        monkeypatch.setattr(backup_mod, "KNOWN_DIRS", (str(tmp_path / "known"),))
        assert find_tool("psql", server_major=18) == on_path

    def test_installed_outside_the_path_is_found_where_it_lives(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # O caso do keg-only do Homebrew: instalado, so que fora do PATH.
        fake_tool(tmp_path / "path", "pg_dump", 14)
        newest = fake_tool(tmp_path / "opt" / "postgresql@18" / "bin", "pg_dump", 18)
        fake_tool(tmp_path / "opt" / "postgresql@17" / "bin", "pg_dump", 17)
        monkeypatch.setenv("PATH", str(tmp_path / "path"))
        monkeypatch.setattr(backup_mod, "KNOWN_DIRS", (str(tmp_path / "opt" / "postgresql@*" / "bin"),))
        assert find_tool("pg_dump", server_major=17) == newest

    def test_only_older_programs_name_the_newest_one(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_tool(tmp_path / "path", "pg_dump", 14)
        fake_tool(tmp_path / "known", "pg_dump", 16)
        monkeypatch.setenv("PATH", str(tmp_path / "path"))
        monkeypatch.setattr(backup_mod, "KNOWN_DIRS", (str(tmp_path / "known"),))
        with pytest.raises(
            BackupError, match=r"pg_dump 16 \(.*known/pg_dump\) é mais antigo que o servidor \(PostgreSQL 18\)"
        ):
            find_tool("pg_dump", server_major=18)

    def test_nothing_anywhere_says_how_to_install_it(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
        monkeypatch.setattr(backup_mod, "KNOWN_DIRS", ())
        with pytest.raises(BackupError) as caught:
            find_tool("pg_dump", server_major=18)
        message = str(caught.value)
        assert "pg_dump não encontrado" in message
        assert "brew install postgresql@18" in message
        assert "apt install postgresql-client-18" in message
        assert TOOLS_ENV in message


# ------------------------------------------------------------------ backup


class TestBackup:
    def test_the_default_goes_to_the_data_directory_readable_only_by_the_owner(
        self, scratch: Callable[[], str], backups: Path
    ) -> None:
        url = seed(scratch())
        result = create_backup(database_url=url)
        assert result.path.parent == backups
        assert re.fullmatch(r"bogle-bogle_test_bk_\w+-\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}\.dump", result.path.name)
        assert result.path.stat().st_mode & 0o777 == 0o600
        assert result.format is DumpFormat.CUSTOM
        assert result.size == result.path.stat().st_size > 0
        assert result.rows == {"assets": 1, "transactions": 3, "user_settings": 1, "price_history": 2}
        assert result.rows == counts(url)
        assert list(backups.iterdir()) == [result.path]  # nenhum .tmp sobrando

    def test_the_custom_archive_lists_both_schemas(self, scratch: Callable[[], str]) -> None:
        result = create_backup(database_url=seed(scratch()))
        listing = subprocess.run(["pg_restore", "--list", str(result.path)], capture_output=True, text=True, check=True)
        for table in ("public assets", "public transactions", "public user_settings", "public price_history"):
            assert f"TABLE {table}" in listing.stdout
        assert "TABLE migrations yoyo_migration" in listing.stdout
        assert "SEQUENCE SET public transactions_id_seq" in listing.stdout

    def test_plain_is_sql_that_drops_before_creating_and_owns_nothing(self, scratch: Callable[[], str]) -> None:
        result = create_backup(database_url=seed(scratch()), dump_format=DumpFormat.PLAIN)
        text = result.path.read_text()
        assert result.path.suffix == ".sql"
        first_command = next(line for line in text.splitlines() if line and not line.startswith("--"))
        assert first_command.startswith("\\restrict ")
        for table in ("public.assets", "public.transactions", "public.price_history", "migrations.yoyo_migration"):
            assert f"DROP TABLE IF EXISTS {table};" in text
        assert "OWNER TO" not in text
        assert "GRANT " not in text
        assert "PostgreSQL database dump complete" in text

    def test_an_output_directory_gets_a_fresh_name_and_an_output_file_is_used_as_is(
        self, scratch: Callable[[], str], tmp_path: Path
    ) -> None:
        url = seed(scratch())
        into_dir = create_backup(database_url=url, output=f"{tmp_path / 'novo'}{os.sep}")
        assert into_dir.path.parent == tmp_path / "novo"
        assert into_dir.path.name.startswith("bogle-")
        as_file = create_backup(database_url=url, output=tmp_path / "x" / "minha.dump")
        assert as_file.path == tmp_path / "x" / "minha.dump"

    def test_a_database_without_the_schema_has_nothing_to_copy(self, scratch: Callable[[], str], backups: Path) -> None:
        with pytest.raises(BackupError, match="não tem o schema do bogle"):
            create_backup(database_url=scratch())
        assert not backups.exists() or not list(backups.iterdir())

    def test_it_copies_the_database_as_it_is_without_migrating_it(
        self, scratch: Callable[[], str], tmp_path: Path
    ) -> None:
        url = scratch()
        migrate_until(url, "006_allow_zero_target_weight", tmp_path)
        result = create_backup(database_url=url)
        assert "007_price_history" not in applied_migrations(url)
        assert "price_history" not in result.rows  # a 006 nao tinha a tabela

    def test_a_dump_that_dies_halfway_leaves_no_file(
        self, scratch: Callable[[], str], tmp_path: Path, backups: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        url = seed(scratch())
        major = server_major()
        fake_tool(
            tmp_path / "bin",
            "pg_dump",
            major,
            body='echo "parcial"; echo "pg_dump: error: conexão perdida" >&2; exit 1',
        )
        fake_tool(tmp_path / "bin", "pg_restore", major)
        monkeypatch.setenv(TOOLS_ENV, str(tmp_path / "bin"))
        with pytest.raises(BackupError, match="pg_dump falhou: conexão perdida"):
            create_backup(database_url=url)
        assert list(backups.iterdir()) == []

    def test_a_dump_that_does_not_check_out_leaves_no_file(
        self, scratch: Callable[[], str], tmp_path: Path, backups: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # O pg_dump diz que terminou, mas o arquivo nao e um arquivo custom.
        url = seed(scratch())
        major = server_major()
        fake_tool(tmp_path / "bin", "pg_dump", major, body='echo "lixo"')
        real = shutil.which("pg_restore")
        assert real is not None
        (tmp_path / "bin" / "pg_restore").symlink_to(real)
        monkeypatch.setenv(TOOLS_ENV, str(tmp_path / "bin"))
        with pytest.raises(BackupError, match="não passou na verificação"):
            create_backup(database_url=url)
        assert list(backups.iterdir()) == []


# ----------------------------------------------------------------- restore


class TestRestore:
    def test_a_fresh_database_gets_everything_back(self, scratch: Callable[[], str]) -> None:
        source = seed(scratch())
        dump = create_backup(database_url=source)
        target = scratch()
        plan = plan_restore(dump.path, database_url=target)
        assert not plan.has_schema
        assert plan.rows == {}
        assert plan.dump.format is DumpFormat.CUSTOM
        assert plan.dump.created_at is not None
        result = restore(plan)
        assert result.safety is None  # nada a salvar num banco recem-criado
        assert result.applied == []
        assert result.rows == dump.rows == counts(target)
        assert ledger(target) == ledger(source)
        assert pending_migrations(target) == []

    def test_plain_round_trips_too(self, scratch: Callable[[], str]) -> None:
        source = seed(scratch())
        dump = create_backup(database_url=source, dump_format=DumpFormat.PLAIN)
        target = scratch()
        restore(plan_restore(dump.path, database_url=target))
        assert ledger(target) == ledger(source)
        assert counts(target) == dump.rows

    def test_it_replaces_and_never_merges_and_keeps_a_safety_backup(
        self, scratch: Callable[[], str], backups: Path
    ) -> None:
        source = seed(scratch(), ticker="PETR4")
        dump = create_backup(database_url=source)
        target = seed(scratch(), ticker="VALE3")
        plan = plan_restore(dump.path, database_url=target)
        assert plan.has_schema
        assert plan.rows == counts(target)
        result = restore(plan)
        assert tickers(target) == ["PETR4"]
        assert ledger(target) == ledger(source)
        # O backup de seguranca e o banco de antes, inteiro.
        assert result.safety is not None
        assert result.safety.path.parent == backups
        assert "-pre-restore-" in result.safety.path.name
        check = scratch()
        restore(plan_restore(result.safety.path, database_url=check), safety_backup=False)
        assert tickers(check) == ["VALE3"]

    def test_tables_the_dump_does_not_have_go_too(self, scratch: Callable[[], str], tmp_path: Path) -> None:
        # O --clean do pg_dump so derruba o que esta no dump: uma tabela de uma
        # migracao mais nova ficaria para tras e colidiria com a migracao.
        source = scratch()
        migrate_until(source, "006_allow_zero_target_weight", tmp_path)
        dump = create_backup(database_url=source)
        target = seed(scratch())
        conn = get_connection(target)
        conn.execute("CREATE TABLE public.glide_path_points (age smallint)")
        conn.close()
        result = restore(plan_restore(dump.path, database_url=target), safety_backup=False)
        assert not has_table(target, "public.glide_path_points")
        assert result.applied == ["007_price_history"]  # e a 007 aplica sem colidir
        assert pending_migrations(target) == []

    def test_an_older_dump_is_migrated_forward(self, scratch: Callable[[], str], tmp_path: Path) -> None:
        source = scratch()
        migrate_until(source, "006_allow_zero_target_weight", tmp_path)
        conn = get_connection(source)
        AssetRepository(conn).add("PETR4", Decimal("0.5"))
        conn.close()
        dump = create_backup(database_url=source)
        target = scratch()
        result = restore(plan_restore(dump.path, database_url=target))
        assert result.applied == ["007_price_history"]
        assert tickers(target) == ["PETR4"]
        assert result.rows["price_history"] == 0

    def test_a_dump_of_a_newer_bogle_is_refused_and_nothing_changes(self, scratch: Callable[[], str]) -> None:
        source = seed(scratch())
        conn = get_connection(source)
        conn.execute(
            "INSERT INTO migrations.yoyo_migration (migration_hash, migration_id, applied_at_utc) "
            "VALUES (repeat('f', 64), '999_future', now())"
        )
        conn.close()
        dump = create_backup(database_url=source)
        target = seed(scratch(), ticker="VALE3")
        before = ledger(target)
        with pytest.raises(BackupError, match=r"versão mais nova do bogle \(migração 999_future\).*Nada foi alterado"):
            restore(plan_restore(dump.path, database_url=target), safety_backup=False)
        assert ledger(target) == before
        assert "999_future" not in applied_migrations(target)

    def test_a_dump_of_another_database_is_refused(self, scratch: Callable[[], str], tmp_path: Path) -> None:
        other = scratch()
        conn = get_connection(other)
        conn.execute("CREATE TABLE notas (texto text)")
        conn.close()
        foreign = tmp_path / "outro.sql"
        subprocess.run(["pg_dump", f"--dbname={other}", f"--file={foreign}"], check=True)
        target = seed(scratch())
        before = ledger(target)
        with pytest.raises(BackupError, match="não é um backup do bogle: faltam as tabelas do bogle"):
            restore(plan_restore(foreign, database_url=target), safety_backup=False)
        assert ledger(target) == before

    def test_a_statement_failing_halfway_rolls_everything_back(
        self, scratch: Callable[[], str], tmp_path: Path
    ) -> None:
        dump = create_backup(database_url=seed(scratch()), dump_format=DumpFormat.PLAIN)
        broken = tmp_path / "quebrado.sql"
        broken.write_text(
            dump.path.read_text().replace(
                "-- PostgreSQL database dump complete", "SELECT 1/0;\n-- PostgreSQL database dump complete"
            )
        )
        target = seed(scratch(), ticker="VALE3")
        before = ledger(target)
        with pytest.raises(BackupError, match="o restore falhou e nada foi alterado: division by zero"):
            restore(plan_restore(broken, database_url=target), safety_backup=False)
        assert ledger(target) == before
        assert tickers(target) == ["VALE3"]

    def test_a_truncated_archive_is_refused_before_touching_anything(
        self, scratch: Callable[[], str], tmp_path: Path
    ) -> None:
        dump = create_backup(database_url=seed(scratch()))
        truncated = tmp_path / "cortado.dump"
        data = dump.path.read_bytes()
        truncated.write_bytes(data[: len(data) // 2])
        target = seed(scratch(), ticker="VALE3")
        before = ledger(target)
        with pytest.raises(BackupError, match="Nada foi alterado"):
            restore(plan_restore(truncated, database_url=target), safety_backup=False)
        assert ledger(target) == before

    def test_a_truncated_sql_dump_is_refused_by_its_missing_last_line(
        self, scratch: Callable[[], str], tmp_path: Path
    ) -> None:
        dump = create_backup(database_url=seed(scratch()), dump_format=DumpFormat.PLAIN)
        truncated = tmp_path / "cortado.sql"
        text = dump.path.read_text()
        truncated.write_text(text[: len(text) // 2])
        with pytest.raises(BackupError, match="está incompleto"):
            plan_restore(truncated, database_url=scratch())

    def test_a_file_that_is_not_a_dump_is_refused(self, scratch: Callable[[], str], tmp_path: Path) -> None:
        notes = tmp_path / "notas.dump"
        notes.write_text("isto não é um backup\n")
        with pytest.raises(BackupError, match="não é um dump do PostgreSQL"):
            plan_restore(notes, database_url=scratch())

    def test_a_missing_file_is_named(self, scratch: Callable[[], str], tmp_path: Path) -> None:
        with pytest.raises(BackupError, match="arquivo não encontrado"):
            plan_restore(tmp_path / "nao-existe.dump", database_url=scratch())

    @pytest.mark.parametrize(
        ("dump_format", "renamed"), [(DumpFormat.CUSTOM, "backup.sql"), (DumpFormat.PLAIN, "backup.dump")]
    )
    def test_the_format_comes_from_the_file_not_from_its_name(
        self, scratch: Callable[[], str], tmp_path: Path, dump_format: DumpFormat, renamed: str
    ) -> None:
        source = seed(scratch())
        dump = create_backup(database_url=source, dump_format=dump_format)
        moved = tmp_path / renamed
        dump.path.rename(moved)
        target = scratch()
        plan = plan_restore(moved, database_url=target)
        assert plan.dump.format is dump_format
        restore(plan)
        assert ledger(target) == ledger(source)

    def test_no_safety_backup_when_asked(self, scratch: Callable[[], str], backups: Path) -> None:
        dump = create_backup(database_url=seed(scratch()))
        target = seed(scratch(), ticker="VALE3")
        result = restore(plan_restore(dump.path, database_url=target), safety_backup=False)
        assert result.safety is None
        assert [path.name for path in backups.iterdir()] == [dump.path.name]

    def test_another_session_holding_the_tables_makes_it_give_up_and_change_nothing(
        self, scratch: Callable[[], str]
    ) -> None:
        dump = create_backup(database_url=seed(scratch()))
        target = seed(scratch(), ticker="VALE3")
        before = ledger(target)
        holder = psycopg.connect(target)  # sem autocommit: a transacao segura o lock
        try:
            holder.execute("LOCK TABLE assets IN ACCESS SHARE MODE")
            with pytest.raises(BackupError, match="outra sessão está usando o banco"):
                restore(plan_restore(dump.path, database_url=target), safety_backup=False)
        finally:
            holder.rollback()
            holder.close()
        assert ledger(target) == before


# --------------------------------------------------------------------- CLI


def run_cli(
    *args: str, url: str, data_home: Path, stdin: str | None = None, **env: str
) -> subprocess.CompletedProcess[str]:
    environment = {key: value for key, value in os.environ.items() if key != TOOLS_ENV}
    environment.update(BOGLE_DATABASE_URL=_guarded(url), XDG_DATA_HOME=str(data_home), **env)
    return subprocess.run(
        [str(BOGLE_BIN), *args],
        capture_output=True,
        text=True,
        env=environment,
        cwd=str(PROJECT_ROOT),
        input=stdin if stdin is not None else "",
        check=False,
    )


class TestCli:
    def test_backup_says_where_and_what(self, scratch: Callable[[], str], tmp_path: Path, backups: Path) -> None:
        result = run_cli("backup", url=seed(scratch()), data_home=tmp_path / "data")
        assert result.returncode == 0, result.stderr
        [written] = list(backups.iterdir())
        assert f"backup gravado em {written}" in result.stdout
        # O banco semeado grava decimal_separator = ",": o tamanho sai com virgula.
        assert re.search(r"custom, \d+,\d kB: 1 ativo, 3 transações, 1 configuração, 2 fechamentos\.", result.stdout)

    def test_backup_json_is_for_scripts(self, scratch: Callable[[], str], tmp_path: Path) -> None:
        result = run_cli("backup", "--format", "plain", "--json", url=seed(scratch()), data_home=tmp_path / "data")
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["format"] == "plain"
        assert Path(payload["path"]).suffix == ".sql"
        assert payload["bytes"] == Path(payload["path"]).stat().st_size
        assert payload["rows"] == {"assets": 1, "transactions": 3, "user_settings": 1, "price_history": 2}

    def test_backup_does_not_migrate_first(self, scratch: Callable[[], str], tmp_path: Path) -> None:
        url = scratch()
        migrate_until(url, "006_allow_zero_target_weight", tmp_path)
        result = run_cli("backup", url=url, data_home=tmp_path / "data")
        assert result.returncode == 0, result.stderr
        assert "banco de dados atualizado" not in result.stderr
        assert "007_price_history" not in applied_migrations(url)

    def test_restore_asks_and_a_no_changes_nothing(self, scratch: Callable[[], str], tmp_path: Path) -> None:
        dump = create_backup(database_url=seed(scratch()))
        target = seed(scratch(), ticker="VALE3")
        result = run_cli("restore", str(dump.path), url=target, data_home=tmp_path / "data", stdin="n\n")
        assert result.returncode == 1
        assert "substituindo 1 ativo, 3 transações, 1 configuração e 2 fechamentos." in result.stdout
        assert "nada foi alterado." in result.stderr
        assert tickers(target) == ["VALE3"]

    def test_restore_with_a_yes_replaces_and_reports(self, scratch: Callable[[], str], tmp_path: Path) -> None:
        source = seed(scratch())
        dump = create_backup(database_url=source)
        target = seed(scratch(), ticker="VALE3")
        result = run_cli("restore", str(dump.path), url=target, data_home=tmp_path / "data", stdin="s\n")
        assert result.returncode == 0, result.stderr
        assert "backup de segurança: " in result.stdout
        assert "banco restaurado: 1 ativo, 3 transações, 1 configuração, 2 fechamentos." in result.stdout
        assert tickers(target) == ["PETR4"]

    def test_restore_with_stdin_closed_is_a_no(self, scratch: Callable[[], str], tmp_path: Path) -> None:
        dump = create_backup(database_url=seed(scratch()))
        target = seed(scratch(), ticker="VALE3")
        result = run_cli("restore", str(dump.path), url=target, data_home=tmp_path / "data")
        assert result.returncode == 1
        assert tickers(target) == ["VALE3"]

    def test_force_skips_the_question(self, scratch: Callable[[], str], tmp_path: Path) -> None:
        dump = create_backup(database_url=seed(scratch()))
        target = scratch()
        result = run_cli("restore", str(dump.path), "--force", url=target, data_home=tmp_path / "data")
        assert result.returncode == 0, result.stderr
        assert "Continuar?" not in result.stdout
        assert tickers(target) == ["PETR4"]

    def test_restore_of_an_older_dump_announces_the_migration(self, scratch: Callable[[], str], tmp_path: Path) -> None:
        source = scratch()
        migrate_until(source, "006_allow_zero_target_weight", tmp_path)
        dump = create_backup(database_url=source)
        result = run_cli("restore", str(dump.path), "--force", url=scratch(), data_home=tmp_path / "data")
        assert result.returncode == 0, result.stderr
        assert result.stderr.count("banco de dados atualizado") == 1
        assert "aviso: banco de dados atualizado: 007_price_history." in result.stderr

    def test_the_password_is_never_printed(self, scratch: Callable[[], str], tmp_path: Path) -> None:
        url = seed(scratch())
        with psycopg.connect(url) as probe:
            user = probe.info.user
        # O pg_hba local e `trust`: a senha e ignorada, e por isso pode ser falsa.
        with_password = url.replace("postgresql://", f"postgresql://{user}:S3CRET-pw@")
        backup = run_cli("backup", url=with_password, data_home=tmp_path / "data")
        assert backup.returncode == 0, backup.stderr
        [written] = list((tmp_path / "data" / "bogle" / "backups").iterdir())
        restored = run_cli("restore", str(written), "--force", url=with_password, data_home=tmp_path / "data")
        assert restored.returncode == 0, restored.stderr
        for result in (backup, restored):
            assert "S3CRET" not in result.stdout + result.stderr

    def test_a_missing_client_is_one_line_without_a_traceback(self, scratch: Callable[[], str], tmp_path: Path) -> None:
        (tmp_path / "vazio").mkdir()
        result = run_cli(
            "backup", url=seed(scratch()), data_home=tmp_path / "data", **{TOOLS_ENV: str(tmp_path / "vazio")}
        )
        assert result.returncode == 1
        assert result.stderr.startswith("erro: pg_dump não está em BOGLE_PG_BIN")
        assert "Traceback" not in result.stderr


class TestNoMigrationBeforeTheseCommands:
    """The callback migrates before every command — except these two."""

    @pytest.mark.parametrize("command", [["backup"], ["restore", "x.dump", "--force"]])
    def test_the_callback_leaves_the_schema_alone(self, command: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
        def never(*_args: object, **_kwargs: object) -> list[str]:
            raise AssertionError("não deveria migrar antes de backup/restore")

        monkeypatch.setattr(cli_mod, "migrate_if_pending", never)
        monkeypatch.setattr(cli_mod, "_read_preferences", lambda: (".", "ciclo vencido"))
        monkeypatch.setattr(backup_cli, "create_backup", lambda **_: _fake_result())
        monkeypatch.setattr(backup_cli, "plan_restore", lambda path: object())
        monkeypatch.setattr(
            backup_cli,
            "restore",
            lambda plan, **_: backup_mod.RestoreResult(rows={"assets": 1}, applied=[], safety=None),
        )
        result = CliRunner().invoke(app, command)
        assert result.exit_code == 0, result.output
        assert "ciclo vencido" not in result.output  # o lembrete do ciclo tambem fica de fora


def _fake_result() -> BackupResult:
    return BackupResult(
        path=Path("/tmp/x.dump"), format=DumpFormat.CUSTOM, size=1, rows={"assets": 1}, database="bogle em localhost"
    )
