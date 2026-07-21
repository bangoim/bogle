"""Tests for the Brazilian business-day calendar."""

from __future__ import annotations

from datetime import date

import pytest

from bogle.analytics.business_days import (
    _easter_sunday,
    business_days_between,
    is_business_day,
)


class TestEasterComputus:
    @pytest.mark.parametrize(
        ("year", "expected"),
        [(2024, date(2024, 3, 31)), (2025, date(2025, 4, 20)), (2026, date(2026, 4, 5))],
    )
    def test_known_easter_dates(self, year: int, expected: date) -> None:
        assert _easter_sunday(year) == expected


class TestHolidays:
    @pytest.mark.parametrize(
        "day",
        [
            date(2026, 1, 1),  # Confraternizacao
            date(2026, 4, 21),  # Tiradentes
            date(2026, 5, 1),  # Trabalho
            date(2026, 9, 7),  # Independencia
            date(2026, 10, 12),  # Aparecida
            date(2026, 11, 2),  # Finados
            date(2026, 11, 15),  # Republica
            date(2026, 12, 25),  # Natal
        ],
    )
    def test_fixed_national_holidays(self, day: date) -> None:
        assert not is_business_day(day)

    @pytest.mark.parametrize(
        "day",
        [
            date(2026, 4, 3),  # Sexta-feira Santa (Easter 2026 = Apr 5)
            date(2026, 2, 16),  # Carnaval segunda
            date(2026, 2, 17),  # Carnaval terca
            date(2026, 6, 4),  # Corpus Christi
        ],
    )
    def test_moving_holidays(self, day: date) -> None:
        assert not is_business_day(day)

    def test_consciencia_negra_is_national_from_2024(self) -> None:
        assert not is_business_day(date(2024, 11, 20))
        assert not is_business_day(date(2026, 11, 20))
        assert is_business_day(date(2023, 11, 20))  # a Monday, not yet a national holiday

    def test_weekends_are_not_business_days(self) -> None:
        assert not is_business_day(date(2026, 1, 3))  # Saturday
        assert not is_business_day(date(2026, 1, 4))  # Sunday

    def test_ordinary_weekday_is_a_business_day(self) -> None:
        assert is_business_day(date(2026, 1, 2))  # Friday, no holiday


class TestBusinessDaysBetween:
    def test_half_open_interval(self) -> None:
        # [Jan 1, Jan 9) 2026: 1 Thu(holiday),2,3 Sat,4 Sun,5,6,7,8 -> {2,5,6,7,8} = 5
        assert business_days_between(date(2026, 1, 1), date(2026, 1, 9)) == 5

    def test_full_calendar_week(self) -> None:
        # [Mon Jan 5, Mon Jan 12): 5,6,7,8,9 business; 10,11 weekend -> 5
        assert business_days_between(date(2026, 1, 5), date(2026, 1, 12)) == 5

    def test_same_day_is_zero(self) -> None:
        assert business_days_between(date(2026, 1, 5), date(2026, 1, 5)) == 0

    def test_end_before_start_is_zero(self) -> None:
        assert business_days_between(date(2026, 1, 9), date(2026, 1, 5)) == 0
