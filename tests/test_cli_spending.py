"""Tests for ``bogle spending`` (issue #114): sync, status and the automatic sync before them.

Pluggy is always the fake of :mod:`tests.pluggy_fakes`, injected by swapping the
entry points the CLI module calls; the end-to-end runs go through the real
executable with the ``PLUGGY_*`` variables empty (see ``conftest``).
"""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import DictRow
from typer.testing import CliRunner, Result

from bogle import cli as cli_mod
from bogle.cli import app
from bogle.cli import spending as spending_cli
from bogle.domain.errors import NetworkError
from bogle.settings import SPENDING_LAST_SYNC_AT, set_value
from bogle.spending.models import AutoSync
from bogle.spending.sync import sync_if_due, sync_now
from tests.pluggy_fakes import ENV, ITEM, FakePluggy, account, item, no_client, tx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BOGLE_BIN = PROJECT_ROOT / ".venv" / "bin" / "bogle"

Conn = psycopg.Connection[DictRow]


@pytest.fixture
def pluggy(monkeypatch: pytest.MonkeyPatch) -> FakePluggy:
    """A fake Pluggy behind both entry points of the CLI, with the variables set."""
    fake = FakePluggy()
    today = date.today()
    fake.add_item(
        item(consent_expires_at=datetime.now(UTC) + timedelta(days=300)),
        account("acc-card", name="Cartão"),
        account("acc-bank", kind="BANK", number="12345-6", name="Conta"),
    )
    fake.set_transactions(
        "acc-card",
        tx("c1", "acc-card", date(2026, 3, 5), "50"),
        tx("c2", "acc-card", today - timedelta(days=1), "30"),
        tx("p1", "acc-card", today + timedelta(days=60), "100", status="PENDING"),
    )
    fake.set_transactions("acc-bank", tx("b1", "acc-bank", date(2026, 1, 10), "-20"))
    monkeypatch.setattr(
        spending_cli, "sync_now", lambda full=False: sync_now(full=full, env=ENV, client_factory=fake.factory())
    )
    monkeypatch.setattr(spending_cli, "sync_if_due", lambda: sync_if_due(env=ENV, client_factory=fake.factory()))
    return fake


def spending(*args: str) -> Result:
    return CliRunner().invoke(app, ["spending", *args])


class TestSync:
    def test_shows_each_account_with_its_freshness_and_coverage(self, conn: Conn, pluggy: FakePluggy) -> None:
        result = spending("sync")
        assert result.exit_code == 0, result.output
        out = result.stdout
        assert "Sync de " in out
        for header in ("Conta", "Novas", "Atualizadas", "Removidas", "Última transação", "Desde"):
            assert header in out
        assert "Cartão" in out
        assert (date.today() - timedelta(days=1)).isoformat() in out  # a ultima transacao, nao a parcela futura
        assert "2026-03-05" in out
        assert "2026-01-10" in out
        assert "Atenção" not in out

    def test_a_second_run_inserts_nothing(self, conn: Conn, pluggy: FakePluggy) -> None:
        spending("sync")
        result = spending("sync")
        assert result.exit_code == 0, result.output
        card = next(line for line in result.stdout.splitlines() if "Cartão" in line)
        assert card.split("│")[2].strip() == "0"

    def test_full_asks_for_everything(self, conn: Conn, pluggy: FakePluggy) -> None:
        spending("sync")
        pluggy.calls.clear()
        assert spending("sync", "--full").exit_code == 0
        assert {call[2:] for call in pluggy.requests("list_transactions")} == {(None, None)}

    def test_a_failure_is_listed_and_exits_one(self, conn: Conn, pluggy: FakePluggy) -> None:
        pluggy.fail["list_transactions:acc-card"] = NetworkError("pluggy", "sem rede")
        result = spending("sync")
        assert result.exit_code == 1
        assert "Atenção:" in result.stdout
        assert "Conta Cartão: Falha de rede ao acessar pluggy" in result.stdout

    def test_sync_does_not_run_the_automatic_sync_first(self, conn: Conn, pluggy: FakePluggy) -> None:
        result = spending("sync")
        assert result.exit_code == 0
        assert len(pluggy.requests("factory")) == 1


