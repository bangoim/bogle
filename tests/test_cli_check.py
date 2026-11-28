"""Tests for ``bogle check`` (issue #108): the lines and the exit code of each
pendency, the notification (osascript mocked, never run) and the failures.

The settings go through the test database; the prices are an injected overview,
so nothing here touches the network.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Iterator
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.rows import DictRow
from typer.testing import CliRunner, Result

from bogle.alerts import CheckResult, listing, notification_text, stale_prices_alert
from bogle.cli import app
from bogle.reports.overview import PortfolioOverview
from bogle.settings import (
    BIRTH_YEAR,
    GLIDE_PATH_APPLIED_YEAR,
    LAST_REBALANCE_DATE,
    SPENDING_LAST_SYNC_AT,
    get_setting,
    set_value,
)
from bogle.spending.health import SpendingCheck, check_spending
from tests.pluggy_fakes import ENV, FakePluggy, account, item, tx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BOGLE_BIN = PROJECT_ROOT / ".venv" / "bin" / "bogle"
THIS_YEAR = date.today().year

CYCLE = "Ciclo de rebalanceamento de 12 meses vencido desde 2021-01-01. Rode 'bogle suggest' para avaliar a carteira"
ANNUAL = f"Rebalanceamento anual de {THIS_YEAR} pendente (bogle rebalance)"
STALE = "Preços desatualizados: MUND11 (2026-11-20), NB1011 (2026-11-19)"


def overview(stale: dict[str, date] | None = None) -> PortfolioOverview:
    return PortfolioOverview(
        as_of=date(2026, 11, 26),
        inception=date(2025, 1, 2),
        invested=Decimal("1000"),
        patrimony=Decimal("1100"),
        twr_12m=None,
        twr_total=None,
        twr_12m_start=None,
        excluded=[],
        stale_prices=stale or {},
    )


STALE_OVERVIEW = overview({"NB1011": date(2026, 11, 19), "MUND11": date(2026, 11, 20)})


@pytest.fixture
def clean(conn: psycopg.Connection[DictRow]) -> Iterator[psycopg.Connection[DictRow]]:
    yield conn
    conn.execute("TRUNCATE glide_path_points, user_settings")
    conn.commit()


@pytest.fixture
def prices(monkeypatch: pytest.MonkeyPatch) -> Callable[[PortfolioOverview | Exception], None]:
    """Replace the priced overview: an overview to return, or an exception to raise."""

    def use(outcome: PortfolioOverview | Exception) -> None:
        def load(today: date) -> PortfolioOverview:
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        monkeypatch.setattr("bogle.cli.check.load_overview", load)

    use(overview())
    return use


@pytest.fixture
def osascript(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Record every subprocess the command starts, instead of starting it."""
    calls: list[list[str]] = []

    def run(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("bogle.cli.check.subprocess.run", run)
    return calls


def overdue(conn: psycopg.Connection[DictRow]) -> None:
    set_value(conn, LAST_REBALANCE_DATE, date(2020, 1, 1))
    conn.commit()


def retired(conn: psycopg.Connection[DictRow]) -> None:
    # 1950 + 70 (o default): o rebalanceamento anual deste ano esta pendente.
    set_value(conn, BIRTH_YEAR, 1950)
    conn.commit()


def check(*args: str) -> Result:
    return CliRunner().invoke(app, ["check", *args])


class TestPureLines:
    def test_stale_prices_name_each_ticker_and_its_close(self) -> None:
        assert stale_prices_alert(STALE_OVERVIEW) == STALE

    def test_no_stale_prices_no_line(self) -> None:
        assert stale_prices_alert(overview()) is None

    def test_one_line_is_not_numbered(self) -> None:
        assert listing(["a"]) == ["a"]

    def test_several_lines_are_numbered(self) -> None:
        assert listing(["a", "b"]) == ["1. a", "2. b"]

    def test_exit_codes(self) -> None:
        assert CheckResult().exit_code == 0
        assert CheckResult(alerts=["a"]).exit_code == 1
        assert CheckResult(alerts=["a"], errors=["b"]).exit_code == 3
        assert CheckResult(errors=["b"]).exit_code == 3

    def test_notification_carries_the_failure_after_the_pendencies(self) -> None:
        text = notification_text(CheckResult(alerts=["a"], errors=["b"]))
        assert text == "1. a\n2. Erro: b"


class TestCheck:
    def test_nothing_pending(self, clean: psycopg.Connection[DictRow], prices: Any) -> None:
        result = check()
        assert result.exit_code == 0, result.output
        assert result.stdout == "Nenhuma pendência.\n"
        assert result.stderr == ""

    def test_overdue_cycle_alone(self, clean: psycopg.Connection[DictRow], prices: Any) -> None:
        overdue(clean)
        result = check()
        assert result.exit_code == 1, result.output
        assert result.stdout == f"{CYCLE}\n"
        # O aviso do callback nao sai de novo no stderr.
        assert result.stderr == ""

    def test_annual_rebalance_alone(self, clean: psycopg.Connection[DictRow], prices: Any) -> None:
        retired(clean)
        result = check()
        assert result.exit_code == 1, result.output
        assert result.stdout == f"{ANNUAL}\n"
        assert result.stderr == ""

    def test_stale_prices_alone(self, clean: psycopg.Connection[DictRow], prices: Any) -> None:
        prices(STALE_OVERVIEW)
        result = check()
        assert result.exit_code == 1, result.output
        assert result.stdout == f"{STALE}\n"

    def test_every_pendency_numbered(self, clean: psycopg.Connection[DictRow], prices: Any) -> None:
        overdue(clean)
        retired(clean)
        prices(STALE_OVERVIEW)
        result = check()
        assert result.exit_code == 1, result.output
        assert result.stdout.splitlines() == [f"1. {CYCLE}", f"2. {ANNUAL}", f"3. {STALE}"]

    def test_the_glide_path_is_left_for_the_next_run(
        self, clean: psycopg.Connection[DictRow], prices: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail() -> None:
            raise AssertionError("o check nao aplica a trajetoria")

        monkeypatch.setattr("bogle.cli._apply_glide_path", fail)
        assert check().exit_code == 0
        assert get_setting(clean, GLIDE_PATH_APPLIED_YEAR) is None


class TestNotify:
    def test_sends_the_text_as_an_argument(
        self, clean: psycopg.Connection[DictRow], prices: Any, osascript: list[list[str]]
    ) -> None:
        overdue(clean)
        retired(clean)
        result = check("--notify")
        assert result.exit_code == 1, result.output
        assert result.stdout.splitlines() == [f"1. {CYCLE}", f"2. {ANNUAL}"]
        [command] = osascript
        assert command[0] == "osascript"
        assert command[-2:] == ["--", f"1. {CYCLE}\n2. {ANNUAL}"]
        # O texto nao entra no script: so nos argumentos depois do `--`.
        script = [command[i + 1] for i, part in enumerate(command) if part == "-e"]
        assert "item 1 of argv" in "\n".join(script)
        assert not any("Rebalanceamento" in line for line in script)

    def test_nothing_pending_sends_nothing(
        self, clean: psycopg.Connection[DictRow], prices: Any, osascript: list[list[str]]
    ) -> None:
        result = check("--notify")
        assert result.exit_code == 0, result.output
        assert osascript == []

    def test_without_the_flag_sends_nothing(
        self, clean: psycopg.Connection[DictRow], prices: Any, osascript: list[list[str]]
    ) -> None:
        overdue(clean)
        assert check().exit_code == 1
        assert osascript == []

    def test_a_notification_that_fails_is_a_failure(
        self, clean: psycopg.Connection[DictRow], prices: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def missing(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
            raise FileNotFoundError("osascript")

        monkeypatch.setattr("bogle.cli.check.subprocess.run", missing)
        overdue(clean)
        result = check("--notify")
        assert result.exit_code == 3
        assert result.stdout == f"{CYCLE}\n"
        assert "erro: notificação não enviada (osascript)." in result.stderr


class TestFailures:
    def test_pricing_failure_keeps_the_other_pendencies(
        self, clean: psycopg.Connection[DictRow], prices: Any, osascript: list[list[str]]
    ) -> None:
        overdue(clean)
        retired(clean)
        prices(RuntimeError("brapi fora do ar"))
        result = check("--notify")
        assert result.exit_code == 3
        assert result.stdout.splitlines() == [f"1. {CYCLE}", f"2. {ANNUAL}"]
        assert "erro: preços não verificados (RuntimeError: brapi fora do ar)" in result.stderr
        [command] = osascript
        assert command[-1] == (
            f"1. {CYCLE}\n2. {ANNUAL}\n3. Erro: preços não verificados (RuntimeError: brapi fora do ar)"
        )

    def test_pricing_failure_alone_does_not_say_nothing_is_pending(
        self, clean: psycopg.Connection[DictRow], prices: Any
    ) -> None:
        prices(RuntimeError("brapi fora do ar"))
        result = check()
        assert result.exit_code == 3
        assert result.stdout == ""

    def test_database_down_is_a_failure_and_is_notified(
        self, prices: Any, osascript: list[list[str]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BOGLE_DATABASE_URL", "postgresql://localhost:1/bogle_test")
        result = check("--notify")
        assert result.exit_code == 3
        assert result.stdout == ""
        assert "erro: não foi possível conectar ao banco de dados" in result.stderr
        [command] = osascript
        assert command[-1].startswith("Erro: não foi possível conectar ao banco de dados")


# ---- De ponta a ponta, pelo executavel: os codigos de saida passam pelo `_run`.
# Carteira vazia, entao o resumo nao precisa de preco nem de rede.


def run_cli(*args: str, database_url: str | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if database_url is not None:
        env["BOGLE_DATABASE_URL"] = database_url
    return subprocess.run(
        [str(BOGLE_BIN), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(PROJECT_ROOT),
        check=False,
    )


# ---- Gastos (issue #114): o estagio das conexoes do Meu Pluggy, por ultimo.

CONSENT = "Consentimento de Cartão vence em 10 dias (2026-12-07): renove em meu.pluggy.ai"


@pytest.fixture
def spending(monkeypatch: pytest.MonkeyPatch) -> Callable[[SpendingCheck | Exception], None]:
    """Replace the spending stage: a result to return, or an exception to raise."""

    def use(outcome: SpendingCheck | Exception) -> None:
        def load(now: datetime) -> SpendingCheck:
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        monkeypatch.setattr("bogle.cli.check.load_spending_alerts", load)

    return use


class TestSpending:
    def test_without_spending_the_output_is_the_same(self, clean: psycopg.Connection[DictRow], prices: Any) -> None:
        result = check()
        assert result.exit_code == 0, result.output
        assert result.stdout == "Nenhuma pendência.\n"

    def test_connection_lines_come_after_the_prices(
        self, clean: psycopg.Connection[DictRow], prices: Any, spending: Any
    ) -> None:
        overdue(clean)
        prices(STALE_OVERVIEW)
        spending(SpendingCheck(alerts=[CONSENT]))
        result = check()
        assert result.exit_code == 1, result.output
        assert result.stdout.splitlines() == [f"1. {CYCLE}", f"2. {STALE}", f"3. {CONSENT}"]

    def test_a_failing_spending_stage_keeps_the_other_lines(
        self, clean: psycopg.Connection[DictRow], prices: Any, spending: Any, osascript: list[list[str]]
    ) -> None:
        overdue(clean)
        spending(RuntimeError("pluggy fora do ar"))
        result = check("--notify")
        assert result.exit_code == 3
        assert result.stdout == f"{CYCLE}\n"
        assert "erro: conexões de gastos não verificadas (RuntimeError: pluggy fora do ar)" in result.stderr
        [command] = osascript
        assert command[-1].endswith("Erro: conexões de gastos não verificadas (RuntimeError: pluggy fora do ar)")

    def test_a_sync_error_is_an_error_and_its_alerts_stay(
        self, clean: psycopg.Connection[DictRow], prices: Any, spending: Any
    ) -> None:
        spending(SpendingCheck(alerts=[CONSENT], errors=["gastos não sincronizados (sem rede)"]))
        result = check()
        assert result.exit_code == 3
        assert result.stdout == f"{CONSENT}\n"
        assert "erro: gastos não sincronizados (sem rede)" in result.stderr

    def test_both_prices_and_spending_failing(
        self, clean: psycopg.Connection[DictRow], prices: Any, spending: Any
    ) -> None:
        prices(RuntimeError("brapi fora do ar"))
        spending(RuntimeError("pluggy fora do ar"))
        result = check()
        assert result.exit_code == 3
        assert "preços não verificados" in result.stderr
        assert "conexões de gastos não verificadas" in result.stderr

    def test_the_check_runs_the_automatic_sync(
        self, clean: psycopg.Connection[DictRow], prices: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakePluggy()
        fake.add_item(item(), account("acc", name="Cartão"))
        fake.set_transactions("acc", tx("t1", "acc", date(2026, 11, 20)))

        def load(now: datetime) -> SpendingCheck:
            return check_spending(now, env=ENV, client_factory=fake.factory())

        monkeypatch.setattr("bogle.cli.check.load_spending_alerts", load)
        result = check()
        assert result.exit_code == 0, result.output
        assert fake.requests("list_transactions") == [("list_transactions", "acc", None, None)]
        assert get_setting(clean, SPENDING_LAST_SYNC_AT) is not None


class TestEndToEnd:
    def test_nothing_pending_exits_zero(self, clean: psycopg.Connection[DictRow]) -> None:
        result = run_cli("check")
        assert result.returncode == 0, result.stderr
        assert result.stdout == "Nenhuma pendência.\n"

    def test_a_pendency_exits_one_without_the_stderr_notice(self, clean: psycopg.Connection[DictRow]) -> None:
        overdue(clean)
        result = run_cli("check")
        assert result.returncode == 1, result.stderr
        assert result.stdout == f"{CYCLE}\n"
        assert "aviso" not in result.stderr

    def test_database_down_exits_three(self) -> None:
        result = run_cli("check", database_url="postgresql://localhost:1/bogle_test")
        assert result.returncode == 3
        assert "não foi possível conectar" in result.stderr
        assert "Traceback" not in result.stderr
