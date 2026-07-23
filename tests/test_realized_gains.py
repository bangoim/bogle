"""Tests for the sequential cost-basis replay (issue #68)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from bogle.domain.cost_basis import average_cost_per_share, replay_cost_basis
from bogle.domain.errors import ValidationError
from bogle.domain.transactions import Transaction, TransactionType

_ID = iter(range(1, 10_000))


def trade(
    kind: TransactionType, shares: str, unit_price: str, on: str = "2026-01-10", fees: str = "0", ticker: str = "X"
) -> Transaction:
    s, p, f = Decimal(shares), Decimal(unit_price), Decimal(fees)
    investment = s * p
    return Transaction(
        id=next(_ID),
        ticker=ticker,
        transaction_type=kind,
        date=datetime.fromisoformat(on).replace(tzinfo=UTC),
        shares=s,
        unit_price=p,
        total_investment=investment,
        fees=f,
        total_cost=investment + f if kind is TransactionType.BUY else f,
        tax_withheld=Decimal("0"),
    )


def buy(shares: str, price: str, on: str = "2026-01-10", **kwargs: str) -> Transaction:
    return trade(TransactionType.BUY, shares, price, on, **kwargs)


def sell(shares: str, price: str, on: str = "2026-01-10", **kwargs: str) -> Transaction:
    return trade(TransactionType.SELL, shares, price, on, **kwargs)


class TestReplay:
    def test_rfb_divergence_case_buy_after_sell(self) -> None:
        # buy 10@10 -> sell 5 -> buy 5@20: RFB = (5*10 + 100) / 10 = 15,00.
        # A formula agregada da 4.5 daria (100+100)/15 = 13,33.
        history = [
            buy("10", "10", "2026-01-05"),
            sell("5", "12", "2026-02-01"),
            buy("5", "20", "2026-03-01"),
        ]
        states, _ = replay_cost_basis(history)
        assert states["X"].average_cost == Decimal("15")
        assert states["X"].remaining_shares == Decimal("10")
        assert average_cost_per_share(history) == Decimal("15")

    def test_partial_sale_keeps_average(self) -> None:
        states, sales = replay_cost_basis([buy("100", "30", "2026-01-05"), sell("40", "50", "2026-02-01")])
        assert states["X"].average_cost == Decimal("30")
        assert states["X"].remaining_shares == Decimal("60")
        [sale] = sales
        assert sale.cost_basis == Decimal("1200")  # 30 * 40
        assert sale.gain == Decimal("800")  # 2000 - 1200

    def test_sale_fees_reduce_the_gain(self) -> None:
        _, [sale] = replay_cost_basis([buy("10", "10", "2026-01-05"), sell("10", "20", "2026-02-01", fees="7")])
        assert sale.gain == Decimal("93")  # 200 - 7 - 100

    def test_full_sale_then_rebuy_resets_average(self) -> None:
        history = [
            buy("10", "10", "2026-01-05"),
            sell("10", "15", "2026-02-01"),
            buy("10", "30", "2026-03-01"),
        ]
        states, _ = replay_cost_basis(history)
        assert states["X"].average_cost == Decimal("30")

    def test_oversell_is_loud(self) -> None:
        with pytest.raises(ValidationError, match="sem quantidade suficiente"):
            replay_cost_basis([buy("5", "10", "2026-01-05"), sell("10", "10", "2026-02-01")])
        with pytest.raises(ValidationError, match="sem quantidade suficiente"):
            replay_cost_basis([sell("10", "10")])

    def test_multiple_tickers_are_independent(self) -> None:
        history = [
            buy("10", "10", "2026-01-05", ticker="AAAA11"),
            buy("10", "50", "2026-01-05", ticker="BBBB11"),
            sell("5", "20", "2026-02-01", ticker="AAAA11"),
        ]
        states, sales = replay_cost_basis(history)
        assert states["AAAA11"].average_cost == Decimal("10")
        assert states["BBBB11"].remaining_shares == Decimal("10")
        assert [s.ticker for s in sales] == ["AAAA11"]

    def test_income_events_are_ignored(self) -> None:
        income = Transaction(
            id=next(_ID), ticker="X", transaction_type=TransactionType.DIVIDEND,
            date=datetime(2026, 2, 1, tzinfo=UTC), shares=Decimal("0"), unit_price=Decimal("0"),
            total_investment=Decimal("50"), fees=Decimal("0"), total_cost=Decimal("0"),
            tax_withheld=Decimal("0"),
        )  # fmt: skip
        states, sales = replay_cost_basis([buy("10", "10", "2026-01-05"), income])
        assert states["X"].average_cost == Decimal("10")
        assert sales == []
