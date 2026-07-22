"""End-to-end tests for ``bogle status`` and the cycle warning (issue #24)."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BOGLE_BIN = PROJECT_ROOT / ".venv" / "bin" / "bogle"


@pytest.fixture(autouse=True)
def _truncate_for_cli(conn: psycopg.Connection) -> Iterator[None]:
    """Requesting `conn` truncates bogle_test before the subprocess runs."""
    yield


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


class TestStatus:
    def test_without_evaluation_recorded(self) -> None:
        result = run_cli("status")
        assert result.returncode == 0
        assert "Ciclo de avaliacao: 12 meses" in result.stdout
        assert "Nenhuma avaliacao registrada" in result.stdout

    def test_upcoming_evaluation(self) -> None:
        from datetime import date

        run_cli("config", "set", "last_rebalance_date", date.today().isoformat())
        run_cli("config", "set", "rebalance_period_months", "6")
        result = run_cli("status")
        assert result.returncode == 0
        assert "Ciclo de avaliacao: 6 meses" in result.stdout
        assert "Proxima avaliacao em" in result.stdout

    def test_overdue_evaluation(self) -> None:
        run_cli("config", "set", "last_rebalance_date", "2020-01-01")
        result = run_cli("status")
        assert result.returncode == 0
        assert "Avaliacao vencida ha" in result.stdout
        assert "bogle suggest" in result.stdout


class TestCycleWarning:
    def test_any_command_warns_when_overdue(self) -> None:
        run_cli("config", "set", "last_rebalance_date", "2020-01-01")
        result = run_cli("list")
        assert result.returncode == 0
        assert "aviso: ciclo de rebalanceamento" in result.stderr
        assert "2021-01-01" in result.stderr  # 2020-01-01 + 12 meses

    def test_no_warning_within_cycle(self) -> None:
        from datetime import date

        run_cli("config", "set", "last_rebalance_date", date.today().isoformat())
        result = run_cli("list")
        assert result.returncode == 0
        assert "aviso" not in result.stderr

    def test_no_warning_without_evaluation(self) -> None:
        result = run_cli("list")
        assert result.returncode == 0
        assert "aviso" not in result.stderr

    def test_status_reports_instead_of_warning(self) -> None:
        run_cli("config", "set", "last_rebalance_date", "2020-01-01")
        result = run_cli("status")
        assert result.returncode == 0
        assert "aviso" not in result.stderr

    def test_database_down_stays_silent_and_friendly(self) -> None:
        result = run_cli("list", database_url="postgresql://localhost:1/bogle_test")
        assert result.returncode == 1
        assert "nao foi possivel conectar" in result.stderr
        assert "Traceback" not in result.stderr
