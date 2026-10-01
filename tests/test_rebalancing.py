"""Tests for the Buy-vs-Hold classifier and the evaluation cycle (issues #22/#24)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from bogle.domain.assets import AssetType
from bogle.domain.errors import MissingPriceError
from bogle.position import Position
from bogle.rebalancing import Recommendation, classify_positions, next_evaluation_date, overdue_notice


def make_position(ticker: str, current: str | None, target: str) -> Position:
    current_weight = Decimal(current) if current is not None else None
    target_weight = Decimal(target)
    return Position(
        ticker=ticker,
        asset_type=AssetType.ETF,
        quantity=Decimal("10"),
        total_invested=Decimal("1000"),
        target_weight=target_weight,
        dividends=Decimal("0"),
        price=Decimal("100") if current is not None else None,
        market_value=Decimal("1000") if current is not None else None,
        current_weight=current_weight,
        drift=current_weight - target_weight if current_weight is not None else None,
    )


class TestClassification:
    def test_below_target_beyond_threshold_is_buy(self) -> None:
        [rec] = classify_positions([make_position("VWRA11", "0.64", "0.70")])
        assert rec.recommendation is Recommendation.BUY
        assert rec.drift == Decimal("-0.06")
        assert rec.reason == "Peso atual 64% esta 6 p.p. abaixo do target de 70%."

    def test_below_target_within_threshold_is_hold(self) -> None:
        [rec] = classify_positions([make_position("VWRA11", "0.67", "0.70")])
        assert rec.recommendation is Recommendation.HOLD
        assert "dentro da tolerancia" in rec.reason

    def test_at_target_is_hold(self) -> None:
        [rec] = classify_positions([make_position("VWRA11", "0.70", "0.70")])
        assert rec.recommendation is Recommendation.HOLD
        assert "no target" in rec.reason

    def test_above_target_is_hold_never_sell(self) -> None:
        [rec] = classify_positions([make_position("VWRA11", "0.80", "0.70")])
        assert rec.recommendation is Recommendation.HOLD
        assert "no-sell" in rec.reason

    def test_boundary_drift_equal_to_threshold_is_hold(self) -> None:
        # Desigualdade estrita: exatamente 5 p.p. abaixo ainda e HOLD.
        [rec] = classify_positions([make_position("VWRA11", "0.65", "0.70")])
        assert rec.recommendation is Recommendation.HOLD

    def test_just_beyond_boundary_is_buy(self) -> None:
        [rec] = classify_positions([make_position("VWRA11", "0.6499", "0.70")])
        assert rec.recommendation is Recommendation.BUY

    def test_custom_threshold(self) -> None:
        [rec] = classify_positions([make_position("VWRA11", "0.67", "0.70")], threshold=Decimal("0.02"))
        assert rec.recommendation is Recommendation.BUY

    def test_preserves_input_order(self) -> None:
        recs = classify_positions([make_position("B5P211", "0.36", "0.30"), make_position("VWRA11", "0.64", "0.70")])
        assert [r.ticker for r in recs] == ["B5P211", "VWRA11"]
        assert [r.recommendation for r in recs] == [Recommendation.HOLD, Recommendation.BUY]


class TestMissingPrice:
    def test_unpriced_position_raises_with_tickers(self) -> None:
        positions = [
            make_position("VWRA11", "0.64", "0.70"),
            make_position("CDB01", None, "0.20"),
            make_position("LCA02", None, "0.10"),
        ]
        with pytest.raises(MissingPriceError, match="CDB01, LCA02"):
            classify_positions(positions)

    def test_empty_portfolio_returns_empty(self) -> None:
        assert classify_positions([]) == []


class TestReasonFormatting:
    def test_fractional_percentages_keep_one_decimal(self) -> None:
        [rec] = classify_positions([make_position("VWRA11", "0.6375", "0.70")])
        assert rec.reason == "Peso atual 63.8% esta 6.3 p.p. abaixo do target de 70%."


class TestNextEvaluationDate:
    def test_six_months(self) -> None:
        assert next_evaluation_date(date(2026, 1, 15), 6) == date(2026, 7, 15)

    def test_twelve_months(self) -> None:
        assert next_evaluation_date(date(2026, 7, 1), 12) == date(2027, 7, 1)

    def test_clamps_to_month_end(self) -> None:
        assert next_evaluation_date(date(2026, 8, 31), 6) == date(2027, 2, 28)

    def test_leap_day(self) -> None:
        assert next_evaluation_date(date(2024, 2, 29), 12) == date(2025, 2, 28)


class TestOverdueNotice:
    def test_never_evaluated_has_nothing_to_remind(self) -> None:
        assert overdue_notice(None, 12, today=date(2026, 8, 12)) is None

    def test_inside_the_cycle_is_silent(self) -> None:
        assert overdue_notice(date(2026, 7, 1), 12, today=date(2026, 8, 12)) is None

    def test_completed_cycle_names_the_date_and_the_next_step(self) -> None:
        notice = overdue_notice(date(2025, 7, 1), 12, today=date(2026, 8, 12))
        assert notice == (
            "ciclo de rebalanceamento de 12 meses vencido desde 2026-07-01. "
            "Rode 'bogle suggest' para avaliar a carteira."
        )

    def test_due_exactly_today_already_counts(self) -> None:
        assert overdue_notice(date(2026, 2, 12), 6, today=date(2026, 8, 12)) is not None
