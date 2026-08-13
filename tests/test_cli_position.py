"""Tests for ``bogle position``: JSON/table rendering (unit) and end-to-end via
subprocess in ``--no-prices`` mode (network-free). A ``@live`` smoke hits the real
APIs.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from rich.console import Console

from bogle.cli.position import _render, _summary_json
from bogle.domain.assets import AssetType
from bogle.position import PortfolioSummary, Position

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BOGLE_BIN = PROJECT_ROOT / ".venv" / "bin" / "bogle"


@pytest.fixture(autouse=True)
def _truncate_for_cli(conn: psycopg.Connection) -> Iterator[None]:
    """Requesting `conn` truncates bogle_test before the subprocess runs."""
    yield


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(BOGLE_BIN), *args],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        cwd=str(PROJECT_ROOT),
        check=False,
    )


def sample_summary() -> PortfolioSummary:
    priced = Position(
        ticker="PETR4", asset_type=AssetType.STOCK, quantity=Decimal("10"), total_invested=Decimal("200"),
        target_weight=Decimal("0.4"), dividends=Decimal("5"), average_price=Decimal("20.5"),
        price=Decimal("22"), market_value=Decimal("220"),
        current_weight=Decimal("1"), drift=Decimal("0.6"), pnl=Decimal("20"), pnl_percent=Decimal("0.1"),
        twr=Decimal("0.1275"), price_source="brapi", as_of=datetime(2026, 2, 1, 18, 30, tzinfo=UTC),
    )  # fmt: skip
    unpriced = Position(
        ticker="CDB01", asset_type=AssetType.CDB, quantity=Decimal("1"), total_invested=Decimal("1000"),
        target_weight=Decimal("0.4"), dividends=Decimal("0"),
    )  # fmt: skip
    return PortfolioSummary([priced, unpriced], Decimal("220"), Decimal("1200"), Decimal("20"), Decimal("5"))


class TestJson:
    def test_is_valid_and_normalized(self) -> None:
        data = _summary_json(sample_summary())
        json.dumps(data)  # must not raise
        petr4 = data["positions"][0]
        assert petr4["average_price"] == "20.5"
        assert petr4["price"] == "22"
        assert petr4["current_weight"] == "1"
        assert petr4["twr"] == "0.1275"
        assert petr4["price_source"] == "brapi"
        assert petr4["as_of"] == "2026-02-01T18:30:00+00:00"

    def test_unpriced_fields_are_null(self) -> None:
        data = _summary_json(sample_summary())
        cdb = data["positions"][1]
        assert cdb["average_price"] is None
        assert cdb["price"] is None
        assert cdb["market_value"] is None
        assert cdb["as_of"] is None

    def test_totals(self) -> None:
        totals = _summary_json(sample_summary())["totals"]
        assert totals["invested"] == "1200"
        assert totals["value"] == "220"
        assert totals["pnl"] == "20"

    def test_totals_include_month_profit_and_income(self) -> None:
        totals = _summary_json(
            sample_summary(),
            month_profit=Decimal("1420.15"),
            income_12m=Decimal("85"),
            excluded=["TESOURO SELIC 2029"],
        )["totals"]
        assert totals["month_profit"] == "1420.15"
        assert totals["income_12m"] == "85"
        assert totals["month_profit_excluded"] == ["TESOURO SELIC 2029"]

    def test_totals_extras_default_to_null(self) -> None:
        totals = _summary_json(sample_summary())["totals"]
        assert totals["month_profit"] is None
        assert totals["income_12m"] is None
        assert totals["month_profit_excluded"] == []


class TestTableRender:
    def test_renders_without_error(self) -> None:
        buffer = io.StringIO()
        _render(sample_summary(), Console(file=buffer, width=200))
        out = buffer.getvalue()
        assert "PETR4" in out
        assert "Total investido" in out
        assert "Fonte(s) de preco: brapi" in out
        assert "Cotacao mais recente" in out

    def test_renders_month_profit_income_and_excluded_note(self) -> None:
        buffer = io.StringIO()
        _render(
            sample_summary(),
            Console(file=buffer, width=200),
            month_profit=Decimal("1420.15"),
            income_12m=Decimal("85"),
            excluded=["TESOURO SELIC 2029"],
        )
        out = buffer.getvalue()
        assert "Lucro do mes" in out
        assert "+1,420.15" in out
        assert "Proventos (12m)" in out
        assert "+85.00" in out
        assert "TESOURO SELIC 2029" in out  # nota de exclusao


class TestEndToEnd:
    def test_empty_portfolio(self) -> None:
        result = run_cli("position", "--no-prices")
        assert result.returncode == 0
        assert "Nenhuma posicao ativa" in result.stdout

    def test_no_prices_json(self) -> None:
        assert run_cli("add", "PETR4", "-w", "0.4").returncode == 0
        assert run_cli("buy", "PETR4", "-s", "10", "-p", "20", "--date", "2026-01-05").returncode == 0
        result = run_cli("position", "--no-prices", "--json")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        petr4 = next(p for p in data["positions"] if p["ticker"] == "PETR4")
        assert petr4["quantity"] == "10"
        assert petr4["price"] is None  # --no-prices
        assert data["totals"]["invested"] == "200"

    def test_no_prices_table(self) -> None:
        assert run_cli("add", "PETR4", "-w", "0.4").returncode == 0
        assert run_cli("buy", "PETR4", "-s", "10", "-p", "20", "--date", "2026-01-05").returncode == 0
        result = run_cli("position", "--no-prices")
        assert result.returncode == 0
        assert "PETR4" in result.stdout
        assert "Total investido" in result.stdout
        assert "Proventos (12m)" in result.stdout

    def test_no_prices_income_counted_month_profit_null(self) -> None:
        # Proventos (12m) sai da base (sem precos); lucro do mes exige historico
        # de precos, entao fica nulo sob --no-prices.
        assert run_cli("add", "ITUB4", "-w", "0.4").returncode == 0
        assert run_cli("buy", "ITUB4", "-s", "10", "-p", "20", "--date", "2026-01-05").returncode == 0
        assert (
            run_cli("income", "ITUB4", "--type", "DIVIDEND", "--amount", "100", "--date", "2026-03-01").returncode == 0
        )
        result = run_cli("position", "--no-prices", "--json")
        assert result.returncode == 0
        totals = json.loads(result.stdout)["totals"]
        assert totals["income_12m"] == "100"
        assert totals["month_profit"] is None


@pytest.mark.live
def test_live_priced_json() -> None:
    """Full stack against real APIs (brapi + yfinance). Deselected by default."""
    assert run_cli("add", "PETR4", "-w", "0.4").returncode == 0
    assert run_cli("buy", "PETR4", "-s", "10", "-p", "20", "--date", "2026-01-05").returncode == 0
    result = run_cli("position", "--json")
    assert result.returncode == 0
    petr4 = next(p for p in json.loads(result.stdout)["positions"] if p["ticker"] == "PETR4")
    assert petr4["price"] is not None
    assert petr4["price_source"] in {"brapi", "yfinance"}


def test_table_columns_are_the_ones_the_interface_shows() -> None:
    # A tela de Posicao promete as mesmas colunas do comando: se uma das duas
    # mudar sozinha, a promessa vira mentira.
    from bogle.tui.screens.position import _COLUMNS

    buffer = io.StringIO()
    _render(sample_summary(), Console(file=buffer, width=200))
    out = buffer.getvalue()
    for header in _COLUMNS:
        assert header in out, header
    assert "20.50" in out  # preco medio da posicao precificada
