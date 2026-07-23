"""Tests for ``bogle.reports.summary`` (issue #28): pure window profit and
income received. The CLI flow now lives in ``bogle position`` and is covered
by ``tests/test_cli_position.py``.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from bogle.domain.transactions import Transaction, TransactionType
from bogle.reports.summary import income_received, window_profit
from tests.test_dividends import make_buy, make_income

_ZERO = Decimal("0")


def make_sell(ticker: str, on: str, proceeds: str) -> Transaction:
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
