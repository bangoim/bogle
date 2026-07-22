"""Tests for the aporte suggestion engine (issue #23)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from bogle.domain.assets import AssetType
from bogle.domain.errors import MissingPriceError, ValidationError
from bogle.position import PortfolioSummary, Position
from bogle.rebalancing import suggest_allocation

_ZERO = Decimal("0")


def make_position(
    ticker: str,
    price: str | None,
    value: str | None,
    target: str,
    asset_type: AssetType = AssetType.ETF,
    total: str = "0",
) -> Position:
    market_value = Decimal(value) if value is not None else None
    total_value = Decimal(total)
    current = market_value / total_value if market_value is not None and total_value > 0 else None
    return Position(
        ticker=ticker,
        asset_type=asset_type,
        quantity=Decimal("1"),
        total_invested=market_value if market_value is not None else Decimal("1"),
        target_weight=Decimal(target),
        dividends=_ZERO,
        price=Decimal(price) if price is not None else None,
        market_value=market_value,
        current_weight=current,
        drift=current - Decimal(target) if current is not None else None,
    )


def make_summary(*positions: Position) -> PortfolioSummary:
    total = sum((p.market_value for p in positions if p.market_value is not None), _ZERO)
    invested = sum((p.total_invested for p in positions), _ZERO)
    return PortfolioSummary(list(positions), total, invested, _ZERO, _ZERO)


def total_drift(summary: PortfolioSummary) -> Decimal:
    return sum(
        (abs(p.drift) for p in summary.positions if p.drift is not None),
        _ZERO,
    )


class TestIssueExample:
    """Carteira 64/36 com targets 70/30 e aporte de 10.000 (exemplo da issue)."""

    def summary(self) -> PortfolioSummary:
        return make_summary(
            make_position("VWRA11", "100", "64000", "0.70", total="100000"),
            make_position("B5P211", "90", "36000", "0.30", total="100000"),
        )

    def test_everything_goes_to_the_laggard(self) -> None:
        suggestion = suggest_allocation(self.summary(), Decimal("10000"))
        vwra = next(item for item in suggestion.items if item.ticker == "VWRA11")
        b5p2 = next(item for item in suggestion.items if item.ticker == "B5P211")
        assert vwra.quantity == Decimal("100")  # 10000 / 100
        assert vwra.effective_cost == Decimal("10000")
        assert b5p2.effective_cost == _ZERO
        assert suggestion.leftover == _ZERO

    def test_weights_move_toward_targets(self) -> None:
        suggestion = suggest_allocation(self.summary(), Decimal("10000"))
        vwra = next(item for item in suggestion.items if item.ticker == "VWRA11")
        b5p2 = next(item for item in suggestion.items if item.ticker == "B5P211")
        # 74k/110k e 36k/110k: mais perto de 70/30 do que 64/36.
        assert Decimal("0.64") < vwra.weight_after < Decimal("0.70")
        assert Decimal("0.30") < b5p2.weight_after < Decimal("0.36")

    def test_aggregate_drift_shrinks(self) -> None:
        summary = self.summary()
        suggestion = suggest_allocation(summary, Decimal("10000"))
        drift_after = sum(
            (abs(item.weight_after - item.target_weight) for item in suggestion.items),
            _ZERO,
        )
        assert drift_after < total_drift(summary)


class TestAllocationBranches:
    def test_needs_covered_leaves_leftover_in_cash(self) -> None:
        # future = 120k: A precisa de 18k, B de nada -> sobram 2k em caixa.
        summary = make_summary(
            make_position("AAAA11", "1", "30000", "0.40", total="100000"),
            make_position("BBBB11", "1", "70000", "0.30", total="100000"),
        )
        suggestion = suggest_allocation(summary, Decimal("20000"))
        aaaa = next(item for item in suggestion.items if item.ticker == "AAAA11")
        assert aaaa.effective_cost == Decimal("18000")
        assert aaaa.weight_after == Decimal("0.40")  # exatamente no target do patrimonio futuro
        assert suggestion.leftover == Decimal("2000")

    def test_proportional_when_needs_exceed_amount(self) -> None:
        # future = 120: A precisa 20, B precisa 6, total 26 > 20 -> proporcional.
        summary = make_summary(
            make_position("AAAA11", "9", "40", "0.50", total="100"),
            make_position("BBBB11", "2", "30", "0.30", total="100"),
            make_position("CCCC11", "1", "30", "0.05", total="100"),  # acima do target, nao recebe
        )
        suggestion = suggest_allocation(summary, Decimal("20"))
        aaaa = next(item for item in suggestion.items if item.ticker == "AAAA11")
        bbbb = next(item for item in suggestion.items if item.ticker == "BBBB11")
        # A: alocacao 15.38 -> 1 cota de 9; B: alocacao 4.61 -> 2 cotas de 2,
        # residual compra +1 cota de B (limite = necessidade de 6).
        assert aaaa.quantity == Decimal("1")
        assert bbbb.quantity == Decimal("3")
        assert bbbb.effective_cost == Decimal("6")
        assert bbbb.weight_after == Decimal("0.3")  # nunca passa do target
        assert suggestion.total_allocated == Decimal("15")
        assert suggestion.leftover == Decimal("5")

    def test_portfolio_at_target_stays_at_target(self) -> None:
        summary = make_summary(
            make_position("AAAA11", "1", "70000", "0.70", total="100000"),
            make_position("BBBB11", "1", "30000", "0.30", total="100000"),
        )
        suggestion = suggest_allocation(summary, Decimal("1000"))
        # Carteira nos targets: o aporte inteiro entra 70/30 e os pesos nao mudam.
        assert suggestion.total_allocated == Decimal("1000")
        assert suggestion.leftover == _ZERO
        for item in suggestion.items:
            assert item.weight_after == item.target_weight


class TestFixedIncome:
    def test_private_fixed_income_gets_exact_value_and_warning(self) -> None:
        summary = make_summary(
            make_position("CDB01", "40", "40", "0.50", asset_type=AssetType.CDB, total="100"),
            make_position("BBBB11", "10", "60", "0.50", total="100"),
        )
        suggestion = suggest_allocation(summary, Decimal("20"))
        cdb = next(item for item in suggestion.items if item.ticker == "CDB01")
        assert cdb.quantity is None
        assert cdb.effective_cost == Decimal("20.00")  # future 120 * 0.5 - 40 = 20, sem floor
        assert suggestion.leftover == _ZERO
        assert any("CDB01" in w and "novo contrato" in w for w in suggestion.warnings)

    def test_tesouro_gets_exact_value_without_warning(self) -> None:
        summary = make_summary(
            make_position("TESOURO SELIC 2029", "100", "40", "0.50", asset_type=AssetType.TESOURO, total="100"),
            make_position("BBBB11", "10", "60", "0.50", total="100"),
        )
        suggestion = suggest_allocation(summary, Decimal("20"))
        tesouro = next(item for item in suggestion.items if item.ticker == "TESOURO SELIC 2029")
        assert tesouro.quantity is None
        assert tesouro.effective_cost == Decimal("20.00")
        assert suggestion.warnings == []


class TestInvariants:
    @pytest.mark.parametrize("amount", ["10", "97", "1000", "12345.67"])
    def test_never_allocates_more_than_amount(self, amount: str) -> None:
        summary = make_summary(
            make_position("AAAA11", "7", "40", "0.50", total="100"),
            make_position("BBBB11", "13", "30", "0.30", total="100"),
            make_position("CDB01", "30", "30", "0.20", asset_type=AssetType.CDB, total="100"),
        )
        suggestion = suggest_allocation(summary, Decimal(amount))
        assert suggestion.total_allocated <= Decimal(amount)
        assert suggestion.total_allocated + suggestion.leftover == Decimal(amount)
        # Quem recebe aporte nunca passa do target; quem ja estava acima (no-sell) segue acima.
        for item in suggestion.items:
            if item.effective_cost > 0:
                assert item.weight_after <= item.target_weight + Decimal("1E-12")

    def test_items_sorted_by_cost_desc(self) -> None:
        summary = make_summary(
            make_position("AAAA11", "1", "40", "0.50", total="100"),
            make_position("BBBB11", "1", "30", "0.30", total="100"),
        )
        suggestion = suggest_allocation(summary, Decimal("20"))
        costs = [item.effective_cost for item in suggestion.items]
        assert costs == sorted(costs, reverse=True)


class TestErrors:
    def test_missing_price_aborts(self) -> None:
        summary = make_summary(
            make_position("AAAA11", "1", "50", "0.50", total="50"),
            make_position("CDB01", None, None, "0.50", asset_type=AssetType.CDB, total="50"),
        )
        with pytest.raises(MissingPriceError, match="CDB01"):
            suggest_allocation(summary, Decimal("100"))

    def test_non_positive_amount(self) -> None:
        summary = make_summary(make_position("AAAA11", "1", "50", "0.50", total="50"))
        with pytest.raises(ValidationError, match="positivo"):
            suggest_allocation(summary, Decimal("0"))

    def test_empty_portfolio(self) -> None:
        with pytest.raises(ValidationError, match="Nenhuma posicao"):
            suggest_allocation(make_summary(), Decimal("100"))
