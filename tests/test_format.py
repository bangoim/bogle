"""Tests for the shared number format (issues #73/#74).

Pins the exact rendering both frontends depend on — two decimals for money,
grouped thousands, ``-`` for absent values, the green/red sign convention — and
the reverse direction: which strings the user is allowed to type.

The separators are process state (``configure``), reset per test by an autouse
fixture in ``conftest.py``.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from bogle.format import (
    AMBIGUOUS,
    CANONICAL_DECIMAL,
    DASH,
    MISPLACED,
    configure,
    exact,
    exact_or_none,
    money,
    pct,
    read_number,
    sample,
    separators,
    separators_for,
    sign_color,
    signed,
    signed_money,
    signed_pct,
)


@pytest.fixture
def comma() -> None:
    """Decimal comma, thousands dot (``1.234,56``)."""
    configure(",")


class TestSeparators:
    def test_the_chosen_decimal_defines_the_thousands(self) -> None:
        assert separators_for(".") == separators_for(CANONICAL_DECIMAL)
        assert (separators_for(".").decimal, separators_for(".").thousands) == (".", ",")
        assert (separators_for(",").decimal, separators_for(",").thousands) == (",", ".")

    def test_default_is_canonical(self) -> None:
        assert separators().is_canonical
        assert sample() == "1,234.56"

    def test_configure_switches_the_pair(self, comma: None) -> None:
        assert not separators().is_canonical
        assert sample() == "1.234,56"


class TestMoney:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (Decimal("1234.5"), "1,234.50"),
            (Decimal("0"), "0.00"),
            (Decimal("-3.456"), "-3.46"),  # arredonda, nao trunca
            (Decimal("100.00000000"), "100.00"),
            (Decimal("12772.9"), "12,772.90"),
            (Decimal("1234567.891"), "1,234,567.89"),
        ],
    )
    def test_two_decimals_and_grouped_thousands(self, value: Decimal, expected: str) -> None:
        assert money(value) == expected

    def test_none_is_dash(self) -> None:
        assert money(None) == DASH == "-"

    def test_signed_always_carries_the_sign(self) -> None:
        assert signed_money(Decimal("1234.5")) == "+1,234.50"
        assert signed_money(Decimal("0")) == "+0.00"
        assert signed_money(Decimal("-1")) == "-1.00"
        assert signed_money(None) == DASH

    def test_comma_configuration(self, comma: None) -> None:
        assert money(Decimal("12772.9")) == "12.772,90"
        assert signed_money(Decimal("-1234.5")) == "-1.234,50"


class TestPercent:
    def test_fraction_becomes_percentage(self) -> None:
        assert pct(Decimal("0.1234")) == "12.34%"
        assert pct(Decimal("1")) == "100.00%"

    def test_signed(self) -> None:
        assert signed_pct(Decimal("0.1234")) == "+12.34%"
        assert signed_pct(Decimal("-0.0264")) == "-2.64%"

    def test_not_grouped(self) -> None:
        # Um peso ou uma rentabilidade nunca precisa de separador de milhar.
        assert pct(Decimal("123.4567")) == "12345.67%"

    def test_follows_the_decimal_separator(self, comma: None) -> None:
        assert pct(Decimal("0.1234")) == "12,34%"
        assert signed_pct(Decimal("-0.0264")) == "-2,64%"

    def test_none_is_dash(self) -> None:
        assert pct(None) == DASH
        assert signed_pct(None) == DASH


class TestExact:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (Decimal("10.00000000"), "10"),  # escala crua do NUMERIC
            (Decimal("0E+4"), "0"),  # nunca notacao cientifica
            (Decimal("3055.2000"), "3,055.2"),
            (Decimal("0.5"), "0.5"),
        ],
    )
    def test_normalizes_without_scientific_notation(self, value: Decimal, expected: str) -> None:
        assert exact(value) == expected

    def test_follows_the_configured_separators(self, comma: None) -> None:
        assert exact(Decimal("3055.2000")) == "3.055,2"

    def test_none_is_dash_but_json_keeps_null(self) -> None:
        assert exact(None) == DASH
        assert exact_or_none(None) is None
        assert exact_or_none(Decimal("10.00")) == "10"

    def test_json_stays_canonical_whatever_the_user_configured(self, comma: None) -> None:
        # `--json` e consumido por script: separador sempre ponto, sem milhar.
        assert exact_or_none(Decimal("1234.5")) == "1234.5"


class TestSigned:
    def test_gain_is_green_and_loss_is_red(self) -> None:
        assert signed(Decimal("20"), percent=False) == "[green]+20.00[/green]"
        assert signed(Decimal("-20"), percent=False) == "[red]-20.00[/red]"

    def test_zero_counts_as_a_gain(self) -> None:
        assert signed(Decimal("0"), percent=True) == "[green]+0.00%[/green]"
        assert sign_color(Decimal("0")) == "green"

    def test_percent_switches_the_body(self) -> None:
        assert signed(Decimal("0.1275"), percent=True) == "[green]+12.75%[/green]"

    def test_grouping_reaches_the_colored_cell(self) -> None:
        assert signed(Decimal("12772.9"), percent=False) == "[green]+12,772.90[/green]"

    def test_none_is_an_uncolored_dash(self) -> None:
        assert signed(None, percent=True) == DASH
        assert signed(None, percent=False) == DASH


class TestReadNumber:
    """What the user is allowed to type; a refusal always says why."""

    @pytest.mark.parametrize(
        ("typed", "expected"),
        [
            ("126.25", "126.25"),  # canonico
            ("1,234.50", "1234.50"),  # milhar configurado
            ("1,234,567.89", "1234567.89"),
            ("1000", "1000"),
            ("-2.5", "-2.5"),
            ("+1,000", "+1000"),
            (" 126.25 ", "126.25"),  # espacos ao redor
            ("1e3", "1e3"),  # notacao cientifica segue chegando ao Decimal
            ("abc", "abc"),  # lixo tambem: o erro e do Decimal
        ],
    )
    def test_dot_decimal(self, typed: str, expected: str) -> None:
        assert read_number(typed).canonical == expected

    @pytest.mark.parametrize("typed", ["126,25", "1,2,3", "1,23", "1,2345", "0,750", "0,75"])
    def test_dot_decimal_rejects_a_misplaced_comma(self, typed: str) -> None:
        # Com decimal ponto, a virgula so pode agrupar milhar — e nao esta.
        reading = read_number(typed)
        assert reading.canonical is None
        assert reading.reason == MISPLACED

    def test_dot_decimal_has_no_ambiguity(self) -> None:
        # `1.000` so pode ser um decimal aqui: o milhar seria `1,000`.
        assert read_number("1.000").canonical == "1.000"
        assert read_number("1,000").canonical == "1000"

    @pytest.mark.parametrize(
        ("typed", "expected"),
        [
            ("126,25", "126.25"),  # decimal configurado
            ("1.234,50", "1234.50"),  # milhar configurado
            ("1.234.567", "1234567"),
            ("126.25", "126.25"),  # canonico continua valendo
            ("-2,5", "-2.5"),
            ("1000", "1000"),
        ],
    )
    def test_comma_decimal(self, comma: None, typed: str, expected: str) -> None:
        assert read_number(typed).canonical == expected

    @pytest.mark.parametrize("typed", ["1,2,3", "1.23.456"])
    def test_comma_decimal_rejects_a_misplaced_separator(self, comma: None, typed: str) -> None:
        reading = read_number(typed)
        assert reading.canonical is None
        assert reading.reason == MISPLACED

    def test_a_grouping_never_opens_with_a_zero(self, comma: None) -> None:
        # `0.750` nao tem duas leituras: nenhuma convencao escreve 750 assim,
        # entao o ponto so pode ser decimal. E com decimal ponto, `0,750` nao
        # pode virar 750 caladamente — a diferenca e de mil vezes.
        assert read_number("0.750").canonical == "0.750"
        configure(".")
        assert read_number("0,750").canonical is None
        assert read_number("0,750").reason == MISPLACED

    @pytest.mark.parametrize("typed", ["1.000", "12.500", "-1.000", "123.000"])
    def test_comma_decimal_refuses_to_guess_thousand_versus_one(self, comma: None, typed: str) -> None:
        # As duas leituras diferem por mil: melhor recusar do que chutar.
        reading = read_number(typed)
        assert reading.canonical is None
        assert reading.reason == AMBIGUOUS

    @pytest.mark.parametrize(
        ("typed", "expected"),
        [("1.000,00", "1000.00"), ("1000", "1000"), ("1.0000", "1.0000"), ("1.00", "1.00")],
    )
    def test_comma_decimal_keeps_the_unambiguous_neighbours(self, comma: None, typed: str, expected: str) -> None:
        assert read_number(typed).canonical == expected
