"""Tests for ``bogle profit`` (issue #29): decomposition engine and CLI."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import psycopg
import pytest
from psycopg.rows import DictRow
from typer.testing import CliRunner

from bogle.cli import app
from bogle.domain.assets import AssetType
from bogle.domain.errors import ValidationError
from bogle.domain.transactions import TransactionType
from bogle.position import PortfolioSummary, Position
from bogle.reports.profit import compute_profit
from tests.test_dividends import make_income
from tests.test_realized_gains import buy, sell

_ZERO = Decimal("0")

TODAY = date(2026, 7, 20)


def make_position(ticker: str, quantity: str, market_value: str | None) -> Position:
    value = Decimal(market_value) if market_value is not None else None
    return Position(
        ticker=ticker,
        asset_type=AssetType.STOCK,
        quantity=Decimal(quantity),
        total_invested=Decimal("1"),
        target_weight=Decimal("0.5"),
        dividends=_ZERO,
        price=Decimal("1") if value is not None else None,
        market_value=value,
    )


def make_portfolio(*positions: Position) -> PortfolioSummary:
    total = sum((p.market_value for p in positions if p.market_value is not None), _ZERO)
    return PortfolioSummary(list(positions), total, _ZERO, _ZERO, _ZERO)


class TestComputeProfit:
    def test_decomposes_realized_unrealized_and_income(self) -> None:
        # buy 10@10, vende 5@20 (realizado 50), restante vale 30 cada (nao realizado 100).
        transactions = [
            buy("10", "10", "2026-01-05", ticker="PETR4"),
            sell("5", "20", "2026-02-01", ticker="PETR4"),
            make_income("PETR4", TransactionType.JCP, "100", "2026-03-01", tax_withheld="15"),
            make_income("PETR4", TransactionType.DIVIDEND, "40", "2026-03-02"),
        ]
        portfolio = make_portfolio(make_position("PETR4", "5", "150"))
        report = compute_profit(portfolio, transactions, income_start=None, income_end=TODAY)
        assert report.since == date(2026, 1, 5)
        assert report.realized == Decimal("50")  # 100 - 5*10
        assert report.unrealized == Decimal("100")  # 150 - 5*10
        assert report.capital_total == Decimal("150")
        assert report.income_by_type[TransactionType.JCP] == Decimal("85")  # liquido
        assert report.income_by_type[TransactionType.DIVIDEND] == Decimal("40")
        assert report.income_total == Decimal("125")
        assert report.total == Decimal("275")

    def test_position_pnl_is_not_unrealized_gain(self) -> None:
        # A prova do ajuste da issue: pnl da view = 150 - (100 - 100) = 150,
        # mas o nao realizado correto e 100 (o realizado de 50 fica separado).
        transactions = [buy("10", "10", "2026-01-05", ticker="PETR4"), sell("5", "20", "2026-02-01", ticker="PETR4")]
        portfolio = make_portfolio(make_position("PETR4", "5", "150"))
        report = compute_profit(portfolio, transactions, income_start=None, income_end=TODAY)
        assert report.unrealized == Decimal("100")
        assert report.realized == Decimal("50")

    def test_income_window(self) -> None:
        transactions = [
            buy("10", "10", "2020-01-05", ticker="PETR4"),
            make_income("PETR4", TransactionType.DIVIDEND, "40", "2020-06-01"),  # fora da janela
            make_income("PETR4", TransactionType.DIVIDEND, "60", "2026-06-01"),
        ]
        portfolio = make_portfolio(make_position("PETR4", "10", "100"))
        report = compute_profit(portfolio, transactions, income_start=date(2025, 8, 1), income_end=TODAY)
        assert report.income_total == Decimal("60")
        assert report.realized + report.unrealized == report.capital_total  # capital continua total

    def test_unpriced_positions_are_reported(self) -> None:
        transactions = [buy("1", "1000", "2026-01-05", ticker="CDB01")]
        portfolio = make_portfolio(make_position("CDB01", "1", None))
        report = compute_profit(portfolio, transactions, income_start=None, income_end=TODAY)
        assert report.unrealized == _ZERO
        assert report.unpriced == ["CDB01"]

    def test_no_transactions_is_friendly(self) -> None:
        with pytest.raises(ValidationError, match="Nenhuma transacao"):
            compute_profit(make_portfolio(), [], income_start=None, income_end=TODAY)


class TestCli:
    @pytest.fixture
    def runner(self, conn: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch) -> CliRunner:
        transactions = [
            buy("10", "10", "2026-01-05", ticker="PETR4"),
            sell("5", "20", "2026-02-01", ticker="PETR4"),
            make_income("PETR4", TransactionType.JCP, "100", "2026-03-01", tax_withheld="15"),
        ]
        portfolio = make_portfolio(make_position("PETR4", "5", "150"))
        monkeypatch.setattr("bogle.cli.profit.default_dispatcher", lambda: None)
        monkeypatch.setattr("bogle.cli.profit.get_portfolio_summary", lambda conn, dispatcher: portfolio)

        class FakeRepo:
            def __init__(self, _conn: object) -> None:
                pass

            def list(self) -> list:
                return transactions

        monkeypatch.setattr("bogle.cli.profit.TransactionRepository", FakeRepo)
        return CliRunner()

    def test_full_panel(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["profit"])
        assert result.exit_code == 0, result.output
        assert "Lucro da carteira (desde 2026-01-05)" in result.stdout
        assert "+150.00" in result.stdout  # ganho de capital
        assert "+50.00" in result.stdout  # realizado
        assert "+100.00" in result.stdout  # nao realizado
        assert "JCP (liquido)" in result.stdout
        assert "+85.00" in result.stdout
        assert "Lucro total" in result.stdout
        assert "+235.00" in result.stdout

    def test_12m_period_omits_grand_total(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["profit", "--period", "12m"])
        assert result.exit_code == 0, result.output
        assert "ultimos 12 meses" in result.stdout
        assert "Lucro total omitido" in result.stdout
