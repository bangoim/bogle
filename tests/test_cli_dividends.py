"""End-to-end tests for ``bogle dividends`` (issue #30) — network-free."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import psycopg
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BOGLE_BIN = PROJECT_ROOT / ".venv" / "bin" / "bogle"


@pytest.fixture(autouse=True)
def _truncate_for_cli(conn: psycopg.Connection) -> Iterator[None]:
    """Requesting `conn` truncates bogle_test before the subprocess runs."""
    yield


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["COLUMNS"] = "200"  # rich trunca a tabela na largura do terminal (80 sem tty)
    return subprocess.run(
        [str(BOGLE_BIN), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(PROJECT_ROOT),
        check=False,
    )


@pytest.fixture
def seeded_income() -> None:
    recent = (date.today() - timedelta(days=30)).isoformat()
    old = (date.today() - timedelta(days=500)).isoformat()
    assert run_cli("add", "ITUB4", "-w", "0.4").returncode == 0
    assert run_cli("add", "HGLG11", "-w", "0.3", "-t", "FII").returncode == 0
    assert (
        run_cli(
            "income", "ITUB4", "--type", "JCP", "--amount", "100", "--tax-withheld", "15", "--date", recent
        ).returncode
        == 0
    )
    assert run_cli("income", "HGLG11", "--type", "RENDIMENTO", "--amount", "890", "--date", recent).returncode == 0
    assert run_cli("income", "ITUB4", "--type", "DIVIDEND", "--amount", "50", "--date", old).returncode == 0


class TestByMonth:
    def test_default_12m_shows_net_jcp_and_total(self, seeded_income: None) -> None:
        result = run_cli("dividends")
        assert result.returncode == 0
        assert "ultimos 12 meses" in result.stdout
        assert "85.00" in result.stdout  # JCP liquido (100 - 15)
        assert "890.00" in result.stdout
        assert "975.00" in result.stdout  # TOTAL do mes e geral
        assert "50.00" not in result.stdout  # provento antigo fora da janela

    def test_period_all_includes_old_income(self, seeded_income: None) -> None:
        result = run_cli("dividends", "--period", "all")
        assert result.returncode == 0
        assert "desde o inicio" in result.stdout
        assert "50.00" in result.stdout

    def test_empty(self) -> None:
        result = run_cli("dividends")
        assert result.returncode == 0
        assert "Nenhum provento no periodo." in result.stdout


class TestByTicker:
    def test_groups_and_totals(self, seeded_income: None) -> None:
        result = run_cli("dividends", "--by", "ticker")
        assert result.returncode == 0
        assert "HGLG11" in result.stdout
        assert "RENDIMENTO" in result.stdout
        assert "JCP" in result.stdout
        assert "975.00" in result.stdout

    def test_invalid_period_is_friendly(self) -> None:
        result = run_cli("dividends", "--period", "3m")
        assert result.returncode == 1
        assert "--period invalido" in result.stderr
