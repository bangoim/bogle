from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from bogle.domain.cost_basis import acquisition_cost, average_cost_per_share
from bogle.domain.errors import ValidationError
from bogle.domain.transactions import Transaction, TransactionType

D = datetime(2026, 1, 10, tzinfo=UTC)


def buy(shares: str, unit_price: str, fees: str = "0") -> Transaction:
    s, p, f = Decimal(shares), Decimal(unit_price), Decimal(fees)
    investment = s * p
    return Transaction(
        id=0,
        ticker="X",
        transaction_type=TransactionType.BUY,
        date=D,
        shares=s,
        unit_price=p,
        total_investment=investment,
        fees=f,
        total_cost=investment + f,
        tax_withheld=Decimal("0"),
    )


def sell(shares: str, unit_price: str, fees: str = "0") -> Transaction:
    s, p, f = Decimal(shares), Decimal(unit_price), Decimal(fees)
    return Transaction(
        id=0,
        ticker="X",
        transaction_type=TransactionType.SELL,
        date=D,
        shares=s,
        unit_price=p,
        total_investment=s * p,
        fees=f,
        total_cost=f,
        tax_withheld=Decimal("0"),
    )


def income(transaction_type: TransactionType, amount: str) -> Transaction:
    return Transaction(
        id=0,
        ticker="X",
        transaction_type=transaction_type,
        date=D,
        shares=Decimal("0"),
        unit_price=Decimal("0"),
        total_investment=Decimal(amount),
        fees=Decimal("0"),
        total_cost=Decimal("0"),
        tax_withheld=Decimal("0"),
    )


class TestAverageCostPerShare:
    def test_weighted_average_of_buys_at_different_prices(self) -> None:
        # (100*30 + 50*36) / 150 = 4800 / 150 = 32
        avg = average_cost_per_share([buy("100", "30"), buy("50", "36")])
        assert avg == Decimal("32")

    def test_purchase_fees_compose_the_cost(self) -> None:
        # (3000 + 5.5) / 100 = 30.055
        avg = average_cost_per_share([buy("100", "30", fees="5.5")])
        assert avg == Decimal("30.055")

    def test_sales_do_not_change_the_average(self) -> None:
        history = [buy("100", "30"), buy("50", "36"), sell("40", "50", fees="2")]
        assert average_cost_per_share(history) == Decimal("32")

    def test_income_events_are_ignored(self) -> None:
        history = [
            buy("100", "30"),
            income(TransactionType.DIVIDEND, "123.45"),
            income(TransactionType.JCP, "20"),
        ]
        assert average_cost_per_share(history) == Decimal("30")

    def test_fixed_income_single_unit_convention(self) -> None:
        # BUY shares=1, unit_price = amount applied -> avg = total_cost of the unit.
        avg = average_cost_per_share([buy("1", "1000", fees="0")])
        assert avg == Decimal("1000")

    def test_result_is_decimal(self) -> None:
        avg = average_cost_per_share([buy("3", "10")])
        assert isinstance(avg, Decimal)

    def test_no_purchases_raises(self) -> None:
        with pytest.raises(ValidationError, match="Sem compras registradas"):
            average_cost_per_share([sell("10", "50")])

    def test_empty_history_raises(self) -> None:
        with pytest.raises(ValidationError, match="Sem compras registradas"):
            average_cost_per_share([])


class TestAcquisitionCost:
    def test_partial_sale_carries_proportional_cost(self) -> None:
        history = [buy("100", "30"), buy("50", "36")]  # avg 32
        assert acquisition_cost(history, Decimal("40")) == Decimal("1280")  # 32 * 40

    def test_full_position_cost(self) -> None:
        history = [buy("100", "30"), buy("50", "36")]  # avg 32, 150 shares
        assert acquisition_cost(history, Decimal("150")) == Decimal("4800")

    def test_fixed_income_redemption_cost(self) -> None:
        # Resgate de renda fixa: SELL shares=1 -> custo = total_cost do BUY.
        assert acquisition_cost([buy("1", "1000")], Decimal("1")) == Decimal("1000")

    def test_result_is_decimal(self) -> None:
        assert isinstance(acquisition_cost([buy("3", "10")], Decimal("1")), Decimal)
