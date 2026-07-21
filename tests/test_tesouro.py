"""Tests for the Tesouro Direto client. HTTP mocked with respx over a compact
golden slice of the real Tesouro Transparente CSV (header + the two most recent
Data Base dates); the cache is pinned to tmp_path.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from httpx import Response

from bogle.data.cache import DiskCache
from bogle.data.models import TesouroQuote
from bogle.data.tesouro import TesouroClient, _parse_decimal, _parse_rate
from bogle.domain.errors import MarketDataError, NetworkError, QuoteNotFoundError

FIXTURES = Path(__file__).parent / "fixtures"
CSV_URL = (
    "https://www.tesourotransparente.gov.br/ckan/dataset/df56aa42-484a-4a59-8184-7676580c81e3"
    "/resource/796d2059-14e9-44e3-80c9-2d9e30b405c1/download/PrecoTaxaTesouroDireto.csv"
)


def csv_fixture() -> str:
    return (FIXTURES / "tesouro_precotaxa.csv").read_text(encoding="utf-8")


def make_client(tmp_path: Path, **kwargs: Any) -> TesouroClient:
    kwargs.setdefault("cache", DiskCache("tesouro", base_dir=tmp_path))
    kwargs.setdefault("sleep", lambda _seconds: None)
    return TesouroClient(**kwargs)


class TestListTitles:
    @respx.mock
    def test_lists_only_latest_snapshot(self, tmp_path: Path) -> None:
        respx.get(CSV_URL).mock(return_value=Response(200, text=csv_fixture()))
        with make_client(tmp_path) as c:
            titles = c.list_titles()
        # 60 titles on the latest Data Base (the fixture also holds the prior day).
        assert len(titles) == 60
        assert titles == sorted(titles)
        assert "Tesouro IPCA+ 2035" in titles
        assert "Tesouro Selic 2029" in titles


class TestGetQuote:
    @respx.mock
    def test_parses_ipca_2035(self, tmp_path: Path) -> None:
        respx.get(CSV_URL).mock(return_value=Response(200, text=csv_fixture()))
        with make_client(tmp_path) as c:
            q = c.get_quote("Tesouro IPCA+ 2035")
        assert isinstance(q, TesouroQuote)
        assert q.title == "Tesouro IPCA+ 2035"
        assert q.bond_type == "Tesouro IPCA+"
        assert q.maturity == date(2035, 5, 15)
        assert q.base_date == date(2026, 7, 17)  # latest, not the prior 07-16
        assert q.pu_compra == Decimal("2429.39")
        assert q.pu_venda == Decimal("2404.58")
        assert q.pu_base == Decimal("2404.58")
        assert q.rate_compra == Decimal("0.0793")
        assert q.rate_venda == Decimal("0.0805")

    @respx.mock
    def test_matching_ignores_case_and_extra_spaces(self, tmp_path: Path) -> None:
        respx.get(CSV_URL).mock(return_value=Response(200, text=csv_fixture()))
        with make_client(tmp_path) as c:
            q = c.get_quote("  tesouro   IPCA+   2035 ")
        assert q.title == "Tesouro IPCA+ 2035"

    @respx.mock
    def test_semestral_and_principal_titles_are_distinct(self, tmp_path: Path) -> None:
        respx.get(CSV_URL).mock(return_value=Response(200, text=csv_fixture()))
        with make_client(tmp_path) as c:
            principal = c.get_quote("Tesouro IPCA+ 2035")
            semestral = c.get_quote("Tesouro IPCA+ com Juros Semestrais 2035")
        assert principal.bond_type == "Tesouro IPCA+"
        assert semestral.bond_type == "Tesouro IPCA+ com Juros Semestrais"
        assert principal.pu_venda != semestral.pu_venda

    @respx.mock
    def test_unknown_title_raises(self, tmp_path: Path) -> None:
        respx.get(CSV_URL).mock(return_value=Response(200, text=csv_fixture()))
        with make_client(tmp_path) as c, pytest.raises(QuoteNotFoundError):
            c.get_quote("Tesouro Inexistente 2099")


class TestCache:
    @respx.mock
    def test_second_call_does_not_redownload(self, tmp_path: Path) -> None:
        route = respx.get(CSV_URL).mock(return_value=Response(200, text=csv_fixture()))
        with make_client(tmp_path) as c:
            c.list_titles()
            c.get_quote("Tesouro IPCA+ 2035")
        assert route.call_count == 1


class TestErrors:
    @respx.mock
    def test_http_error_status_raises(self, tmp_path: Path) -> None:
        respx.get(CSV_URL).mock(return_value=Response(503, text="unavailable"))
        with make_client(tmp_path) as c, pytest.raises(MarketDataError):
            c.list_titles()

    @respx.mock
    def test_network_failure_raises_network_error(self, tmp_path: Path) -> None:
        respx.get(CSV_URL).mock(side_effect=httpx.ConnectError("boom"))
        with make_client(tmp_path) as c, pytest.raises(NetworkError):
            c.list_titles()

    @respx.mock
    def test_retries_on_429_then_succeeds(self, tmp_path: Path) -> None:
        route = respx.get(CSV_URL).mock(side_effect=[Response(429, text="slow"), Response(200, text=csv_fixture())])
        with make_client(tmp_path) as c:
            titles = c.list_titles()
        assert route.call_count == 2
        assert titles

    @respx.mock
    def test_empty_body_raises(self, tmp_path: Path) -> None:
        respx.get(CSV_URL).mock(return_value=Response(200, text=""))
        with make_client(tmp_path) as c, pytest.raises(MarketDataError):
            c.list_titles()

    @respx.mock
    def test_missing_columns_raises(self, tmp_path: Path) -> None:
        respx.get(CSV_URL).mock(return_value=Response(200, text="foo;bar\n1;2\n"))
        with make_client(tmp_path) as c, pytest.raises(MarketDataError):
            c.list_titles()


class TestNumberParsing:
    def test_parse_decimal_comma(self) -> None:
        assert _parse_decimal("2429,39") == Decimal("2429.39")

    def test_parse_decimal_thousands_and_comma(self) -> None:
        assert _parse_decimal("1.234,56") == Decimal("1234.56")

    def test_parse_decimal_negative(self) -> None:
        assert _parse_decimal("-0,02") == Decimal("-0.02")

    def test_parse_decimal_empty_and_none(self) -> None:
        assert _parse_decimal("") is None
        assert _parse_decimal("   ") is None
        assert _parse_decimal(None) is None

    def test_parse_rate_divides_by_100(self) -> None:
        assert _parse_rate("8,05") == Decimal("0.0805")
        assert _parse_rate(None) is None


@pytest.mark.live
def test_live_ipca_quote(tmp_path: Path) -> None:
    """Smoke test against the real 14MB CSV. Deselected by default; run with -m live."""
    with TesouroClient(cache=DiskCache("tesouro", base_dir=tmp_path)) as c:
        titles = c.list_titles()
    assert titles
    assert any(t.startswith("Tesouro ") for t in titles)
