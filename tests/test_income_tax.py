from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bogle.domain.assets import Asset, AssetType
from bogle.domain.cost_basis import acquisition_cost
from bogle.domain.errors import ValidationError
from bogle.domain.transactions import Transaction, TransactionType
from bogle.tax import income_tax
from bogle.tax.income_tax import (
    income_tax_on_dividend,
    income_tax_on_fii_rendimento,
    income_tax_on_fixed_income,
    income_tax_on_jcp,
    income_tax_on_sale,
)

D = datetime(2026, 1, 10, tzinfo=UTC)
PURCHASE = datetime(2026, 1, 1, tzinfo=UTC)


def asset(asset_type: AssetType) -> Asset:
    return Asset(ticker="X", target_weight=Decimal("0.1"), asset_type=asset_type)


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


class TestSaleVariableIncome:
    def test_stock_below_monthly_limit_is_exempt(self) -> None:
        # Venda de R$ 19.999 em acoes -> isento, mesmo com lucro.
        tax = income_tax_on_sale(asset(AssetType.STOCK), sell("100", "200"), Decimal("1000"), Decimal("19999"))
        assert tax == Decimal("0")

    def test_stock_at_limit_is_exempt(self) -> None:
        tax = income_tax_on_sale(asset(AssetType.STOCK), sell("100", "200"), Decimal("1000"), Decimal("20000"))
        assert tax == Decimal("0")

    def test_stock_above_monthly_limit_taxes_full_gain(self) -> None:
        # Venda de R$ 20.001 no mes -> tributa todo o lucro a 15%.
        # gain = 20001 - 0 - 18001 = 2000 ; 2000 * 0.15 = 300
        tax = income_tax_on_sale(asset(AssetType.STOCK), sell("1", "20001"), Decimal("18001"), Decimal("20001"))
        assert tax == Decimal("300")

    def test_bdr_has_no_value_exemption(self) -> None:
        # BDR tributa mesmo com total mensal baixo (isencao e so para acoes).
        # gain = 5000 - 4000 = 1000 ; 15%
        tax = income_tax_on_sale(asset(AssetType.BDR), sell("100", "50"), Decimal("4000"), Decimal("1000"))
        assert tax == Decimal("150")

    def test_etf_variable_income_15_percent_no_exemption(self) -> None:
        tax = income_tax_on_sale(asset(AssetType.ETF), sell("100", "50"), Decimal("4000"), Decimal("1000"))
        assert tax == Decimal("150")

    def test_fii_capital_gain_20_percent_no_exemption(self) -> None:
        # gain = 5000 - 4000 = 1000 ; 20%
        tax = income_tax_on_sale(asset(AssetType.FII), sell("100", "50"), Decimal("4000"), Decimal("0"))
        assert tax == Decimal("200")

    def test_loss_owes_nothing(self) -> None:
        tax = income_tax_on_sale(asset(AssetType.STOCK), sell("100", "20"), Decimal("3000"), Decimal("50000"))
        assert tax == Decimal("0")

    def test_sale_fees_reduce_the_gain(self) -> None:
        # gain = 5000 - 10 (fees) - 4000 = 990 ; 15%
        tax = income_tax_on_sale(asset(AssetType.BDR), sell("100", "50", fees="10"), Decimal("4000"), Decimal("0"))
        assert tax == Decimal("148.5")

    def test_partial_sale_uses_weighted_average_cost(self) -> None:
        # Integracao com a 4.5: compras 100@30 e 50@36 -> preco medio 32.
        history = [buy("100", "30"), buy("50", "36")]
        cost = acquisition_cost(history, Decimal("40"))  # 32 * 40 = 1280
        assert cost == Decimal("1280")
        s = sell("40", "50", fees="2")  # proceeds 2000, fees 2
        # gain = 2000 - 2 - 1280 = 718 ; acoes acima do limite -> 15%
        tax = income_tax_on_sale(asset(AssetType.STOCK), s, cost, Decimal("25000"))
        assert tax == Decimal("107.7")

    def test_fixed_income_asset_raises(self) -> None:
        with pytest.raises(ValidationError, match="renda variavel"):
            income_tax_on_sale(asset(AssetType.CDB), sell("1", "1000"), Decimal("900"), Decimal("0"))

    def test_result_is_decimal(self) -> None:
        tax = income_tax_on_sale(asset(AssetType.BDR), sell("100", "50"), Decimal("4000"), Decimal("0"))
        assert isinstance(tax, Decimal)


