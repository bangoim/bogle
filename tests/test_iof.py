from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bogle.domain.assets import AssetType
from bogle.tax.iof import IOF_RATES, iof_on_redemption

PURCHASE = datetime(2026, 1, 1, tzinfo=UTC)

# Independent copy of the official table (Decreto 6.306/2007) to cross-check the
# module's IOF_RATES — a typo in one will not be mirrored in the other.
EXPECTED_RATES: dict[int, str] = {
    1: "0.96", 2: "0.93", 3: "0.90", 4: "0.86", 5: "0.83", 6: "0.80",
    7: "0.76", 8: "0.73", 9: "0.70", 10: "0.66", 11: "0.63", 12: "0.60",
    13: "0.56", 14: "0.53", 15: "0.50", 16: "0.46", 17: "0.43", 18: "0.40",
    19: "0.36", 20: "0.33", 21: "0.30", 22: "0.26", 23: "0.23", 24: "0.20",
    25: "0.16", 26: "0.13", 27: "0.10", 28: "0.06", 29: "0.03", 30: "0.00",
}  # fmt: skip


def redeemed_on_day(day: int, income: str = "1000", asset_type: AssetType = AssetType.CDB) -> Decimal:
    return iof_on_redemption(PURCHASE, PURCHASE + timedelta(days=day), Decimal(income), asset_type)


class TestRegressiveBands:
    @pytest.mark.parametrize("day", list(range(1, 31)))
    def test_all_thirty_bands(self, day: int) -> None:
        expected = Decimal("1000") * Decimal(EXPECTED_RATES[day])
        assert redeemed_on_day(day) == expected

    def test_day_1_is_96_percent(self) -> None:
        assert redeemed_on_day(1) == Decimal("960")

    def test_day_29_is_3_percent(self) -> None:
        assert redeemed_on_day(29) == Decimal("30")

    def test_day_30_is_zero(self) -> None:
        assert redeemed_on_day(30) == Decimal("0")


class TestEdges:
    def test_same_day_is_zero(self) -> None:
        assert redeemed_on_day(0) == Decimal("0")

    def test_redemption_before_purchase_is_zero(self) -> None:
        assert iof_on_redemption(PURCHASE, PURCHASE - timedelta(days=5), Decimal("1000"), AssetType.CDB) == Decimal("0")

    @pytest.mark.parametrize("day", [31, 45, 365])
    def test_after_thirty_days_is_zero(self, day: int) -> None:
        assert redeemed_on_day(day) == Decimal("0")

    def test_non_positive_income_is_zero(self) -> None:
        assert redeemed_on_day(5, income="0") == Decimal("0")


class TestVariableIncomeExempt:
    @pytest.mark.parametrize("asset_type", [AssetType.STOCK, AssetType.BDR, AssetType.FII, AssetType.ETF])
    def test_variable_income_always_zero(self, asset_type: AssetType) -> None:
        # Mesmo no dia 1, renda variavel nao tem IOF.
        assert redeemed_on_day(1, asset_type=asset_type) == Decimal("0")


class TestFixedIncomeSubject:
    @pytest.mark.parametrize(
        "asset_type",
        [AssetType.CDB, AssetType.RDB, AssetType.LCI, AssetType.LCA, AssetType.TESOURO, AssetType.CAIXINHA],
    )
    def test_fixed_income_pays_within_window(self, asset_type: AssetType) -> None:
        assert redeemed_on_day(1, asset_type=asset_type) == Decimal("960")


class TestTableIntegrity:
    def test_module_table_matches_official_rates(self) -> None:
        assert {day: str(rate) for day, rate in IOF_RATES.items()} == EXPECTED_RATES

    def test_rates_are_decimal(self) -> None:
        assert all(isinstance(rate, Decimal) for rate in IOF_RATES.values())

    def test_no_float_imprecision(self) -> None:
        # Decimal("0.96") != Decimal(0.96): a tabela deve usar strings.
        assert IOF_RATES[1] == Decimal("0.96")

    def test_result_is_decimal(self) -> None:
        assert isinstance(redeemed_on_day(5), Decimal)
