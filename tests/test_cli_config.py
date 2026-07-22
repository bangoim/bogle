"""End-to-end tests for ``bogle config`` (issue #31), network-free via subprocess."""

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


class TestGetSet:
    def test_get_default(self) -> None:
        result = run_cli("config", "get", "rebalance_period_months")
        assert result.returncode == 0
        assert result.stdout.strip() == "12"

    def test_set_then_get(self) -> None:
        assert run_cli("config", "set", "rebalance_period_months", "6").returncode == 0
        result = run_cli("config", "get", "rebalance_period_months")
        assert result.stdout.strip() == "6"

    def test_set_rejects_invalid_period(self) -> None:
        result = run_cli("config", "set", "rebalance_period_months", "9")
        assert result.returncode == 1
        assert "6 ou 12" in result.stderr

    def test_unknown_key_is_friendly(self) -> None:
        result = run_cli("config", "get", "typo_key")
        assert result.returncode == 1
        assert "nao reconhecida" in result.stderr
        assert "rebalance_period_months" in result.stderr


class TestUnsetList:
    def test_unset_reverts(self) -> None:
        run_cli("config", "set", "weight_drift_threshold", "0.03")
        assert run_cli("config", "unset", "weight_drift_threshold").returncode == 0
        result = run_cli("config", "get", "weight_drift_threshold")
        assert result.stdout.strip() == "0.05"

    def test_list_shows_all_keys(self) -> None:
        run_cli("config", "set", "last_rebalance_date", "2026-07-01")
        result = run_cli("config", "list")
        assert result.returncode == 0
        for key in ("rebalance_period_months", "default_compare_indices", "weight_drift_threshold"):
            assert key in result.stdout
        assert "2026-07-01" in result.stdout
        assert "(default)" in result.stdout
