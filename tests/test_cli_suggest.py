"""Tests for ``bogle suggest``: JSON/table rendering (unit) and the CLI flow with
an injected portfolio (no network). A ``@live`` smoke hits the real APIs.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
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
from bogle.rebalancing import AporteSuggestion, TickerSuggestion, suggest_allocation
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
            ),
        ],
        total_allocated=Decimal("9950.50"),
        leftover=Decimal("49.50"),
        warnings=["Aporte em renda fixa privada (CDB01) cria um novo contrato."],
    )


class TestJson:
    def test_is_valid_and_normalized(self) -> None:
        data = _suggestion_json(sample_suggestion())
        json.dumps(data)  # must not raise
        vwra = data["items"][0]
        assert vwra["quantity"] == "90"
        assert vwra["effective_cost"] == "9000"
        assert data["totals"]["allocated"] == "9950.5"
        assert data["totals"]["leftover"] == "49.5"
        assert data["warnings"]

    def test_fixed_income_quantity_is_null(self) -> None:
        data = _suggestion_json(sample_suggestion())
        assert data["items"][1]["quantity"] is None


class TestTableRender:
    def test_renders_without_error(self) -> None:
        buffer = io.StringIO()
        _render(sample_suggestion(), Console(file=buffer, width=200))
        out = buffer.getvalue()
        assert "VWRA11" in out
        assert "Total alocado: 9950.50 / Aporte: 10000.00" in out
        assert "Sobra (caixa): 49.50" in out
        assert "novo contrato" in out


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
        monkeypatch.setattr("bogle.cli.suggest.get_portfolio_summary", lambda conn, dispatcher: summary)
        return CliRunner()

    def test_json_output(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["suggest", "--amount", "10000", "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        vwra = next(item for item in data["items"] if item["ticker"] == "VWRA11")
        assert vwra["quantity"] == "100"
        assert data["totals"]["leftover"] == "0"

    def test_records_last_rebalance_date(self, runner: CliRunner, conn: psycopg.Connection[DictRow]) -> None:
        assert get_setting(conn, LAST_REBALANCE_DATE) is None
        result = runner.invoke(app, ["suggest", "--amount", "10000"])
        assert result.exit_code == 0, result.output
        assert get_setting(conn, LAST_REBALANCE_DATE) == date.today()

    def test_invalid_amount_fails_without_recording(self, runner: CliRunner, conn: psycopg.Connection[DictRow]) -> None:
        result = runner.invoke(app, ["suggest", "--amount", "-5"])
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
