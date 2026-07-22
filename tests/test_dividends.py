"""Tests for the income report engine and period parser (issues #30/#67)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from bogle.domain.errors import ValidationError
from bogle.domain.transactions import Transaction, TransactionType
from bogle.reports.dividends import income_by_month, income_by_ticker, twelve_month_start
from bogle.reports.periods import add_months, parse_period, period_start

_ID = iter(range(1, 10_000))


def make_income(
    ticker: str,
    income_type: TransactionType,
    amount: str,
    on: str,
    tax_withheld: str = "0",
) -> Transaction:
    return Transaction(
        id=next(_ID),
        ticker=ticker,
        transaction_type=income_type,
        date=datetime.fromisoformat(on),
        shares=Decimal("0"),
        unit_price=Decimal("0"),
        total_investment=Decimal(amount),
        fees=Decimal("0"),
        total_cost=Decimal("0"),
        tax_withheld=Decimal(tax_withheld),
    )


def make_buy(ticker: str, on: str) -> Transaction:
    return Transaction(
        id=next(_ID),
        ticker=ticker,
        transaction_type=TransactionType.BUY,
        date=datetime.fromisoformat(on),
        shares=Decimal("10"),
        unit_price=Decimal("10"),
        total_investment=Decimal("100"),
        fees=Decimal("0"),
        total_cost=Decimal("100"),
        tax_withheld=Decimal("0"),
    )


class TestIncomeByMonth:
    def test_buckets_by_calendar_month_with_gaps_filled(self) -> None:
        txns = [
            make_income("HGLG11", TransactionType.RENDIMENTO, "890", "2026-03-10"),
            make_income("HGLG11", TransactionType.RENDIMENTO, "890", "2026-05-10"),
        ]
        rows = income_by_month(txns, start=date(2026, 3, 1), end=date(2026, 5, 31))
        assert [r.month for r in rows] == [date(2026, 3, 1), date(2026, 4, 1), date(2026, 5, 1)]
        assert rows[0].rendimento == Decimal("890")
        assert rows[1].total == Decimal("0")  # mes sem provento aparece zerado

    def test_jcp_is_net_of_withheld_tax(self) -> None:
        txns = [make_income("ITUB4", TransactionType.JCP, "100", "2026-06-01", tax_withheld="15")]
        [row] = income_by_month(txns, start=date(2026, 6, 1), end=date(2026, 6, 30))
        assert row.jcp == Decimal("85")

    def test_other_types_are_gross(self) -> None:
        txns = [
            make_income("PETR4", TransactionType.DIVIDEND, "120", "2026-06-01"),
            make_income("CDB01", TransactionType.INTEREST, "50", "2026-06-02", tax_withheld="10"),
        ]
        [row] = income_by_month(txns, start=date(2026, 6, 1), end=date(2026, 6, 30))
        assert row.dividend == Decimal("120")
        assert row.interest == Decimal("50")  # INTEREST bruto (so JCP e liquido)

    def test_ignores_trades_and_out_of_window_income(self) -> None:
        txns = [
            make_buy("PETR4", "2026-06-01"),
            make_income("PETR4", TransactionType.DIVIDEND, "10", "2025-01-01"),  # antes da janela
            make_income("PETR4", TransactionType.DIVIDEND, "20", "2026-06-05"),
        ]
        rows = income_by_month(txns, start=date(2026, 6, 1), end=date(2026, 6, 30))
        assert len(rows) == 1
        assert rows[0].total == Decimal("20")

    def test_unbounded_starts_at_first_income(self) -> None:
        txns = [make_income("PETR4", TransactionType.DIVIDEND, "10", "2026-04-15")]
        rows = income_by_month(txns, start=None, end=date(2026, 6, 30))
        assert [r.month for r in rows] == [date(2026, 4, 1), date(2026, 5, 1), date(2026, 6, 1)]

    def test_empty(self) -> None:
        assert income_by_month([], start=None, end=date(2026, 6, 30)) == []


class TestIncomeByTicker:
    def test_groups_by_ticker_and_type_sorted_by_total(self) -> None:
        txns = [
            make_income("ITUB4", TransactionType.DIVIDEND, "1200", "2026-05-01"),
            make_income("ITUB4", TransactionType.JCP, "400", "2026-05-01", tax_withheld="40"),
            make_income("HGLG11", TransactionType.RENDIMENTO, "5340", "2026-05-01"),
        ]
        rows = income_by_ticker(txns, start=None, end=date(2026, 6, 30))
        assert [(r.ticker, r.income_type, r.total) for r in rows] == [
            ("HGLG11", TransactionType.RENDIMENTO, Decimal("5340")),
            ("ITUB4", TransactionType.DIVIDEND, Decimal("1200")),
            ("ITUB4", TransactionType.JCP, Decimal("360")),
        ]


class TestPeriods:
    def test_add_months_clamps_day(self) -> None:
        assert add_months(date(2026, 3, 31), -1) == date(2026, 2, 28)
        assert add_months(date(2026, 1, 31), -1) == date(2025, 12, 31)
        assert add_months(date(2026, 1, 15), 1) == date(2026, 2, 15)

    def test_twelve_month_start_is_calendar_window(self) -> None:
        assert twelve_month_start(date(2026, 7, 22)) == date(2025, 8, 1)

    def test_period_start(self) -> None:
        today = date(2026, 7, 22)
        assert period_start("all", today=today) is None
        assert period_start("total", today=today) is None
        assert period_start("ytd", today=today) == date(2026, 1, 1)
        assert period_start("1m", today=today) == date(2026, 6, 22)
        assert period_start("12m", today=today) == date(2025, 7, 22)
        assert period_start("2y", today=today) == date(2024, 7, 22)

    def test_parse_period_validates(self) -> None:
        assert parse_period(" 12M ") == "12m"
        with pytest.raises(ValidationError, match="--period invalido"):
            parse_period("3m")
        with pytest.raises(ValidationError, match="12m, all"):
            parse_period("ytd", allowed=("12m", "all"))
