"""Tests for the end-of-day rule of the ledger (``bogle.domain.ledger``)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from itertools import count

from bogle.domain.ledger import end_of_day_positions, first_uncovered, sellable_on
from bogle.domain.transactions import Transaction, TransactionType

_ID = count(1)


def trade(kind: TransactionType, shares: str, on: str) -> Transaction:
    day = date.fromisoformat(on)
    return Transaction(
        id=next(_ID),
        ticker="PETR4",
        transaction_type=kind,
        date=datetime(day.year, day.month, day.day, 12, tzinfo=UTC),
        shares=Decimal(shares),
        unit_price=Decimal("10"),
        total_investment=Decimal(shares) * 10,
        fees=Decimal("0"),
        total_cost=Decimal(shares) * 10,
        tax_withheld=Decimal("0"),
    )


def buy(shares: str, on: str) -> Transaction:
    return trade(TransactionType.BUY, shares, on)


def sell(shares: str, on: str) -> Transaction:
    return trade(TransactionType.SELL, shares, on)


def income(on: str) -> Transaction:
    day = date.fromisoformat(on)
    return Transaction(
        id=next(_ID),
        ticker="PETR4",
        transaction_type=TransactionType.DIVIDEND,
        date=datetime(day.year, day.month, day.day, 12, tzinfo=UTC),
        shares=Decimal("0"),
        unit_price=Decimal("0"),
        total_investment=Decimal("5"),
        fees=Decimal("0"),
        total_cost=Decimal("0"),
        tax_withheld=Decimal("0"),
    )


class TestEndOfDay:
    def test_one_close_per_trading_day(self) -> None:
        history = [buy("10", "2026-01-05"), sell("4", "2026-03-10"), income("2026-02-01")]
        assert end_of_day_positions(history) == [(date(2026, 1, 5), Decimal("10")), (date(2026, 3, 10), Decimal("6"))]

    def test_a_day_nets_its_trades_whatever_the_order_they_were_registered(self) -> None:
        # Venda registrada antes da compra do mesmo dia: no fechamento, cobre.
        history = [sell("5", "2026-01-05"), buy("10", "2026-01-05")]
        assert end_of_day_positions(history) == [(date(2026, 1, 5), Decimal("5"))]
        assert first_uncovered(history) is None


class TestSellable:
    def test_the_position_of_the_day_is_the_ceiling(self) -> None:
        room = sellable_on([buy("10", "2026-01-05")], date(2026, 2, 1))
        assert (room.held, room.free, room.covers) == (Decimal("10"), Decimal("10"), None)

    def test_before_the_first_purchase_there_is_nothing(self) -> None:
        room = sellable_on([buy("10", "2026-01-05")], date(2026, 1, 4))
        assert (room.held, room.free) == (Decimal("0"), Decimal("0"))

    def test_the_purchases_of_the_same_day_count(self) -> None:
        room = sellable_on([buy("10", "2026-01-05")], date(2026, 1, 5))
        assert room.free == Decimal("10")

    def test_a_later_sale_takes_its_shares_out_of_the_room(self) -> None:
        # Compra 10 em jan, vende 8 em mar, compra 10 em abr: em fev ha 10, mas
        # 8 deles sao os da venda de marco.
        history = [buy("10", "2026-01-05"), sell("8", "2026-03-10"), buy("10", "2026-04-01")]
        room = sellable_on(history, date(2026, 2, 5))
        assert room.held == Decimal("10")
        assert room.free == Decimal("2")
        assert room.covers == date(2026, 3, 10)

    def test_a_later_purchase_does_not_widen_the_room(self) -> None:
        history = [buy("10", "2026-01-05"), buy("10", "2026-04-01")]
        assert sellable_on(history, date(2026, 2, 5)).free == Decimal("10")


class TestFirstUncovered:
    def test_a_consistent_history_has_none(self) -> None:
        assert first_uncovered([buy("10", "2026-01-05"), sell("10", "2026-03-10")]) is None

    def test_the_first_day_below_zero_and_what_is_missing(self) -> None:
        history = [sell("5", "2026-01-02"), buy("10", "2026-01-05"), sell("8", "2026-03-10")]
        assert first_uncovered(history) == (date(2026, 1, 2), Decimal("5"))