class TestProventos:
    def test_dividend_is_exempt_per_operation(self) -> None:
        assert income_tax_on_dividend(asset(AssetType.STOCK), Decimal("100000")) == Decimal("0")

    def test_jcp_15_percent_retained(self) -> None:
        assert income_tax_on_jcp(asset(AssetType.STOCK), Decimal("200")) == Decimal("30")

    def test_fii_rendimento_is_exempt(self) -> None:
        assert income_tax_on_fii_rendimento(asset(AssetType.FII), Decimal("80")) == Decimal("0")

    def test_jcp_result_is_decimal(self) -> None:
        assert isinstance(income_tax_on_jcp(asset(AssetType.STOCK), Decimal("200")), Decimal)


class TestFixedIncome:
    def test_lci_is_exempt(self) -> None:
        tax = income_tax_on_fixed_income(asset(AssetType.LCI), PURCHASE, PURCHASE + timedelta(days=90), Decimal("500"))
        assert tax == Decimal("0")

    def test_lca_is_exempt(self) -> None:
        tax = income_tax_on_fixed_income(asset(AssetType.LCA), PURCHASE, PURCHASE + timedelta(days=400), Decimal("500"))
        assert tax == Decimal("0")

    @pytest.mark.parametrize(
        ("days", "expected"),
        [
            (100, "225"),  # ate 180d -> 22,5%
            (200, "200"),  # 181-360d -> 20%
            (400, "175"),  # 361-720d -> 17,5%
            (800, "150"),  # > 720d -> 15%
        ],
    )
    def test_cdb_regressive_brackets(self, days: int, expected: str) -> None:
        tax = income_tax_on_fixed_income(
            asset(AssetType.CDB), PURCHASE, PURCHASE + timedelta(days=days), Decimal("1000")
        )
        assert tax == Decimal(expected)

    @pytest.mark.parametrize(
        ("days", "rate"),
        [
            (180, "0.225"),
            (181, "0.20"),
            (360, "0.20"),
            (361, "0.175"),
            (720, "0.175"),
            (721, "0.15"),
        ],
    )
    def test_bracket_boundaries(self, days: int, rate: str) -> None:
        tax = income_tax_on_fixed_income(
            asset(AssetType.TESOURO), PURCHASE, PURCHASE + timedelta(days=days), Decimal("1000")
        )
        assert tax == Decimal("1000") * Decimal(rate)

    def test_non_positive_income_owes_nothing(self) -> None:
        tax = income_tax_on_fixed_income(asset(AssetType.CDB), PURCHASE, PURCHASE + timedelta(days=10), Decimal("0"))
        assert tax == Decimal("0")

    def test_caixinha_treated_as_fixed_income(self) -> None:
        tax = income_tax_on_fixed_income(
            asset(AssetType.CAIXINHA), PURCHASE, PURCHASE + timedelta(days=100), Decimal("1000")
        )
        assert tax == Decimal("225")

    def test_variable_income_asset_raises(self) -> None:
        with pytest.raises(ValidationError, match="renda fixa"):
            income_tax_on_fixed_income(asset(AssetType.STOCK), PURCHASE, PURCHASE + timedelta(days=10), Decimal("10"))

    def test_result_is_decimal(self) -> None:
        tax = income_tax_on_fixed_income(
            asset(AssetType.CDB), PURCHASE, PURCHASE + timedelta(days=100), Decimal("1000")
        )
        assert isinstance(tax, Decimal)


class TestScope:
    def test_no_day_trade_path(self) -> None:
        # Day trade (20% / 1%) e informativo na 4.1 e nao tem caminho de calculo.
        assert not hasattr(income_tax, "income_tax_on_day_trade")
