"""Tests for the brapi.dev client. Every HTTP call is mocked with respx.

Happy paths assert against golden fixtures recorded live from the API
(``tests/fixtures/brapi_*.json``); the plan-specific error codes, which this
token's plan does not actually trigger, use synthetic bodies matching the
confirmed ``{"error", "message", "code"}`` shape. A single ``@pytest.mark.live``
smoke test hits the real API and is deselected by default (see pyproject addopts).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from httpx import Response

from bogle.data.brapi import BrapiClient
from bogle.data.models import HistPoint, Quote
from bogle.domain.errors import (
    MarketDataError,
    NetworkError,
    QuoteNotFoundError,
    RateLimitError,
)

FIXTURES = Path(__file__).parent / "fixtures"
QUOTE_URL = "https://brapi.dev/api/v2/stocks/quote"
HISTORY_URL = "https://brapi.dev/api/v2/stocks/historical"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def client(**kwargs: Any) -> BrapiClient:
    kwargs.setdefault("token", "test-token")
    kwargs.setdefault("sleep", lambda _seconds: None)  # never sleep for real in tests
    return BrapiClient(**kwargs)


def quote_dict(
    symbol: str,
    *,
    price: float = 10.25,
    requested: str | None = None,
    changed: bool = False,
    previous_close: float = 9.5,
    change: float = 0.75,
    change_percent: float = 7.89,
    currency: str = "BRL",
) -> dict[str, Any]:
    return {
        "requestedSymbol": symbol if requested is None else requested,
        "symbol": symbol,
        "changed": changed,
        "data": {
            "currency": currency,
            "regularMarketPrice": price,
            "regularMarketChange": change,
            "regularMarketChangePercent": change_percent,
            "regularMarketTime": "2026-07-20T22:00:00.000Z",
            "regularMarketPreviousClose": previous_close,
        },
    }


def envelope(*results: dict[str, Any]) -> dict[str, Any]:
    return {"results": list(results), "requestedAt": "2026-07-20T00:00:00.000Z", "took": "1ms"}


def api_error(code: str, message: str = "erro") -> dict[str, Any]:
    return {"error": True, "message": message, "code": code}


class TestGetQuote:
    @respx.mock
    def test_parses_real_petr4_fixture(self) -> None:
        respx.get(QUOTE_URL).mock(return_value=Response(200, text=fixture("brapi_quote_petr4.json")))
        with client() as c:
            q = c.get_quote("PETR4")
        assert isinstance(q, Quote)
        assert q.symbol == "PETR4"
        assert q.requested_symbol == "PETR4"
        assert q.renamed is False
        assert q.price == Decimal("41.15")
        assert q.currency == "BRL"
        assert q.previous_close == Decimal("41.25")
        assert q.change == Decimal("0.25")
        assert q.change_percent == Decimal("0.61")

    @respx.mock
    def test_price_and_change_are_decimal(self) -> None:
        respx.get(QUOTE_URL).mock(return_value=Response(200, text=fixture("brapi_quote_petr4.json")))
        with client() as c:
            q = c.get_quote("PETR4")
        assert isinstance(q.price, Decimal)
        assert isinstance(q.change, Decimal)
        assert isinstance(q.change_percent, Decimal)

    @respx.mock
    def test_time_is_timezone_aware(self) -> None:
        respx.get(QUOTE_URL).mock(return_value=Response(200, text=fixture("brapi_quote_petr4.json")))
        with client() as c:
            q = c.get_quote("PETR4")
        assert q.time.tzinfo is not None
        assert q.time == datetime(2026, 7, 20, 22, 28, 54, tzinfo=UTC)

    @respx.mock
    def test_sends_bearer_token_never_query_param(self) -> None:
        route = respx.get(QUOTE_URL).mock(return_value=Response(200, json=envelope(quote_dict("PETR4"))))
        with client(token="secret-abc") as c:
            c.get_quote("PETR4")
        request = route.calls.last.request
        assert request.headers["Authorization"] == "Bearer secret-abc"
        assert "token" not in request.url.params  # the ?token= form leaks in logs


class TestGetQuotes:
    @respx.mock
    def test_iterates_one_request_per_symbol_on_free_plan(self) -> None:
        def responder(request: httpx.Request) -> Response:
            symbol = request.url.params["symbols"]
            assert "," not in symbol  # free plan: one symbol per request
            return Response(200, json=envelope(quote_dict(symbol)))

        route = respx.get(QUOTE_URL).mock(side_effect=responder)
        with client() as c:  # default chunk_size == 1
            quotes = c.get_quotes(["PETR4", "VALE3", "ITUB4"])
        assert [q.symbol for q in quotes] == ["PETR4", "VALE3", "ITUB4"]
        assert route.call_count == 3

    @respx.mock
    def test_batches_when_chunk_size_allows(self) -> None:
        def responder(request: httpx.Request) -> Response:
            symbols = request.url.params["symbols"].split(",")
            return Response(200, json=envelope(*[quote_dict(s) for s in symbols]))

        route = respx.get(QUOTE_URL).mock(side_effect=responder)
        with client(chunk_size=2) as c:
            quotes = c.get_quotes(["PETR4", "VALE3", "ITUB4"])
        assert [q.symbol for q in quotes] == ["PETR4", "VALE3", "ITUB4"]
        assert route.call_count == 2  # [PETR4,VALE3] then [ITUB4]

    @respx.mock
    def test_empty_input_makes_no_request(self) -> None:
        route = respx.get(QUOTE_URL).mock(return_value=Response(200, json=envelope()))
        with client() as c:
            quotes = c.get_quotes([])
        assert quotes == []
        assert route.call_count == 0


class TestGetIndexQuote:
    @respx.mock
    def test_parses_real_ibov_fixture(self) -> None:
        route = respx.get(QUOTE_URL).mock(return_value=Response(200, text=fixture("brapi_quote_ibov.json")))
        with client() as c:
            q = c.get_index_quote("^BVSP")
        assert q.symbol == "^BVSP"
        assert isinstance(q.price, Decimal)
        assert q.price > 0
        assert route.calls.last.request.url.params["symbols"] == "^BVSP"


class TestGetHistory:
    @respx.mock
    def test_parses_real_history_fixture(self) -> None:
        respx.get(HISTORY_URL).mock(return_value=Response(200, text=fixture("brapi_history_petr4.json")))
        with client() as c:
            points = c.get_history("PETR4")
        assert len(points) == 61
        assert all(isinstance(p, HistPoint) for p in points)
        first = points[0]
        assert isinstance(first.open, Decimal)
        assert isinstance(first.close, Decimal)
        assert isinstance(first.volume, int)

    @respx.mock
    def test_normalizes_to_chronological_order(self) -> None:
        # The API returns newest-first; the client must sort oldest-first.
        respx.get(HISTORY_URL).mock(return_value=Response(200, text=fixture("brapi_history_petr4.json")))
        with client() as c:
            points = c.get_history("PETR4")
        assert points == sorted(points, key=lambda p: p.date)
        # The recorded newest bar (unix 1784516400, close 41.15) is now last.
        assert points[-1].date == datetime.fromtimestamp(1784516400, tz=UTC)
        assert points[-1].close == Decimal("41.15")

    @respx.mock
    def test_forwards_range_interval_and_dates(self) -> None:
        route = respx.get(HISTORY_URL).mock(return_value=Response(200, text=fixture("brapi_history_petr4.json")))
        with client() as c:
            c.get_history("PETR4", range_="1mo", interval="1d", start="2026-01-01", end="2026-03-01")
        params = route.calls.last.request.url.params
        assert params["symbols"] == "PETR4"
        assert params["range"] == "1mo"
        assert params["interval"] == "1d"
        assert params["startDate"] == "2026-01-01"
        assert params["endDate"] == "2026-03-01"

    @respx.mock
    def test_skips_bars_without_close(self) -> None:
        body = {
            "results": [
                {
                    "requestedSymbol": "PETR4",
                    "symbol": "PETR4",
                    "changed": False,
                    "data": {
                        "historicalDataPrice": [
                            {
                                "date": 1784516400,
                                "open": 41.2,
                                "high": 41.4,
                                "low": 40.5,
                                "close": 41.15,
                                "volume": 100,
                                "adjustedClose": 41.15,
                            },
                            {
                                "date": 1784602800,
                                "open": None,
                                "high": None,
                                "low": None,
                                "close": None,
                                "volume": None,
                                "adjustedClose": None,
                            },
                        ]
                    },
                }
            ]
        }
        respx.get(HISTORY_URL).mock(return_value=Response(200, json=body))
        with client() as c:
            points = c.get_history("PETR4")
        assert len(points) == 1
        assert points[0].close == Decimal("41.15")


class TestRenamedTicker:
    @respx.mock
    def test_changed_true_keeps_both_symbols(self) -> None:
        body = envelope(quote_dict("NEWX3", requested="OLDX3", changed=True))
        respx.get(QUOTE_URL).mock(return_value=Response(200, json=body))
        with client() as c:
            q = c.get_quote("OLDX3")
        assert q.symbol == "NEWX3"
        assert q.requested_symbol == "OLDX3"
        assert q.renamed is True


class TestNotFound:
    @respx.mock
    def test_404_raises_quote_not_found(self) -> None:
        respx.get(QUOTE_URL).mock(return_value=Response(404, text=fixture("brapi_error_notfound.json")))
        with client() as c, pytest.raises(QuoteNotFoundError) as exc_info:
            c.get_quote("ZZZZ99")
        assert exc_info.value.symbol == "ZZZZ99"

    @respx.mock
    def test_empty_results_raises_quote_not_found(self) -> None:
        respx.get(QUOTE_URL).mock(return_value=Response(200, json=envelope()))
        with client() as c, pytest.raises(QuoteNotFoundError):
            c.get_quote("PETR4")

    @respx.mock
    def test_missing_price_raises_quote_not_found(self) -> None:
        body = envelope({"requestedSymbol": "PETR4", "symbol": "PETR4", "changed": False, "data": {}})
        respx.get(QUOTE_URL).mock(return_value=Response(200, json=body))
        with client() as c, pytest.raises(QuoteNotFoundError):
            c.get_quote("PETR4")


class TestRetryOn429:
    @respx.mock
    def test_retries_then_succeeds(self) -> None:
        route = respx.get(QUOTE_URL).mock(
            side_effect=[
                Response(429, json=api_error("RATE_LIMIT")),
                Response(200, json=envelope(quote_dict("PETR4"))),
            ]
        )
        with client() as c:
            q = c.get_quote("PETR4")
        assert q.symbol == "PETR4"
        assert route.call_count == 2

    @respx.mock
    def test_exhausts_retries_raises_rate_limit(self) -> None:
        route = respx.get(QUOTE_URL).mock(return_value=Response(429, json=api_error("RATE_LIMIT")))
        with client(max_retries=3) as c, pytest.raises(RateLimitError):
            c.get_quote("PETR4")
        assert route.call_count == 3

    @respx.mock
    def test_backoff_sleeps_between_retries(self) -> None:
        slept: list[float] = []
        respx.get(QUOTE_URL).mock(
            side_effect=[
                Response(429, json=api_error("RATE_LIMIT")),
                Response(429, json=api_error("RATE_LIMIT")),
                Response(200, json=envelope(quote_dict("PETR4"))),
            ]
        )
        with client(max_retries=3, backoff_base=0.5, sleep=slept.append) as c:
            c.get_quote("PETR4")
        assert slept == [0.5, 1.0]  # exponential: base * 2**attempt


class TestNetworkFailure:
    @respx.mock
    def test_connect_error_becomes_network_error(self) -> None:
        respx.get(QUOTE_URL).mock(side_effect=httpx.ConnectError("boom"))
        with client() as c, pytest.raises(NetworkError) as exc_info:
            c.get_quote("PETR4")
        assert exc_info.value.provider == "brapi"

    @respx.mock
    def test_timeout_becomes_network_error(self) -> None:
        respx.get(QUOTE_URL).mock(side_effect=httpx.ConnectTimeout("slow"))
        with client() as c, pytest.raises(NetworkError):
            c.get_quote("PETR4")


class TestPlanErrors:
    @respx.mock
    def test_feature_not_available_403(self) -> None:
        respx.get(QUOTE_URL).mock(
            return_value=Response(403, json=api_error("FEATURE_NOT_AVAILABLE", "nao disponivel no plano"))
        )
        with client() as c, pytest.raises(MarketDataError) as exc_info:
            c.get_quote("PETR4")
        err = exc_info.value
        assert err.code == "FEATURE_NOT_AVAILABLE"
        assert not isinstance(err, (QuoteNotFoundError, RateLimitError, NetworkError))

    @respx.mock
    def test_invalid_range_400(self) -> None:
        respx.get(HISTORY_URL).mock(return_value=Response(400, json=api_error("INVALID_RANGE")))
        with client() as c, pytest.raises(MarketDataError) as exc_info:
            c.get_history("PETR4", range_="1y")
        assert exc_info.value.code == "INVALID_RANGE"

    @respx.mock
    def test_quotes_per_request_exceeded_400(self) -> None:
        respx.get(QUOTE_URL).mock(return_value=Response(400, json=api_error("QUOTES_PER_REQUEST_EXCEEDED")))
        with client(chunk_size=5) as c, pytest.raises(MarketDataError) as exc_info:
            c.get_quotes(["PETR4", "VALE3"])
        assert exc_info.value.code == "QUOTES_PER_REQUEST_EXCEEDED"


class TestTokenHandling:
    @respx.mock
    def test_missing_token_raises_without_calling_api(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BRAPI_TOKEN", raising=False)
        route = respx.get(QUOTE_URL).mock(return_value=Response(200, json=envelope(quote_dict("PETR4"))))
        with BrapiClient(token=None) as c, pytest.raises(MarketDataError) as exc_info:
            c.get_quote("PETR4")
        assert "BRAPI_TOKEN" in str(exc_info.value)
        assert route.call_count == 0

    def test_reads_token_from_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRAPI_TOKEN", "from-env")
        c = BrapiClient()
        assert c._token == "from-env"
        c.close()


@pytest.mark.live
def test_live_quote_petr4() -> None:
    """Smoke test against the real API. Deselected by default; run with -m live."""
    if not os.environ.get("BRAPI_TOKEN"):
        pytest.skip("BRAPI_TOKEN not set")
    with BrapiClient() as c:
        q = c.get_quote("PETR4")
    assert q.symbol == "PETR4"
    assert q.price > 0
