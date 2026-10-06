"""Tests for ``bogle suggest``: JSON/table rendering (unit) and the CLI flow with
an injected portfolio (no network). A ``@live`` smoke hits the real APIs.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import DictRow
from rich.console import Console
from typer.testing import CliRunner

from bogle.cli import app
from bogle.cli.suggest import _render, _suggestion_json
from bogle.domain.assets import AssetType
from bogle.position import PortfolioSummary, Position
from bogle.rebalancing import AporteSuggestion, TickerSuggestion, UnquotedTarget, suggest_allocation
from bogle.settings import LAST_REBALANCE_DATE, get_setting

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BOGLE_BIN = PROJECT_ROOT / ".venv" / "bin" / "bogle"


def sample_suggestion() -> AporteSuggestion:
    return AporteSuggestion(
        amount=Decimal("10000"),
        items=[
            TickerSuggestion(
                ticker="VWRA11",
                asset_type=AssetType.ETF,
                price=Decimal("100"),
                allocation=Decimal("9000"),
                quantity=Decimal("90"),
                effective_cost=Decimal("9000"),
                target_weight=Decimal("0.70"),
                weight_after=Decimal("0.6727"),
                current_weight=Decimal("0.64"),
            ),
            TickerSuggestion(
                ticker="CDB01",
                asset_type=AssetType.CDB,
                price=Decimal("1000"),
                allocation=Decimal("950.50"),
                quantity=None,
                effective_cost=Decimal("950.50"),
                target_weight=Decimal("0.30"),
                weight_after=Decimal("0.30"),
                current_weight=Decimal("0.36"),
            ),
        ],
        total_allocated=Decimal("9950.50"),
        estimated_fees=Decimal("2.88"),  # 0.032% dos 9000 do ETF; o CDB nao paga
        leftover=Decimal("46.62"),
        warnings=["CDB01 é renda fixa privada: registre como novo ativo"],
    )


class TestJson:
    def test_is_valid_and_normalized(self) -> None:
        data = _suggestion_json(sample_suggestion())
        json.dumps(data)  # must not raise
        vwra = data["items"][0]
        assert vwra["quantity"] == "90"
        assert vwra["effective_cost"] == "9000"
        assert data["totals"]["allocated"] == "9950.5"
        assert data["totals"]["estimated_fees"] == "2.88"
        assert data["totals"]["with_fees"] == "9953.38"
        assert data["totals"]["leftover"] == "46.62"
        assert data["warnings"]

    def test_fixed_income_quantity_is_null(self) -> None:
        data = _suggestion_json(sample_suggestion())
        assert data["items"][1]["quantity"] is None

    def test_carries_the_whole_trip_of_the_weight(self) -> None:
        # Peso de onde saiu, target, peso onde chegou e o que ainda falta: sem os
        # quatro, um script que le o JSON nao consegue dizer se o aporte resolveu.
        vwra = _suggestion_json(sample_suggestion())["items"][0]
        assert vwra["current_weight"] == "0.64"
        assert vwra["target_weight"] == "0.7"
        assert vwra["weight_after"] == "0.6727"
        assert vwra["drift_after"] == "-0.0273"


class TestTableRender:
    def test_renders_without_error(self) -> None:
        buffer = io.StringIO()
        _render(sample_suggestion(), Console(file=buffer, width=200))
        out = buffer.getvalue()
        assert "VWRA11" in out
        assert "Alocado: 9,950.50 / Taxa B3 (est.): 2.88 / Custo: 9,953.38" in out
        assert "Aporte: 10,000.00 / Sobra (caixa): 46.62" in out
        assert "Taxa B3 estimada" not in out  # o rotulo (est.) ja diz que e estimativa
        assert "Atenção:\n1. CDB01 é renda fixa privada: registre como novo ativo" in out

    def test_shows_the_weight_before_the_target_and_after(self) -> None:
        buffer = io.StringIO()
        _render(sample_suggestion(), Console(file=buffer, width=200))
        out = buffer.getvalue()
        header = next(line for line in out.splitlines() if "Ticker" in line)
        order = ["Preço", "Valor", "Qtde", "Custo", "Target", "Peso atual", "Peso após", "Drift após"]
        assert [header.index(name) for name in order] == sorted(header.index(name) for name in order)
        assert "64.00%" in out  # peso atual do VWRA11
        assert "67.27%" in out  # peso depois do aporte
        assert "-2.73%" in out  # o que ainda falta para o target de 70%

    def test_an_unquoted_target_says_how_to_bring_it_in(self) -> None:
        # O aviso do motor nao fala de flag; o caminho de volta e da CLI dizer.
        suggestion = replace(
            sample_suggestion(),
            unquoted=[UnquotedTarget("MUND11", AssetType.ETF, Decimal("0.70"), Decimal("0"))],
        )
        buffer = io.StringIO()
        _render(suggestion, Console(file=buffer, width=200))
        assert "--price MUND11=VALOR" in buffer.getvalue()

    def test_a_pinned_purchase_marks_what_was_informed(self) -> None:
        # Cotas na renda variavel, valor na renda fixa: o asterisco fica no numero
        # que veio de voce, como no preco informado.
        vwra, cdb = sample_suggestion().items
        suggestion = replace(sample_suggestion(), items=[replace(vwra, is_pinned=True), replace(cdb, is_pinned=True)])
        buffer = io.StringIO()
        _render(suggestion, Console(file=buffer, width=200))
        out = buffer.getvalue()
        assert "90 *" in out
        assert "9,000.00 *" not in out
        assert "950.50 *" in out

    def test_no_hint_when_every_target_has_a_quote(self) -> None:
        buffer = io.StringIO()
        _render(sample_suggestion(), Console(file=buffer, width=200))
        assert "--price" not in buffer.getvalue()


class TestCliFlow:
    """CLI com carteira injetada: sem rede, mas com banco (last_rebalance_date)."""

    @pytest.fixture
    def runner(self, conn: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch) -> CliRunner:
        summary = PortfolioSummary(
            positions=[
                Position(
                    ticker="VWRA11",
                    asset_type=AssetType.ETF,
                    quantity=Decimal("640"),
                    total_invested=Decimal("60000"),
                    target_weight=Decimal("0.70"),
                    dividends=Decimal("0"),
                    price=Decimal("100"),
                    market_value=Decimal("64000"),
                    current_weight=Decimal("0.64"),
                    drift=Decimal("-0.06"),
                ),
                Position(
                    ticker="B5P211",
                    asset_type=AssetType.ETF,
                    quantity=Decimal("400"),
                    total_invested=Decimal("30000"),
                    target_weight=Decimal("0.30"),
                    dividends=Decimal("0"),
                    price=Decimal("90"),
                    market_value=Decimal("36000"),
                    current_weight=Decimal("0.36"),
                    drift=Decimal("0.06"),
                ),
            ],
            total_value=Decimal("100000"),
            total_invested=Decimal("90000"),
            total_pnl=Decimal("10000"),
            total_dividends=Decimal("0"),
        )
        monkeypatch.setattr("bogle.cli.suggest.default_dispatcher", lambda: None)
        monkeypatch.setattr("bogle.cli.suggest.get_allocation_summary", lambda conn, dispatcher: summary)
        return CliRunner()

    def test_json_output(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["suggest", "--amount", "10000", "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        vwra = next(item for item in data["items"] if item["ticker"] == "VWRA11")
        assert vwra["quantity"] == "100"
        assert data["totals"]["estimated_fees"] == "3.2"
        assert data["totals"]["leftover"] == "-3.2"  # o floor gastou tudo, e a taxa fica faltando

    def test_records_last_rebalance_date(self, runner: CliRunner, conn: psycopg.Connection[DictRow]) -> None:
        assert get_setting(conn, LAST_REBALANCE_DATE) is None
        result = runner.invoke(app, ["suggest", "--amount", "10000"])
        assert result.exit_code == 0, result.output
        assert get_setting(conn, LAST_REBALANCE_DATE) == date.today()

    def test_invalid_amount_fails_without_recording(self, runner: CliRunner, conn: psycopg.Connection[DictRow]) -> None:
        result = runner.invoke(app, ["suggest", "--amount", "-5"])
        assert result.exit_code != 0
        assert get_setting(conn, LAST_REBALANCE_DATE) is None

    def test_price_changes_the_shares_and_marks_the_row(self, runner: CliRunner) -> None:
        # Ordem limitada: com 80 no lugar dos 100 de mercado, os 10.000 compram
        # 125 cotas em vez de 100 — e a linha diz que o preco e do usuario.
        result = runner.invoke(app, ["suggest", "--amount", "10000", "--price", "VWRA11=80"])
        assert result.exit_code == 0, result.output
        assert "*" in result.stdout
        assert "1. Preço de VWRA11 definido pelo usuário" in result.stdout

    def test_price_in_json_keeps_the_quote_beside_it(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["suggest", "--amount", "10000", "--price", "vwra11=80", "--json"])
        assert result.exit_code == 0, result.output
        vwra = next(item for item in json.loads(result.stdout)["items"] if item["ticker"] == "VWRA11")
        assert vwra["price"] == "80"
        assert vwra["quoted_price"] == "100"
        assert vwra["manual_price"] is True
        assert vwra["quantity"] == "125"

    def test_qty_pins_the_ticker_and_the_rest_goes_to_the_others(self, runner: CliRunner) -> None:
        # B5P211 esta acima do target e nao receberia nada; fixado em 10 cotas
        # (900), o VWRA11 fica com os 9.100 que sobraram: 91 cotas em vez de 100.
        result = runner.invoke(app, ["suggest", "--amount", "10000", "--qty", "b5p211=10", "--json"])
        assert result.exit_code == 0, result.output
        items = {item["ticker"]: item for item in json.loads(result.stdout)["items"]}
        assert items["B5P211"]["quantity"] == "10"
        assert items["B5P211"]["pinned"] is True
        assert items["VWRA11"]["quantity"] == "91"
        assert items["VWRA11"]["pinned"] is False

    def test_qty_is_marked_and_explained_in_the_table(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["suggest", "--amount", "10000", "--qty", "B5P211=10"])
        assert result.exit_code == 0, result.output
        assert "10 *" in result.stdout
        assert "1. Compra em B5P211 fixada pelo usuário" in result.stdout

    def test_value_on_variable_income_fails_without_recording(
        self, runner: CliRunner, conn: psycopg.Connection[DictRow]
    ) -> None:
        result = runner.invoke(app, ["suggest", "--amount", "10000", "--value", "VWRA11=500"])
        assert result.exit_code != 0
        assert get_setting(conn, LAST_REBALANCE_DATE) is None

    def test_a_price_for_a_ticker_outside_the_portfolio_fails(
        self, runner: CliRunner, conn: psycopg.Connection[DictRow]
    ) -> None:
        result = runner.invoke(app, ["suggest", "--amount", "10000", "--price", "XPTO11=80"])
        assert result.exit_code != 0
        assert get_setting(conn, LAST_REBALANCE_DATE) is None

    def test_a_malformed_price_fails_before_touching_the_database(
        self, runner: CliRunner, conn: psycopg.Connection[DictRow]
    ) -> None:
        result = runner.invoke(app, ["suggest", "--amount", "10000", "--price", "VWRA11"])
        assert result.exit_code != 0
        assert get_setting(conn, LAST_REBALANCE_DATE) is None


@pytest.mark.live
def test_live_suggest_json() -> None:
    """Full stack against real APIs. Deselected by default."""
    env = os.environ.copy()

    def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(BOGLE_BIN), *args], capture_output=True, text=True, env=env, cwd=str(PROJECT_ROOT), check=False
        )

    assert run_cli("add", "PETR4", "-w", "0.4").returncode == 0
    assert run_cli("buy", "PETR4", "-s", "10", "-p", "20", "--date", "2026-01-05").returncode == 0
    result = run_cli("suggest", "--amount", "1000", "--json")
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert Decimal(data["totals"]["allocated"]) <= Decimal("1000")


def test_engine_and_cli_agree_on_issue_example() -> None:
    """O exemplo da issue #23 renderiza de ponta a ponta sem erro."""
    summary = PortfolioSummary(
        positions=[
            Position(
                ticker="VWRA11",
                asset_type=AssetType.ETF,
                quantity=Decimal("640"),
                total_invested=Decimal("60000"),
                target_weight=Decimal("0.70"),
                dividends=Decimal("0"),
                price=Decimal("100"),
                market_value=Decimal("64000"),
                current_weight=Decimal("0.64"),
                drift=Decimal("-0.06"),
            ),
        ],
        total_value=Decimal("64000"),
        total_invested=Decimal("60000"),
        total_pnl=Decimal("4000"),
        total_dividends=Decimal("0"),
    )
    buffer = io.StringIO()
    _render(suggest_allocation(summary, Decimal("10000")), Console(file=buffer, width=200))
    assert "VWRA11" in buffer.getvalue()
