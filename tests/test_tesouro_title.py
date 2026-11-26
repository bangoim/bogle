"""Tests for the TESOURO ticker check shared by ``bogle add`` and the TUI form (issue #93)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from bogle.domain.errors import MarketDataError, ValidationError
from bogle.tesouro_title import check_tesouro_title
from tests.tesouro_fakes import FakeTitles, OfflineTitles


class TestMatching:
    @pytest.mark.parametrize(
        "ticker",
        [
            "Tesouro IPCA+ 2035",
            "TESOURO IPCA+ 2035",  # como o ticker fica gravado (maiusculas)
            "  tesouro   ipca+   2035 ",
        ],
    )
    def test_the_canonical_name_passes_however_it_is_cased_or_spaced(self, ticker: str) -> None:
        assert check_tesouro_title(ticker, date(2035, 5, 15), source=FakeTitles()) is None

    def test_a_semestral_title_is_not_the_principal_one(self) -> None:
        titles = FakeTitles(["Tesouro IPCA+ com Juros Semestrais 2035"])
        with pytest.raises(ValidationError):
            check_tesouro_title("Tesouro IPCA+ 2035", date(2035, 5, 15), source=titles)

    def test_the_maturity_may_be_a_datetime(self) -> None:
        assert check_tesouro_title("Tesouro IPCA+ 2035", datetime(2035, 5, 15, tzinfo=UTC), source=FakeTitles()) is None


class TestRefusal:
    def test_suggests_the_titles_of_the_maturity_year(self) -> None:
        with pytest.raises(ValidationError) as caught:
            check_tesouro_title("TESOURO-IPCA-2035", date(2035, 5, 15), source=FakeTitles())
        assert str(caught.value) == (
            "'TESOURO-IPCA-2035' não é um título do Tesouro Transparente. "
            "Títulos com vencimento em 2035: Tesouro IPCA+ 2035, Tesouro IPCA+ com Juros Semestrais 2035."
        )

    def test_a_year_with_no_title_gets_no_suggestion(self) -> None:
        with pytest.raises(ValidationError) as caught:
            check_tesouro_title("TESOURO-IPCA-2041", date(2041, 5, 15), source=FakeTitles())
        assert str(caught.value) == "'TESOURO-IPCA-2041' não é um título do Tesouro Transparente."


class TestOffline:
    def test_a_network_failure_accepts_with_a_warning(self) -> None:
        warning = check_tesouro_title("Tesouro IPCA+ 2035", date(2035, 5, 15), source=OfflineTitles())
        assert warning == (
            "não foi possível conferir o nome de 'Tesouro IPCA+ 2035' no Tesouro Transparente; "
            "se não for o nome exato do título, ele não será precificado."
        )

    def test_any_failure_to_get_the_list_is_the_same(self) -> None:
        class Blocked:
            def list_titles(self) -> list[str]:
                raise MarketDataError("Tesouro Transparente retornou HTTP 403.", provider="tesouro")

        assert check_tesouro_title("Tesouro IPCA+ 2035", date(2035, 5, 15), source=Blocked()) is not None
