"""End-to-end tests for ``bogle config`` (issue #31), network-free via subprocess."""

from __future__ import annotations

import json
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


class TestDecimalSeparator:
    """The setting reaches the rendering of every command (issues #73/#74)."""

    def test_default_is_the_canonical_dot(self) -> None:
        result = run_cli("config", "get", "decimal_separator")
        assert result.returncode == 0
        assert result.stdout.strip() == "."

    def test_only_dot_and_comma_are_accepted(self) -> None:
        result = run_cli("config", "set", "decimal_separator", ";")
        assert result.returncode == 1
        assert "deve ser '.' ou ','" in result.stderr

    def test_setting_it_switches_the_output_of_a_command(self) -> None:
        assert run_cli("add", "PETR4", "-w", "0.4").returncode == 0
        assert run_cli("buy", "PETR4", "-s", "100", "-p", "30.50", "--date", "2026-01-15").returncode == 0

        dot = run_cli("transactions")
        assert "3,050" in dot.stdout  # milhar com virgula, decimal com ponto

        assert run_cli("config", "set", "decimal_separator", ",").returncode == 0
        comma = run_cli("transactions")
        assert "3.050" in comma.stdout  # milhar com ponto

    def test_input_takes_either_separator_for_the_cents(self) -> None:
        # A entrada nao depende da configuracao: os dois separadores marcam
        # centavos, e milhar vai sem separador nenhum.
        assert run_cli("add", "PETR4", "-w", "0.4").returncode == 0
        assert run_cli("config", "set", "decimal_separator", ",").returncode == 0
        result = run_cli("buy", "PETR4", "-s", "1", "-p", "1234,50", "--date", "2026-01-15")
        assert result.returncode == 0
        assert "custo total: 1.234,5" in result.stdout  # exibicao agrupada

    def test_input_rejects_a_thousands_separator(self) -> None:
        assert run_cli("add", "PETR4", "-w", "0.4").returncode == 0
        result = run_cli("buy", "PETR4", "-s", "1", "-p", "1.234,50", "--date", "2026-01-15")
        assert result.returncode == 1
        assert "milhar vai sem separador" in result.stderr

    def test_json_output_stays_canonical(self) -> None:
        assert run_cli("add", "PETR4", "-w", "0.4").returncode == 0
        assert run_cli("buy", "PETR4", "-s", "100", "-p", "30.50", "--date", "2026-01-15").returncode == 0
        assert run_cli("config", "set", "decimal_separator", ",").returncode == 0
        result = run_cli("position", "--no-prices", "--json")
        assert result.returncode == 0
        totals = json.loads(result.stdout)["totals"]
        assert totals["invested"] == "3050"  # normalizado, sem milhar e sem virgula


class TestHideValues:
    """The privacy mode is the TUI's; the CLI stays scriptable."""

    def test_default_is_visible(self) -> None:
        result = run_cli("config", "get", "hide_values")
        assert result.returncode == 0
        assert result.stdout.strip() == "false"

    def test_only_booleans_are_accepted(self) -> None:
        result = run_cli("config", "set", "hide_values", "talvez")
        assert result.returncode == 1
        assert "nao e um booleano" in result.stderr

    def test_a_command_output_is_not_masked(self) -> None:
        # A configuracao vale para a interface interativa: mascarar a saida de
        # texto quebraria quem le `bogle position` num script.
        assert run_cli("add", "PETR4", "-w", "0.4").returncode == 0
        assert run_cli("buy", "PETR4", "-s", "100", "-p", "30.50", "--date", "2026-01-15").returncode == 0
        assert run_cli("config", "set", "hide_values", "true").returncode == 0
        result = run_cli("position", "--no-prices")
        assert result.returncode == 0
        assert "3,050.00" in result.stdout
        assert "\u2022" not in result.stdout
