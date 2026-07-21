"""Tests for the fixed-income present-value engine. Hand-calculated expectations."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from bogle.analytics.business_days import business_days_between
from bogle.data.fixed_income import present_value
from bogle.data.models import SeriesPoint
from bogle.domain.assets import Indexer

_252 = Decimal(252)


def sp(day: date, value: str) -> SeriesPoint:
    return SeriesPoint(day, Decimal(value))


def approx(value: Decimal, expected: Decimal, tol: str = "1e-9") -> bool:
    return abs(value - expected) < Decimal(tol)


# Three consecutive business days with a 0.04%/day rate.
CDI_3D = [sp(date(2026, 1, 5), "0.0004"), sp(date(2026, 1, 6), "0.0004"), sp(date(2026, 1, 7), "0.0004")]


class TestPrefixado:
    def test_252_business_day_convention(self) -> None:
        # du([Jan 5, Jan 9)) = 4 => 1000 * 1.12 ** (4/252)
        pv = present_value(
            Decimal("1000"), indexer=None, rate=Decimal("0.12"), is_prefixed=True,
            purchase_date=date(2026, 1, 5), on_date=date(2026, 1, 9),
        )  # fmt: skip
        expected = Decimal("1000") * (Decimal("1.12") ** (Decimal(4) / _252))
        assert approx(pv, expected)

    def test_zero_business_days_no_growth(self) -> None:
        # Saturday -> Sunday: no business days elapsed.
        pv = present_value(
            Decimal("1000"), indexer=None, rate=Decimal("0.12"), is_prefixed=True,
            purchase_date=date(2026, 1, 3), on_date=date(2026, 1, 4),
        )  # fmt: skip
        assert pv == Decimal("1000")


class TestPercentCdi:
    def test_multiplier_applied_over_daily_series(self) -> None:
        # 110% CDI over 3 days: 1000 * (1 + 1.10*0.0004)^3
        pv = present_value(
            Decimal("1000"), indexer=Indexer.CDI, rate=Decimal("1.10"), is_prefixed=False,
            purchase_date=date(2026, 1, 5), on_date=date(2026, 1, 8), cdi=CDI_3D,
        )  # fmt: skip
        assert approx(pv, Decimal("1000") * (Decimal("1.00044") ** 3))

    def test_100_percent_matches_index(self) -> None:
        pv = present_value(
            Decimal("1000"), indexer=Indexer.CDI, rate=Decimal("1.00"), is_prefixed=False,
            purchase_date=date(2026, 1, 5), on_date=date(2026, 1, 8), cdi=CDI_3D,
        )  # fmt: skip
        assert approx(pv, Decimal("1000") * (Decimal("1.0004") ** 3))

    def test_only_points_inside_window_count(self) -> None:
        # A point on the valuation day (Jan 8) is excluded (half-open end).
        series = [*CDI_3D, sp(date(2026, 1, 8), "0.0004")]
        pv = present_value(
            Decimal("1000"), indexer=Indexer.CDI, rate=Decimal("1.00"), is_prefixed=False,
            purchase_date=date(2026, 1, 5), on_date=date(2026, 1, 8), cdi=series,
        )  # fmt: skip
        assert approx(pv, Decimal("1000") * (Decimal("1.0004") ** 3))

    def test_empty_series_raises(self) -> None:
        with pytest.raises(ValueError, match="CDI"):
            present_value(
                Decimal("1000"), indexer=Indexer.CDI, rate=Decimal("1.10"), is_prefixed=False,
                purchase_date=date(2026, 1, 5), on_date=date(2026, 1, 8), cdi=[],
            )  # fmt: skip


class TestSelic:
    def test_uses_selic_series(self) -> None:
        selic = [sp(date(2026, 1, 5), "0.0005"), sp(date(2026, 1, 6), "0.0005")]
        pv = present_value(
            Decimal("1000"), indexer=Indexer.SELIC, rate=Decimal("1.00"), is_prefixed=False,
            purchase_date=date(2026, 1, 5), on_date=date(2026, 1, 7), selic=selic,
        )  # fmt: skip
        assert approx(pv, Decimal("1000") * (Decimal("1.0005") ** 2))


class TestCdiPlus:
    def test_cdi_accumulation_times_spread(self) -> None:
        # du([Jan 5, Jan 8)) = 3; 1000 * (1.0004)^3 * 1.02 ** (3/252)
        pv = present_value(
            Decimal("1000"), indexer=Indexer.CDI_PLUS, rate=Decimal("0.02"), is_prefixed=False,
            purchase_date=date(2026, 1, 5), on_date=date(2026, 1, 8), cdi=CDI_3D,
        )  # fmt: skip
        cdi_factor = Decimal("1.0004") ** 3
        spread = Decimal("1.02") ** (Decimal(3) / _252)
        assert approx(pv, Decimal("1000") * cdi_factor * spread)


class TestIpcaPlus:
    def test_full_months_with_zero_real_rate(self) -> None:
        # Jan 0.5% + Feb 0.4%, both full => 1000 * 1.005 * 1.004
        ipca = [sp(date(2026, 1, 1), "0.005"), sp(date(2026, 2, 1), "0.004")]
        pv = present_value(
            Decimal("1000"), indexer=Indexer.IPCA_PLUS, rate=Decimal("0"), is_prefixed=False,
            purchase_date=date(2026, 1, 1), on_date=date(2026, 3, 1), ipca=ipca,
        )  # fmt: skip
        assert approx(pv, Decimal("1000") * Decimal("1.005") * Decimal("1.004"))

    def test_real_rate_leg_applied(self) -> None:
        ipca = [sp(date(2026, 1, 1), "0.005"), sp(date(2026, 2, 1), "0.004")]
        pv = present_value(
            Decimal("1000"), indexer=Indexer.IPCA_PLUS, rate=Decimal("0.06"), is_prefixed=False,
            purchase_date=date(2026, 1, 1), on_date=date(2026, 3, 1), ipca=ipca,
        )  # fmt: skip
        du = business_days_between(date(2026, 1, 1), date(2026, 3, 1))
        expected = Decimal("1000") * Decimal("1.005") * Decimal("1.004") * (Decimal("1.06") ** (Decimal(du) / _252))
        assert approx(pv, expected)

    def test_partial_month_prorata(self) -> None:
        ipca = [sp(date(2026, 1, 1), "0.006")]
        pv = present_value(
            Decimal("1000"), indexer=Indexer.IPCA_PLUS, rate=Decimal("0"), is_prefixed=False,
            purchase_date=date(2026, 1, 15), on_date=date(2026, 1, 22), ipca=ipca,
        )  # fmt: skip
        du_window = business_days_between(date(2026, 1, 15), date(2026, 1, 22))
        du_month = business_days_between(date(2026, 1, 1), date(2026, 2, 1))
        expected = Decimal("1000") * (Decimal("1.006") ** (Decimal(du_window) / Decimal(du_month)))
        assert approx(pv, expected)

    def test_unpublished_month_uses_latest_as_projection(self) -> None:
        # Only January is published; a February window falls back to Jan's value.
        ipca = [sp(date(2026, 1, 1), "0.006")]
        pv = present_value(
            Decimal("1000"), indexer=Indexer.IPCA_PLUS, rate=Decimal("0"), is_prefixed=False,
            purchase_date=date(2026, 2, 1), on_date=date(2026, 3, 1), ipca=ipca,
        )  # fmt: skip
        assert approx(pv, Decimal("1000") * Decimal("1.006"))

    def test_empty_series_raises(self) -> None:
        with pytest.raises(ValueError, match="IPCA"):
            present_value(
                Decimal("1000"), indexer=Indexer.IPCA_PLUS, rate=Decimal("0.06"), is_prefixed=False,
                purchase_date=date(2026, 1, 1), on_date=date(2026, 3, 1), ipca=[],
            )  # fmt: skip


class TestGuards:
    def test_on_date_before_purchase_returns_principal(self) -> None:
        pv = present_value(
            Decimal("777"), indexer=Indexer.CDI, rate=Decimal("1.10"), is_prefixed=False,
            purchase_date=date(2026, 1, 10), on_date=date(2026, 1, 5), cdi=CDI_3D,
        )  # fmt: skip
        assert pv == Decimal("777")

    def test_on_date_equals_purchase_returns_principal(self) -> None:
        pv = present_value(
            Decimal("777"), indexer=Indexer.CDI, rate=Decimal("1.10"), is_prefixed=False,
            purchase_date=date(2026, 1, 5), on_date=date(2026, 1, 5), cdi=CDI_3D,
        )  # fmt: skip
        assert pv == Decimal("777")

    def test_unsupported_indexer_raises(self) -> None:
        with pytest.raises(ValueError, match="indexer"):
            present_value(
                Decimal("1000"), indexer=None, rate=Decimal("0.1"), is_prefixed=False,
                purchase_date=date(2026, 1, 1), on_date=date(2026, 2, 1),
            )  # fmt: skip