class TestStatus:
    def test_nothing_yet(self, conn: Conn, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(spending_cli, "sync_if_due", lambda: AutoSync(ran=False))
        result = spending("status")
        assert result.exit_code == 0, result.output
        assert result.stdout == f"{spending_cli.NO_ACCOUNTS}\n"

    def test_each_account_and_connection(self, conn: Conn, pluggy: FakePluggy) -> None:
        result = spending("status")  # o sync automatico roda antes: nunca houve sync
        assert result.exit_code == 0, result.output
        out = result.stdout
        for header in ("Último sync", "Última transação", "Desde", "Conexões", "UPDATED", "Consentimento até"):
            assert header in out
        assert "2026-03-05" in out
        assert len(pluggy.requests("list_transactions")) == 2

    def test_a_recent_sync_does_not_call_the_api(self, conn: Conn, pluggy: FakePluggy) -> None:
        spending("sync")
        pluggy.calls.clear()
        result = spending("status")
        assert result.exit_code == 0, result.output
        assert pluggy.calls == []
        assert result.stderr == ""

    def test_past_24_hours_it_syncs_first(self, conn: Conn, pluggy: FakePluggy) -> None:
        spending("sync")
        set_value(conn, SPENDING_LAST_SYNC_AT, datetime.now(UTC) - timedelta(hours=25))
        pluggy.calls.clear()
        assert spending("status").exit_code == 0
        assert len(pluggy.requests("list_transactions")) == 2

    def test_a_failing_automatic_sync_warns_and_shows_what_is_stored(self, conn: Conn, pluggy: FakePluggy) -> None:
        spending("sync")
        set_value(conn, SPENDING_LAST_SYNC_AT, datetime(2026, 11, 25, 11, tzinfo=UTC))
        pluggy.fail[f"get_item:{ITEM}"] = NetworkError("pluggy", "sem rede")
        result = spending("status")
        assert result.exit_code == 0, result.output
        assert result.stderr.startswith("aviso: gastos não sincronizados (conexão Cartão, Conta: Falha de rede")
        assert result.stderr.rstrip().endswith("dados de 25/11 08:00")
        assert "Cartão" in result.stdout

    def test_pending_duplicates_are_counted(self, conn: Conn, pluggy: FakePluggy) -> None:
        spending("sync")
        rows = conn.execute("SELECT id FROM spending.transactions ORDER BY id LIMIT 2").fetchall()
        conn.execute(
            "INSERT INTO spending.duplicate_suggestions (transaction_id, duplicate_of_id) VALUES (%s, %s)",
            (rows[1]["id"], rows[0]["id"]),
        )
        result = spending("status")
        assert "1 duplicata provável pendente" in result.stdout

    def test_help_does_not_sync(self, conn: Conn, monkeypatch: pytest.MonkeyPatch) -> None:
        def never() -> AutoSync:
            raise AssertionError("o --help nao sincroniza")

        monkeypatch.setattr(spending_cli, "sync_if_due", never)
        result = spending("status", "--help")
        assert result.exit_code == 0, result.output


class TestPortfolioNoticesStayOut:
    def test_the_portfolio_reminders_are_not_shown(self, conn: Conn, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            cli_mod, "_read_preferences", lambda: (".", "ciclo vencido", "rebalanceamento anual de 2061 pendente")
        )
        monkeypatch.setattr(spending_cli, "sync_if_due", lambda: AutoSync(ran=False))
        result = spending("status")
        assert result.exit_code == 0, result.output
        assert "ciclo vencido" not in result.output
        assert "rebalanceamento anual" not in result.output

    def test_other_commands_still_show_them(self, conn: Conn, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cli_mod, "_read_preferences", lambda: (".", "ciclo vencido", None))
        result = CliRunner().invoke(app, ["list"])
        assert "aviso: ciclo vencido" in result.stderr


class TestDefaultFactoryIsNeverReachedInTests:
    def test_the_variables_are_empty(self) -> None:
        for name in ("PLUGGY_CLIENT_ID", "PLUGGY_CLIENT_SECRET", "PLUGGY_ITEM_IDS"):
            assert os.environ.get(name) == ""

    def test_without_configuration_the_automatic_sync_never_builds_a_client(self, conn: Conn) -> None:
        auto = sync_if_due(client_factory=no_client)
        assert auto.notice is not None and "faltam PLUGGY_CLIENT_ID" in auto.notice


# ---- De ponta a ponta, pelo executavel, sem nenhuma variavel da Pluggy.


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(BOGLE_BIN), *args],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        cwd=str(PROJECT_ROOT),
        check=False,
    )


class TestWithoutConfiguration:
    def test_sync_says_what_is_missing_without_a_traceback(self, conn: Conn) -> None:
        result = run_cli("spending", "sync")
        assert result.returncode == 1
        assert result.stderr.startswith(
            "erro: faltam PLUGGY_CLIENT_ID, PLUGGY_CLIENT_SECRET, PLUGGY_ITEM_IDS no ambiente"
        )
        assert "Traceback" not in result.stderr

    def test_status_warns_and_goes_on(self, conn: Conn) -> None:
        result = run_cli("spending", "status")
        assert result.returncode == 0, result.stderr
        assert "aviso: gastos não sincronizados (faltam PLUGGY_CLIENT_ID" in result.stderr
        assert result.stdout == f"{spending_cli.NO_ACCOUNTS}\n"

    def test_the_rest_of_bogle_is_untouched(self, conn: Conn) -> None:
        listed = run_cli("list")
        assert listed.returncode == 0, listed.stderr
        assert "PLUGGY" not in listed.stderr
        checked = run_cli("check")
        assert checked.returncode == 0, checked.stderr
        assert checked.stdout == "Nenhuma pendência.\n"
