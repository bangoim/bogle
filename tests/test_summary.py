"""Tests for ``bogle summary`` (issue #28): pure window profit and the CLI flow."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import psycopg
import pytest
from psycopg.rows import DictRow
from typer.testing import CliRunner

from bogle.cli import app
from bogle.domain.assets import AssetType
from bogle.domain.transactions import TransactionType
from bogle.position import PortfolioSummary, Position
from bogle.reports.summary import income_received, window_profit
from tests.test_dividends import make_buy, make_income

_ZERO = Decimal("0")


def make_sell(ticker: str, on: str, proceeds: str) -> object:
    from datetime import datetime

    from bogle.domain.transactions import Transaction

    return Transaction(
        id=9999,
        ticker=ticker,
        transaction_type=TransactionType.SELL,
        date=datetime.fromisoformat(on),
        shares=Decimal("5"),
        unit_price=Decimal(proceeds) / Decimal("5"),
        total_investment=Decimal(proceeds),
        fees=Decimal("0"),
        total_cost=Decimal("0"),
        tax_withheld=Decimal("0"),
    )


class TestWindowProfit:
    def test_pure_price_appreciation(self) -> None:
        profit = window_profit([], Decimal("100"), Decimal("110"), start=date(2026, 6, 22), end=date(2026, 7, 22))
        assert profit == Decimal("10")

    def test_aporte_is_not_profit(self) -> None:
        txns = [make_buy("PETR4", "2026-07-01")]  # 100 investidos no meio da janela
        profit = window_profit(txns, Decimal("100"), Decimal("200"), start=date(2026, 6, 22), end=date(2026, 7, 22))
        assert profit == _ZERO

    def test_sale_proceeds_and_income_count_as_profit_components(self) -> None:
        txns = [
            make_sell("PETR4", "2026-07-01", "50"),
            make_income("PETR4", TransactionType.JCP, "20", "2026-07-02", tax_withheld="3"),
        ]
        # Patrimonio caiu 50 pela venda, mas o caixa recebeu 50 + 17 liquidos.
        profit = window_profit(txns, Decimal("100"), Decimal("50"), start=date(2026, 6, 22), end=date(2026, 7, 22))
        assert profit == Decimal("17")

    def test_flows_on_window_start_are_excluded(self) -> None:
        txns = [make_buy("PETR4", "2026-06-22")]  # ja dentro do value_start (fim do dia)
        profit = window_profit(txns, Decimal("100"), Decimal("100"), start=date(2026, 6, 22), end=date(2026, 7, 22))
        assert profit == _ZERO


class TestIncomeReceived:
    def test_sums_net_within_window(self) -> None:
        txns = [
            make_income("ITUB4", TransactionType.JCP, "100", "2026-06-01", tax_withheld="15"),
            make_income("ITUB4", TransactionType.DIVIDEND, "50", "2020-01-01"),  # fora
        ]
        assert income_received(txns, start=date(2026, 1, 1), end=date(2026, 7, 22)) == Decimal("85")


class TestCliFlow:
    @pytest.fixture
    def runner(self, conn: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch) -> CliRunner:
        position = Position(
            ticker="PETR4",
            asset_type=AssetType.STOCK,
            quantity=Decimal("10"),
            total_invested=Decimal("105000"),
            target_weight=Decimal("0.5"),
            dividends=Decimal("0"),
            price=Decimal("20"),
            market_value=Decimal("152847.32"),
            current_weight=Decimal("1"),
            drift=Decimal("0.5"),
        )
        portfolio = PortfolioSummary(
            positions=[position],
            total_value=Decimal("152847.32"),
            total_invested=Decimal("105000.00"),
            total_pnl=Decimal("47847.32"),
            total_dividends=Decimal("0"),
        )

        class FakeValuation:
            def __init__(self) -> None:
                self.valuator = lambda holdings, on: Decimal("0")
                self.transactions: list[object] = []
                self.excluded = ["TESOURO SELIC 2029"]

        today = date.today()
        values = {today: Decimal("152847.32"), None: Decimal("0")}

        monkeypatch.setattr("bogle.cli.summary.default_dispatcher", lambda: None)
        monkeypatch.setattr("bogle.cli.summary.get_portfolio_summary", lambda conn, dispatcher: portfolio)
        monkeypatch.setattr(
            "bogle.cli.summary.build_portfolio_valuation",
            lambda conn, dispatcher, *, start, end: FakeValuation(),
        )
        monkeypatch.setattr(
            "bogle.cli.summary.patrimony_at",
            lambda valuation, on: values.get(on, Decimal("151427.17")),
        )
        return CliRunner()

    def test_json_output(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["summary", "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["total_value"] == "152847.32"
        assert data["variation"] == "47847.32"
        assert data["month_profit"] == "1420.15"  # 152847.32 - 151427.17
        assert data["month_profit_excluded"] == ["TESOURO SELIC 2029"]

    def test_table_output_shows_note_for_excluded(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["summary"])
        assert result.exit_code == 0, result.output
        assert "Resumo da carteira" in result.stdout
        assert "+45.57%" in result.stdout
        assert "TESOURO SELIC 2029" in result.stdout

    def test_empty_portfolio(self, conn: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch) -> None:
        empty = PortfolioSummary([], _ZERO, _ZERO, _ZERO, _ZERO)
        monkeypatch.setattr("bogle.cli.summary.default_dispatcher", lambda: None)
        monkeypatch.setattr("bogle.cli.summary.get_portfolio_summary", lambda conn, dispatcher: empty)
        monkeypatch.setattr(
            "bogle.cli.summary.build_portfolio_valuation",
            lambda conn, dispatcher, *, start, end: None,
        )
        monkeypatch.setattr("bogle.cli.summary.patrimony_at", lambda valuation, on: None)
        result = CliRunner().invoke(app, ["summary"])
        assert result.exit_code == 0, result.output
        assert "Nenhuma posicao ativa." in result.stdout
