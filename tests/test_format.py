"""Tests for the shared formatters (issue #73).

Pins the exact rendering the CLI (and now the TUI) depends on: two decimals for
money, ``-`` for absent values, no scientific notation and the green/red sign
convention.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from bogle.format import (
    DASH,
    exact,
    exact_or_none,
    money,
    pct,
    sign_color,
    signed,
    signed_money,
    signed_pct,
)


class TestMoney:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (Decimal("1234.5"), "1234.50"),
            (Decimal("0"), "0.00"),
            (Decimal("-3.456"), "-3.46"),  # arredonda, nao trunca
            (Decimal("100.00000000"), "100.00"),
        ],
    )
    def test_two_decimals(self, value: Decimal, expected: str) -> None:
        assert money(value) == expected

    def test_none_is_dash(self) -> None:
        assert money(None) == DASH == "-"

    def test_signed_always_carries_the_sign(self) -> None:
        assert signed_money(Decimal("1234.5")) == "+1234.50"
        assert signed_money(Decimal("0")) == "+0.00"
        assert signed_money(Decimal("-1")) == "-1.00"
        assert signed_money(None) == DASH


class TestPercent:
    def test_fraction_becomes_percentage(self) -> None:
        assert pct(Decimal("0.1234")) == "12.34%"
        assert pct(Decimal("1")) == "100.00%"

    def test_signed(self) -> None:
        assert signed_pct(Decimal("0.1234")) == "+12.34%"
        assert signed_pct(Decimal("-0.0264")) == "-2.64%"

    def test_none_is_dash(self) -> None:
        assert pct(None) == DASH
        assert signed_pct(None) == DASH


class TestExact:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (Decimal("10.00000000"), "10"),  # escala crua do NUMERIC
            (Decimal("0E+4"), "0"),  # nunca notacao cientifica
            (Decimal("3055.2000"), "3055.2"),
            (Decimal("0.5"), "0.5"),
        ],
    )
    def test_normalizes_without_scientific_notation(self, value: Decimal, expected: str) -> None:
        assert exact(value) == expected

    def test_none_is_dash_but_json_keeps_null(self) -> None:
        assert exact(None) == DASH
        assert exact_or_none(None) is None
        assert exact_or_none(Decimal("10.00")) == "10"


class TestSigned:
    def test_gain_is_green_and_loss_is_red(self) -> None:
        assert signed(Decimal("20"), percent=False) == "[green]+20.00[/green]"
        assert signed(Decimal("-20"), percent=False) == "[red]-20.00[/red]"

    def test_zero_counts_as_a_gain(self) -> None:
        assert signed(Decimal("0"), percent=True) == "[green]+0.00%[/green]"
        assert sign_color(Decimal("0")) == "green"

    def test_percent_switches_the_body(self) -> None:
        assert signed(Decimal("0.1275"), percent=True) == "[green]+12.75%[/green]"

    def test_none_is_an_uncolored_dash(self) -> None:
        assert signed(None, percent=True) == DASH
        assert signed(None, percent=False) == DASH
