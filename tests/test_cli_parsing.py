"""Unit tests for the CLI option parsers (in-process, no subprocess)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from bogle.cli.parsing import parse_date, parse_decimal, parse_rate, parse_ticker_values, parse_weight
from bogle.domain.errors import ValidationError


class TestParseDecimal:
    def test_valid(self) -> None:
        assert parse_decimal("10.5", "--shares") == Decimal("10.5")

    @pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "inf"])
    def test_non_finite_rejected(self, value: str) -> None:
        # NaN/Infinity parseiam como Decimal mas estouram em comparacoes e no banco.
        with pytest.raises(ValidationError, match="--shares deve ser um numero decimal"):
            parse_decimal(value, "--shares")


class TestParseDate:
    def test_naive_date_gets_sao_paulo_timezone(self) -> None:
        parsed = parse_date("2026-04-01", "--purchase-date")
        # Wall time preservado (meia-noite local), nao convertido.
        assert parsed == datetime(2026, 4, 1, tzinfo=ZoneInfo("America/Sao_Paulo"))
        assert parsed.hour == 0
        assert parsed.tzinfo == ZoneInfo("America/Sao_Paulo")

    def test_aware_input_keeps_its_timezone(self) -> None:
        parsed = parse_date("2026-04-01T12:00:00+00:00", "--purchase-date")
        assert parsed == datetime(2026, 4, 1, 12, tzinfo=UTC)

    def test_invalid_format_mentions_the_option(self) -> None:
        with pytest.raises(ValidationError, match="--maturity-date deve ser uma data ISO"):
            parse_date("01/04/2026", "--maturity-date")


class TestParseRate:
    def test_valid_decimal(self) -> None:
        assert parse_rate("1.10", "--rate") == Decimal("1.10")

    def test_not_a_number(self) -> None:
        with pytest.raises(ValidationError, match="--rate deve ser um numero decimal"):
            parse_rate("abc", "--rate")

    @pytest.mark.parametrize("value", ["0", "-5", "10000", "100000"])
    def test_out_of_range(self, value: str) -> None:
        # Limite superior espelha NUMERIC(10, 6): sem ele o psycopg
        # estouraria com NumericValueOutOfRange cru.
        with pytest.raises(ValidationError, match=r"--rate deve estar em \(0, 10000\)"):
            parse_rate(value, "--rate")


class TestParseWeight:
    def test_valid(self) -> None:
        assert parse_weight("0.6", "--weight") == Decimal("0.6")

    def test_out_of_range(self) -> None:
        with pytest.raises(ValidationError, match=r"deve estar em \(0, 1\]"):
            parse_weight("1.5", "--weight")

    def test_zero_is_refused_for_a_new_asset(self) -> None:
        with pytest.raises(ValidationError, match=r"deve estar em \(0, 1\]"):
            parse_weight("0", "--weight")

    def test_zero_is_accepted_when_changing_an_asset(self) -> None:
        # O caminho de volta de um target restaurado por engano: sem isso, so a
        # venda que zera a posicao chegava ao zero (migracao 006).
        assert parse_weight("0", "--weight", allow_zero=True) == Decimal("0")

    @pytest.mark.parametrize("value", ["-0.1", "1.5"])
    def test_the_open_range_still_has_both_ends(self, value: str) -> None:
        with pytest.raises(ValidationError, match=r"deve estar em \[0, 1\]"):
            parse_weight(value, "--weight", allow_zero=True)


def parse_price_overrides(values: list[str], option: str) -> dict[str, Decimal]:
    return parse_ticker_values(values, option, unit="PRECO", example="VWRA11=114,86")


class TestParseTickerValues:
    def test_pairs_become_a_mapping_with_upper_case_tickers(self) -> None:
        assert parse_price_overrides(["vwra11=114,86", "B5P211=110.67"], "--price") == {
            "VWRA11": Decimal("114.86"),
            "B5P211": Decimal("110.67"),
        }

    def test_empty_gives_an_empty_mapping(self) -> None:
        assert parse_price_overrides([], "--price") == {}

    def test_spaces_around_the_pair_are_tolerated(self) -> None:
        assert parse_price_overrides([" vwra11 = 114.86 "], "--price") == {"VWRA11": Decimal("114.86")}

    @pytest.mark.parametrize("raw", ["VWRA11", "=114.86", "114.86"])
    def test_a_pair_without_both_halves_is_refused(self, raw: str) -> None:
        with pytest.raises(ValidationError, match="TICKER=PRECO"):
            parse_price_overrides([raw], "--price")

    def test_the_price_goes_through_the_shared_number_parser(self) -> None:
        # Milhar com separador e o que torna um numero ambiguo, aqui como em
        # qualquer outro campo.
        with pytest.raises(ValidationError, match="--price VWRA11"):
            parse_price_overrides(["VWRA11=1.114,86"], "--price")

    def test_the_same_ticker_twice_is_refused(self) -> None:
        # Silenciosamente valeria o ultimo, e o usuario leria o primeiro.
        with pytest.raises(ValidationError, match="repetido para VWRA11"):
            parse_price_overrides(["VWRA11=114", "vwra11=115"], "--price")

    def test_the_error_names_the_unit_and_shows_the_example(self) -> None:
        with pytest.raises(ValidationError, match=r"--qty espera TICKER=QTDE \(ex: VWRA11=10\)"):
            parse_ticker_values(["VWRA11"], "--qty", unit="QTDE", example="VWRA11=10")
