"""Tests for ``bogle rebalance`` (issue #81): JSON/table rendering (unit), the CLI
flow with an injected portfolio (no network, but the database for the settings),
and the yearly reminder end to end, as ``bogle status`` and any command show it.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import DictRow
from rich.console import Console
from typer.testing import CliRunner

from bogle.cli import app
from bogle.cli.rebalance import _rebalance_json, _render
from bogle.rebalancing import AnnualRebalance
from bogle.settings import BIRTH_YEAR, LAST_ANNUAL_REBALANCE_YEAR, LAST_REBALANCE_DATE, get_setting, set_value
from tests.test_annual_rebalance import D, drifted_portfolio, plan

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BOGLE_BIN = PROJECT_ROOT / ".venv" / "bin" / "bogle"
THIS_YEAR = date.today().year


def sample_rebalance() -> AnnualRebalance:
    """O plano de ``test_annual_rebalance``: MUND11 e NB1011 vendem, B5P211 compra."""
    return plan(drifted_portfolio(), {"NB1011"})


def render(rebalance: AnnualRebalance) -> str:
    buffer = io.StringIO()
    _render(rebalance, Console(file=buffer, width=200))
    return buffer.getvalue()


class TestJson:
    def test_carries_each_operation_and_the_totals(self) -> None:
        data = _rebalance_json(sample_rebalance())
        json.dumps(data)  # must not raise
        assert data["year"] == 2061
        assert data["threshold"] == "0.05"
        mund, nb, b5 = data["items"]
        assert (mund["ticker"], mund["operation"], mund["reason"]) == ("MUND11", "SELL", "DRIFT")
        assert (mund["quantity"], mund["value"], mund["income_tax"]) == ("102", "10200", "306")
        assert (nb["operation"], nb["reason"]) == ("SELL", "EXITING")
        assert (b5["operation"], b5["reason"], b5["income_tax"]) == ("BUY", None, None)
        assert b5["quantity"] == "147"
        assert data["totals"] == {
            "sold": "15250",
            "income_tax": "457.5",
            "available": "14792.5",
            "bought": "14700",
            "estimated_fees": "4.71",
            "leftover": "87.79",
        }
        assert data["warnings"] == ["NB1011 em saída na trajetória: vendido até o alvo"]


class TestTableRender:
    def test_title_and_columns_in_order(self) -> None:
        out = render(sample_rebalance())
        assert "Rebalanceamento anual de 2061 (tolerância 5.00%)" in out
        header = next(line for line in out.splitlines() if "Ticker" in line)
        order = ["Operação", "Preço", "Qtde", "Valor", "IR (est.)", "Target", "Peso atual", "Peso após"]
        assert [header.index(name) for name in order] == sorted(header.index(name) for name in order)

    def test_each_row_says_what_to_do(self) -> None:
        lines = render(sample_rebalance()).splitlines()
        mund = next(line for line in lines if "MUND11" in line)
        b5 = next(line for line in lines if "B5P211" in line)
        assert "Vender" in mund and "10,200.00" in mund and "306.00" in mund
        assert "Comprar" in b5 and "14,700.00" in b5

    def test_a_ticker_that_holds_shows_dashes(self) -> None:
        # Com 20 p.p. de tolerancia o MUND11 fica: sem quantidade, valor nem IR.
        rebalance = plan(drifted_portfolio(), {"NB1011"}, threshold=D("0.20"))
        mund = next(line for line in render(rebalance).splitlines() if "MUND11" in line)
        cells = [cell.strip() for cell in mund.split("│")]
        assert cells[1:8] == ["MUND11", "Manter", "100.00", "-", "-", "-", "30.00%"]

    def test_totals(self) -> None:
        out = render(sample_rebalance())
        assert "Vendas: 15,250.00 / IR (est.): 457.50 / Para compras: 14,792.50" in out
        assert "Compras: 14,700.00 / Taxa B3 (est.): 4.71 / Sobra (caixa): 87.79" in out

    def test_warnings_are_numbered(self) -> None:
        assert "Atenção:\n1. NB1011 em saída na trajetória: vendido até o alvo" in render(sample_rebalance())


@pytest.fixture
def clean(conn: psycopg.Connection[DictRow]) -> Iterator[psycopg.Connection[DictRow]]:
    yield conn
    conn.execute("TRUNCATE glide_path_points, user_settings")
    conn.commit()


class TestCliFlow:
    """CLI com carteira injetada: sem rede, mas com banco (as configuracoes)."""

    @pytest.fixture
    def runner(self, clean: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch) -> CliRunner:
        summary = drifted_portfolio()
        monkeypatch.setattr("bogle.cli.rebalance.default_dispatcher", lambda: None)
        monkeypatch.setattr("bogle.annual_rebalance.get_allocation_summary", lambda conn, dispatcher, **_: summary)
        return CliRunner()

    @pytest.fixture
    def retired(self, clean: psycopg.Connection[DictRow]) -> None:
        # 1950 + 70 (o default): o rebalanceamento anual comecou em 2021.
        set_value(clean, BIRTH_YEAR, 1950)
        clean.commit()

    def test_json_output(self, runner: CliRunner, retired: None) -> None:
        result = runner.invoke(app, ["rebalance", "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["year"] == THIS_YEAR
        # Sem trajetoria nada esta em saida: so o MUND11 sai da faixa.
        assert [(item["ticker"], item["operation"]) for item in data["items"]] == [
            ("MUND11", "SELL"),
            ("B5P211", "BUY"),
            ("NB1011", "HOLD"),
        ]

    def test_the_table(self, runner: CliRunner, retired: None) -> None:
        result = runner.invoke(app, ["rebalance"])
        assert result.exit_code == 0, result.output
        assert f"Rebalanceamento anual de {THIS_YEAR}" in result.stdout
        assert "Para compras:" in result.stdout

    def test_seeing_the_plan_records_nothing(
        self, runner: CliRunner, retired: None, clean: psycopg.Connection[DictRow]
    ) -> None:
        assert runner.invoke(app, ["rebalance"]).exit_code == 0
        assert get_setting(clean, LAST_ANNUAL_REBALANCE_YEAR) is None
        assert get_setting(clean, LAST_REBALANCE_DATE) is None

    def test_done_records_the_year(self, runner: CliRunner, retired: None, clean: psycopg.Connection[DictRow]) -> None:
        result = runner.invoke(app, ["rebalance", "--done"])
        assert result.exit_code == 0, result.output
        assert result.stdout.strip() == f"Rebalanceamento anual de {THIS_YEAR} registrado."
        assert get_setting(clean, LAST_ANNUAL_REBALANCE_YEAR) == THIS_YEAR

    def test_done_as_json(self, runner: CliRunner, retired: None) -> None:
        result = runner.invoke(app, ["rebalance", "--done", "--json"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout) == {"recorded_year": THIS_YEAR}

    def test_without_birth_year_it_refuses(self, runner: CliRunner, clean: psycopg.Connection[DictRow]) -> None:
        for command in (["rebalance"], ["rebalance", "--done"]):
            result = runner.invoke(app, command)
            assert result.exit_code != 0
            assert "Sem ano de nascimento" in str(result.exception)
        assert get_setting(clean, LAST_ANNUAL_REBALANCE_YEAR) is None

    def test_before_the_phase_it_refuses(self, runner: CliRunner, clean: psycopg.Connection[DictRow]) -> None:
        set_value(clean, BIRTH_YEAR, 2000)
        clean.commit()
        result = runner.invoke(app, ["rebalance"])
        assert result.exit_code != 0
        assert "Antes da aposentadoria: o rebalanceamento anual começa em 2071." in str(result.exception)


# ---- O aviso anual e o `bogle status`, de ponta a ponta (como em test_cli_status)


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(BOGLE_BIN), *args],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        cwd=str(PROJECT_ROOT),
        check=False,
    )


NOTICE = f"aviso: rebalanceamento anual de {THIS_YEAR} pendente (bogle rebalance)"


class TestAnnualReminder:
    @pytest.fixture(autouse=True)
    def _clean(self, clean: psycopg.Connection[DictRow]) -> None:
        """Requesting ``clean`` truncates bogle_test before the subprocess runs, and after."""

    def test_any_command_warns_while_pending(self) -> None:
        run_cli("config", "set", "birth_year", "1950")
        result = run_cli("list")
        assert result.returncode == 0
        assert NOTICE in result.stderr
        assert result.stderr.count("rebalanceamento anual") == 1

    def test_done_silences_it_until_next_year(self) -> None:
        run_cli("config", "set", "birth_year", "1950")
        done = run_cli("rebalance", "--done")
        assert done.returncode == 0, done.stderr
        assert "aviso" not in done.stderr  # o proprio --done nao avisa do que resolve
        assert "rebalanceamento anual" not in run_cli("list").stderr

    def test_before_the_phase_there_is_no_warning(self) -> None:
        run_cli("config", "set", "birth_year", "2000")
        assert "rebalanceamento anual" not in run_cli("list").stderr

    def test_without_birth_year_there_is_no_warning(self) -> None:
        assert "rebalanceamento anual" not in run_cli("list").stderr

    def test_status_shows_the_pendency_instead_of_warning(self) -> None:
        run_cli("config", "set", "birth_year", "1950")
        result = run_cli("status")
        assert result.returncode == 0
        assert "aviso" not in result.stderr
        assert f"Rebalanceamento anual de {THIS_YEAR} pendente. Rode 'bogle rebalance'." in result.stdout

    def test_status_after_done(self) -> None:
        run_cli("config", "set", "birth_year", "1950")
        run_cli("rebalance", "--done")
        assert f"Rebalanceamento anual de {THIS_YEAR} feito." in run_cli("status").stdout

    def test_status_before_the_phase_says_when_it_starts(self) -> None:
        run_cli("config", "set", "birth_year", "2000")
        assert "Rebalanceamento anual: a partir de 2071." in run_cli("status").stdout

    def test_status_without_birth_year_says_nothing_about_it(self) -> None:
        assert "Rebalanceamento anual" not in run_cli("status").stdout
