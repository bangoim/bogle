"""Unit tests for the CLI option parsers (in-process, no subprocess)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from bogle.cli.parsing import parse_date, parse_decimal, parse_rate, parse_weight
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
