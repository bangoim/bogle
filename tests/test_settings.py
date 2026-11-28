"""Tests for the user_settings module (issue #31): typed get/set/unset/list."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import psycopg
import pytest
from psycopg.rows import DictRow

from bogle.domain.errors import UnknownSettingError, ValidationError
from bogle.settings import (
    BIRTH_YEAR,
    DEFAULT_COMPARE_INDICES,
    GLIDE_PATH_APPLIED_YEAR,
    HIDE_VALUES,
    LAST_ANNUAL_REBALANCE_YEAR,
    LAST_REBALANCE_DATE,
    REBALANCE_PERIOD_MONTHS,
    RETIREMENT_AGE,
    SPENDING_LAST_SYNC_AT,
    WEIGHT_DRIFT_THRESHOLD,
    format_value,
    get_setting,
    list_settings,
    set_setting,
    set_value,
    unset_setting,
)


class TestDefaults:
    def test_period_defaults_to_12(self, conn: psycopg.Connection[DictRow]) -> None:
        assert get_setting(conn, REBALANCE_PERIOD_MONTHS) == 12

    def test_indices_default_to_ibov_and_cdi(self, conn: psycopg.Connection[DictRow]) -> None:
        assert get_setting(conn, DEFAULT_COMPARE_INDICES) == ["IBOV", "CDI"]

    def test_threshold_defaults_to_5pp(self, conn: psycopg.Connection[DictRow]) -> None:
        assert get_setting(conn, WEIGHT_DRIFT_THRESHOLD) == Decimal("0.05")

    def test_last_rebalance_defaults_to_none(self, conn: psycopg.Connection[DictRow]) -> None:
        assert get_setting(conn, LAST_REBALANCE_DATE) is None


class TestRoundTrip:
    def test_period(self, conn: psycopg.Connection[DictRow]) -> None:
        assert set_setting(conn, REBALANCE_PERIOD_MONTHS, "6") == 6
        assert get_setting(conn, REBALANCE_PERIOD_MONTHS) == 6

    def test_indices_normalized_upper(self, conn: psycopg.Connection[DictRow]) -> None:
        set_setting(conn, DEFAULT_COMPARE_INDICES, "cdi, ibov")
        assert get_setting(conn, DEFAULT_COMPARE_INDICES) == ["CDI", "IBOV"]

    def test_threshold_keeps_decimal_exactness(self, conn: psycopg.Connection[DictRow]) -> None:
        set_setting(conn, WEIGHT_DRIFT_THRESHOLD, "0.03")
        value = get_setting(conn, WEIGHT_DRIFT_THRESHOLD)
        assert isinstance(value, Decimal)
        assert value == Decimal("0.03")

    def test_date(self, conn: psycopg.Connection[DictRow]) -> None:
        set_setting(conn, LAST_REBALANCE_DATE, "2026-07-01")
        assert get_setting(conn, LAST_REBALANCE_DATE) == date(2026, 7, 1)

    def test_set_value_typed(self, conn: psycopg.Connection[DictRow]) -> None:
        set_value(conn, LAST_REBALANCE_DATE, date(2026, 7, 22))
        assert get_setting(conn, LAST_REBALANCE_DATE) == date(2026, 7, 22)

    def test_set_overwrites(self, conn: psycopg.Connection[DictRow]) -> None:
        set_setting(conn, REBALANCE_PERIOD_MONTHS, "6")
        set_setting(conn, REBALANCE_PERIOD_MONTHS, "12")
        assert get_setting(conn, REBALANCE_PERIOD_MONTHS) == 12


class TestValidation:
    def test_period_rejects_other_values(self, conn: psycopg.Connection[DictRow]) -> None:
        with pytest.raises(ValidationError, match="6 ou 12"):
            set_setting(conn, REBALANCE_PERIOD_MONTHS, "9")

    def test_period_rejects_non_int(self, conn: psycopg.Connection[DictRow]) -> None:
        with pytest.raises(ValidationError, match="inteiro"):
            set_setting(conn, REBALANCE_PERIOD_MONTHS, "seis")

    def test_threshold_rejects_out_of_range(self, conn: psycopg.Connection[DictRow]) -> None:
        with pytest.raises(ValidationError, match=r"\(0, 1\)"):
            set_setting(conn, WEIGHT_DRIFT_THRESHOLD, "1.5")

    def test_indices_reject_empty(self, conn: psycopg.Connection[DictRow]) -> None:
        with pytest.raises(ValidationError, match="vazia"):
            set_setting(conn, DEFAULT_COMPARE_INDICES, " , ")

    def test_date_rejects_bad_format(self, conn: psycopg.Connection[DictRow]) -> None:
        with pytest.raises(ValidationError, match="YYYY-MM-DD"):
            set_setting(conn, LAST_REBALANCE_DATE, "22/07/2026")

    def test_unknown_key(self, conn: psycopg.Connection[DictRow]) -> None:
        with pytest.raises(UnknownSettingError, match="não reconhecida"):
            get_setting(conn, "nope")
        with pytest.raises(UnknownSettingError):
            set_setting(conn, "nope", "1")
        with pytest.raises(UnknownSettingError):
            unset_setting(conn, "nope")


class TestUnsetAndList:
    def test_unset_reverts_to_default(self, conn: psycopg.Connection[DictRow]) -> None:
        set_setting(conn, REBALANCE_PERIOD_MONTHS, "6")
        unset_setting(conn, REBALANCE_PERIOD_MONTHS)
        assert get_setting(conn, REBALANCE_PERIOD_MONTHS) == 12

    def test_unset_missing_key_is_noop(self, conn: psycopg.Connection[DictRow]) -> None:
        unset_setting(conn, REBALANCE_PERIOD_MONTHS)  # nunca setada

    def test_list_marks_provenance(self, conn: psycopg.Connection[DictRow]) -> None:
        set_setting(conn, WEIGHT_DRIFT_THRESHOLD, "0.03")
        entries = {e.key: e for e in list_settings(conn)}
        assert set(entries) == {
            "decimal_separator",
            "hide_values",
            "theme",
            REBALANCE_PERIOD_MONTHS,
            DEFAULT_COMPARE_INDICES,
            WEIGHT_DRIFT_THRESHOLD,
            LAST_REBALANCE_DATE,
            BIRTH_YEAR,
            RETIREMENT_AGE,
            GLIDE_PATH_APPLIED_YEAR,
            LAST_ANNUAL_REBALANCE_YEAR,
            SPENDING_LAST_SYNC_AT,
        }
        assert entries[WEIGHT_DRIFT_THRESHOLD].is_default is False
        assert entries[WEIGHT_DRIFT_THRESHOLD].updated_at is not None
        assert entries[REBALANCE_PERIOD_MONTHS].is_default is True
        assert entries[REBALANCE_PERIOD_MONTHS].updated_at is None


class TestFormatValue:
    def test_none(self) -> None:
        assert format_value(None) == "(não definido)"

    def test_list(self) -> None:
        assert format_value(["CDI", "IBOV"]) == "CDI,IBOV"

    def test_date(self) -> None:
        assert format_value(date(2026, 7, 1)) == "2026-07-01"

    def test_decimal(self) -> None:
        assert format_value(Decimal("0.05")) == "0.05"


class TestHideValues:
    def test_default_is_visible(self, conn: psycopg.Connection[DictRow]) -> None:
        assert get_setting(conn, HIDE_VALUES) is False

    @pytest.mark.parametrize("raw", ["true", "TRUE", "1", "sim", "yes", "on"])
    def test_accepts_the_usual_spellings_of_true(self, conn: psycopg.Connection[DictRow], raw: str) -> None:
        assert set_setting(conn, HIDE_VALUES, raw) is True

    @pytest.mark.parametrize("raw", ["false", "0", "não", "no", "off"])
    def test_accepts_the_usual_spellings_of_false(self, conn: psycopg.Connection[DictRow], raw: str) -> None:
        assert set_setting(conn, HIDE_VALUES, raw) is False

    def test_rejects_anything_else(self, conn: psycopg.Connection[DictRow]) -> None:
        with pytest.raises(ValidationError, match="não é um booleano"):
            set_setting(conn, HIDE_VALUES, "talvez")

    def test_round_trips_through_jsonb(self, conn: psycopg.Connection[DictRow]) -> None:
        set_setting(conn, HIDE_VALUES, "true")
        assert get_setting(conn, HIDE_VALUES) is True

    def test_reads_as_the_word_it_is_typed_with(self) -> None:
        # `bogle config get hide_values` mostra o que se digita, nao "True".
        assert format_value(True) == "true"
        assert format_value(False) == "false"


class TestGlidePathKeys:
    def test_both_default_to_none(self, conn: psycopg.Connection[DictRow]) -> None:
        assert get_setting(conn, BIRTH_YEAR) is None
        assert get_setting(conn, GLIDE_PATH_APPLIED_YEAR) is None

    def test_birth_year_round_trips_as_an_int(self, conn: psycopg.Connection[DictRow]) -> None:
        assert set_setting(conn, BIRTH_YEAR, " 2000 ") == 2000
        assert get_setting(conn, BIRTH_YEAR) == 2000

    @pytest.mark.parametrize("raw", ["1899", "26", "20000", str(date.today().year + 1)])
    def test_birth_year_must_be_plausible(self, conn: psycopg.Connection[DictRow], raw: str) -> None:
        with pytest.raises(ValidationError, match="Ano de nascimento deve estar entre 1900 e"):
            set_setting(conn, BIRTH_YEAR, raw)

    def test_birth_year_this_year_is_accepted(self, conn: psycopg.Connection[DictRow]) -> None:
        assert set_setting(conn, BIRTH_YEAR, str(date.today().year)) == date.today().year

    def test_birth_year_rejects_non_int(self, conn: psycopg.Connection[DictRow]) -> None:
        with pytest.raises(ValidationError, match="não é um ano válido"):
            set_setting(conn, BIRTH_YEAR, "dois mil")

    def test_applied_year_is_written_by_code(self, conn: psycopg.Connection[DictRow]) -> None:
        set_value(conn, GLIDE_PATH_APPLIED_YEAR, 2040)
        assert get_setting(conn, GLIDE_PATH_APPLIED_YEAR) == 2040


class TestAnnualRebalanceKey:
    """``last_annual_rebalance_year`` (issue #81), no molde de ``glide_path_applied_year``."""

    def test_defaults_to_none(self, conn: psycopg.Connection[DictRow]) -> None:
        assert get_setting(conn, LAST_ANNUAL_REBALANCE_YEAR) is None

    def test_is_written_by_code_as_an_int(self, conn: psycopg.Connection[DictRow]) -> None:
        set_value(conn, LAST_ANNUAL_REBALANCE_YEAR, 2061)
        assert get_setting(conn, LAST_ANNUAL_REBALANCE_YEAR) == 2061

    def test_can_be_set_by_hand(self, conn: psycopg.Connection[DictRow]) -> None:
        assert set_setting(conn, LAST_ANNUAL_REBALANCE_YEAR, " 2061 ") == 2061

    def test_rejects_non_int(self, conn: psycopg.Connection[DictRow]) -> None:
        with pytest.raises(ValidationError, match="não é um ano válido"):
            set_setting(conn, LAST_ANNUAL_REBALANCE_YEAR, "ontem")


class TestRetirementAge:
    def test_defaults_to_seventy(self, conn: psycopg.Connection[DictRow]) -> None:
        assert get_setting(conn, RETIREMENT_AGE) == 70

    def test_round_trips_as_an_int(self, conn: psycopg.Connection[DictRow]) -> None:
        assert set_setting(conn, RETIREMENT_AGE, " 65 ") == 65
        assert get_setting(conn, RETIREMENT_AGE) == 65

    @pytest.mark.parametrize("raw", ["0", "121", "-5"])
    def test_must_be_a_plausible_age(self, conn: psycopg.Connection[DictRow], raw: str) -> None:
        with pytest.raises(ValidationError, match="Idade de aposentadoria deve estar entre 1 e 120"):
            set_setting(conn, RETIREMENT_AGE, raw)

    def test_rejects_non_int(self, conn: psycopg.Connection[DictRow]) -> None:
        with pytest.raises(ValidationError, match="não é uma idade válida"):
            set_setting(conn, RETIREMENT_AGE, "setenta")


class TestSpendingLastSyncAt:
    """The mark of the automatic spending sync (issue #114): an instant, always with its zone."""

    def test_defaults_to_none(self, conn: psycopg.Connection[DictRow]) -> None:
        assert get_setting(conn, SPENDING_LAST_SYNC_AT) is None

    def test_round_trips_the_instant_with_its_zone(self, conn: psycopg.Connection[DictRow]) -> None:
        moment = datetime(2026, 11, 27, 8, 30, 15, 123456, tzinfo=UTC)
        set_value(conn, SPENDING_LAST_SYNC_AT, moment)
        stored = get_setting(conn, SPENDING_LAST_SYNC_AT)
        assert stored == moment
        assert stored.tzinfo is not None

    def test_parses_iso_with_an_offset(self, conn: psycopg.Connection[DictRow]) -> None:
        value = set_setting(conn, SPENDING_LAST_SYNC_AT, "2026-11-27T08:00:00-03:00")
        assert value == datetime(2026, 11, 27, 8, tzinfo=timezone(timedelta(hours=-3)))
        assert get_setting(conn, SPENDING_LAST_SYNC_AT) == datetime(2026, 11, 27, 11, tzinfo=UTC)

    @pytest.mark.parametrize("raw", ["2026-11-27T08:00:00", "2026-11-27", "ontem"])
    def test_refuses_an_instant_without_a_zone(self, conn: psycopg.Connection[DictRow], raw: str) -> None:
        with pytest.raises(ValidationError):
            set_setting(conn, SPENDING_LAST_SYNC_AT, raw)

    def test_reads_as_iso(self) -> None:
        assert format_value(datetime(2026, 11, 27, 8, tzinfo=UTC)) == "2026-11-27T08:00:00+00:00"
