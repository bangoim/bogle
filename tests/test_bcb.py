"""Tests for the BCB SGS client. Every HTTP call is mocked with respx; the cache
is pinned to tmp_path so nothing touches the real ~/.cache.

Happy paths assert against golden fixtures recorded live (``bcb_cdi_12.json``,
``bcb_ipca_433.json``, ``bcb_selic_11.json``, ``bcb_error_badcode.html``).
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

from bogle.data.bcb import CDI_CODE, IPCA_CODE, SELIC_CODE, BcbClient
from bogle.data.cache import DiskCache
from bogle.data.models import SeriesPoint
from bogle.domain.errors import MarketDataError, NetworkError

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def series_url(code: int) -> str:
    return f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados"


def make_client(tmp_path: Path, **kwargs: Any) -> BcbClient:
    kwargs.setdefault("cache", DiskCache("bcb", base_dir=tmp_path))
    kwargs.setdefault("sleep", lambda _seconds: None)
    return BcbClient(**kwargs)


class TestShortcuts:
    @respx.mock
    def test_cdi_parses_fixture_as_daily_fraction(self, tmp_path: Path) -> None:
        respx.get(series_url(CDI_CODE)).mock(return_value=Response(200, text=fixture("bcb_cdi_12.json")))
        with make_client(tmp_path) as c:
            points = c.get_cdi(date(2026, 1, 5), date(2026, 1, 16))
        assert len(points) == 10
        assert points[0] == SeriesPoint(date(2026, 1, 5), Decimal("0.055131") / 100)
        assert points[0].value == Decimal("0.00055131")
        assert all(isinstance(p.value, Decimal) for p in points)

    @respx.mock
    def test_ipca_monthly_fixture(self, tmp_path: Path) -> None:
        respx.get(series_url(IPCA_CODE)).mock(return_value=Response(200, text=fixture("bcb_ipca_433.json")))
        with make_client(tmp_path) as c:
            points = c.get_ipca(date(2025, 1, 1), date(2025, 6, 30))
        assert len(points) == 6
        assert points[0].date == date(2025, 1, 1)
        assert points[0].value == Decimal("0.16") / 100
        assert points[-1].value == Decimal("0.24") / 100

    @respx.mock
    def test_selic_uses_series_11(self, tmp_path: Path) -> None:
        route = respx.get(series_url(SELIC_CODE)).mock(return_value=Response(200, text=fixture("bcb_selic_11.json")))
        with make_client(tmp_path) as c:
            points = c.get_selic(date(2026, 1, 5), date(2026, 1, 16))
        assert route.called
        assert len(points) == 10
        assert points[0].value == Decimal("0.055131") / 100


class TestGetSeries:
    @respx.mock
    def test_raw_values_keep_percent(self, tmp_path: Path) -> None:
        respx.get(series_url(CDI_CODE)).mock(return_value=Response(200, text=fixture("bcb_cdi_12.json")))
        with make_client(tmp_path) as c:
            points = c.get_series(CDI_CODE, date(2026, 1, 5), date(2026, 1, 16), as_fraction=False)
        assert points[0].value == Decimal("0.055131")

    @respx.mock
    def test_sends_ddmmyyyy_date_params(self, tmp_path: Path) -> None:
        route = respx.get(series_url(CDI_CODE)).mock(return_value=Response(200, text=fixture("bcb_cdi_12.json")))
        with make_client(tmp_path) as c:
            c.get_cdi(date(2026, 1, 5), date(2026, 1, 16))
        params = route.calls.last.request.url.params
        assert params["formato"] == "json"
        assert params["dataInicial"] == "05/01/2026"
        assert params["dataFinal"] == "16/01/2026"

    @respx.mock
    def test_normalizes_to_chronological_order(self, tmp_path: Path) -> None:
        body = [
            {"data": "03/01/2026", "valor": "0.02"},
            {"data": "01/01/2026", "valor": "0.01"},
            {"data": "02/01/2026", "valor": "0.03"},
        ]
        respx.get(series_url(CDI_CODE)).mock(return_value=Response(200, json=body))
        with make_client(tmp_path) as c:
            points = c.get_series(CDI_CODE, date(2026, 1, 1), date(2026, 1, 3))
        assert [p.date for p in points] == [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)]

    @respx.mock
    def test_without_dates_sends_no_date_params(self, tmp_path: Path) -> None:
        route = respx.get(series_url(CDI_CODE)).mock(return_value=Response(200, text=fixture("bcb_cdi_12.json")))
        with make_client(tmp_path) as c:
            c.get_series(CDI_CODE)
        assert route.call_count == 1
        params = route.calls.last.request.url.params
        assert "dataInicial" not in params
        assert "dataFinal" not in params

    @respx.mock
    def test_empty_series_returns_empty_list(self, tmp_path: Path) -> None:
        respx.get(series_url(CDI_CODE)).mock(return_value=Response(200, json=[]))
        with make_client(tmp_path) as c:
            assert c.get_cdi(date(2026, 1, 5), date(2026, 1, 16)) == []


class TestPagination:
    @respx.mock
    def test_splits_windows_over_ten_years(self, tmp_path: Path) -> None:
        def responder(request: httpx.Request) -> Response:
            start = request.url.params["dataInicial"]
            return Response(200, json=[{"data": start, "valor": "0.01"}])

        route = respx.get(series_url(CDI_CODE)).mock(side_effect=responder)
        with make_client(tmp_path) as c:
            points = c.get_series(CDI_CODE, date(2000, 1, 1), date(2026, 12, 31))
        assert route.call_count == 3  # 2000-2009, 2010-2019, 2020-2026
        assert [p.date for p in points] == [date(2000, 1, 1), date(2010, 1, 1), date(2020, 1, 1)]
        finals = {call.request.url.params["dataFinal"] for call in route.calls}
        assert "31/12/2009" in finals
        assert "31/12/2026" in finals


class TestCache:
    @respx.mock
    def test_second_identical_call_uses_cache(self, tmp_path: Path) -> None:
        route = respx.get(series_url(CDI_CODE)).mock(return_value=Response(200, text=fixture("bcb_cdi_12.json")))
        with make_client(tmp_path) as c:
            first = c.get_cdi(date(2026, 1, 5), date(2026, 1, 16))
            second = c.get_cdi(date(2026, 1, 5), date(2026, 1, 16))
        assert route.call_count == 1
        assert first == second

    @respx.mock
    def test_different_window_is_a_separate_entry(self, tmp_path: Path) -> None:
        route = respx.get(series_url(CDI_CODE)).mock(return_value=Response(200, text=fixture("bcb_cdi_12.json")))
        with make_client(tmp_path) as c:
            c.get_cdi(date(2026, 1, 5), date(2026, 1, 16))
            c.get_cdi(date(2026, 2, 1), date(2026, 2, 10))
        assert route.call_count == 2

    @respx.mock
    def test_expired_entry_refetches(self, tmp_path: Path) -> None:
        clock = [1000.0]
        cache = DiskCache("bcb", base_dir=tmp_path, now=lambda: clock[0])
        route = respx.get(series_url(CDI_CODE)).mock(return_value=Response(200, text=fixture("bcb_cdi_12.json")))
        with make_client(tmp_path, cache=cache, cache_ttl=60) as c:
            c.get_cdi(date(2026, 1, 5), date(2026, 1, 16))
            clock[0] = 5000.0  # well past the 60s TTL
            c.get_cdi(date(2026, 1, 5), date(2026, 1, 16))
        assert route.call_count == 2


class TestErrors:
    @respx.mock
    def test_invalid_code_html_body_raises(self, tmp_path: Path) -> None:
        # SGS answers an unknown code with HTTP 200 + an HTML error page.
        respx.get(series_url(999999999)).mock(return_value=Response(200, text=fixture("bcb_error_badcode.html")))
        with make_client(tmp_path) as c, pytest.raises(MarketDataError) as exc_info:
            c.get_series(999999999, date(2026, 1, 1), date(2026, 1, 31))
        assert exc_info.value.provider == "bcb"

    @respx.mock
    def test_non_list_json_raises(self, tmp_path: Path) -> None:
        respx.get(series_url(CDI_CODE)).mock(return_value=Response(200, json={"erro": "x"}))
        with make_client(tmp_path) as c, pytest.raises(MarketDataError):
            c.get_series(CDI_CODE)

    @respx.mock
    def test_http_error_status_raises(self, tmp_path: Path) -> None:
        respx.get(series_url(CDI_CODE)).mock(return_value=Response(500, text="oops"))
        with make_client(tmp_path) as c, pytest.raises(MarketDataError):
            c.get_series(CDI_CODE)

    @respx.mock
    def test_network_failure_raises_network_error(self, tmp_path: Path) -> None:
        respx.get(series_url(CDI_CODE)).mock(side_effect=httpx.ConnectError("boom"))
        with make_client(tmp_path) as c, pytest.raises(NetworkError):
            c.get_series(CDI_CODE)

    @respx.mock
    def test_retries_on_429_then_succeeds(self, tmp_path: Path) -> None:
        route = respx.get(series_url(CDI_CODE)).mock(
            side_effect=[Response(429, text="slow"), Response(200, text=fixture("bcb_cdi_12.json"))]
        )
        with make_client(tmp_path) as c:
            points = c.get_cdi(date(2026, 1, 5), date(2026, 1, 16))
        assert route.call_count == 2
        assert len(points) == 10


@pytest.mark.live
def test_live_cdi(tmp_path: Path) -> None:
    """Smoke test against the real SGS API. Deselected by default; run with -m live."""
    with BcbClient(cache=DiskCache("bcb", base_dir=tmp_path)) as c:
        points = c.get_cdi(date(2026, 1, 5), date(2026, 1, 16))
    assert points
    assert all(isinstance(p.value, Decimal) for p in points)
