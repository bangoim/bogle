"""Client for the brapi.dev market-data API (v2).

Primary source of *current* quotes for B3 tickers and indices. Historical bars
are also available, though the range depends on the token's plan; the client does
not police ranges — it forwards them and maps whatever error the API returns.

Wire format (confirmed live 2026-07-20 against PETR4 / ^BVSP):
``{"results": [{"requestedSymbol", "symbol", "changed", "data": {...}}],
"requestedAt", "took"}`` — quote fields and ``historicalDataPrice[]`` both live
inside ``data``. Errors come back as ``{"error": true, "message", "code"}`` with a
non-200 status. All JSON numbers are parsed straight to ``Decimal``.

Auth is ``Authorization: Bearer $BRAPI_TOKEN`` (never the ``?token=`` query form,
which leaks in logs). The token is read from the environment unless passed in.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, NoReturn

import httpx

from bogle.data.models import HistPoint, Quote
from bogle.domain.errors import (
    MarketDataError,
    NetworkError,
    QuoteNotFoundError,
    RateLimitError,
)

_PROVIDER = "brapi"
_BASE_URL = "https://brapi.dev"
_QUOTE_PATH = "/api/v2/stocks/quote"
_HISTORY_PATH = "/api/v2/stocks/historical"
_TOKEN_ENV = "BRAPI_TOKEN"

_DEFAULT_TIMEOUT = 15.0
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_BACKOFF_BASE = 0.5  # seconds; doubles each retry
# Free plan accepts a single symbol per request; a paid plan raises this
# (Startup batch=10, Pro batch=20) without any code change.
_DEFAULT_CHUNK_SIZE = 1


def _dec(value: Any) -> Decimal:
    """Coerce a JSON number to ``Decimal`` exactly, via ``str`` (never float)."""
    return Decimal(str(value))


def _dec_opt(value: Any) -> Decimal | None:
    return None if value is None else _dec(value)


def _parse_time(value: Any) -> datetime:
    # brapi sends ISO-8601 with a trailing 'Z' and milliseconds
    # (e.g. "2026-07-20T22:28:54.000Z"); fromisoformat handles both on 3.11+.
    if not value:
        raise MarketDataError("Cotacao da brapi sem timestamp.", provider=_PROVIDER)
    return datetime.fromisoformat(str(value))


def _chunked(items: Sequence[str], size: int) -> Iterator[Sequence[str]]:
    size = max(1, size)
    for i in range(0, len(items), size):
        yield items[i : i + size]


class BrapiClient:
    """Thin, synchronous client over ``httpx``.

    Usable as a context manager. When no ``client`` is injected it owns an
    ``httpx.Client`` and closes it on exit; tests inject their own (or let
    ``respx`` intercept) and pass a no-op ``sleep`` to skip real backoff waits.
    """

    def __init__(
        self,
        token: str | None = None,
        *,
        base_url: str = _BASE_URL,
        chunk_size: int = _DEFAULT_CHUNK_SIZE,
        timeout: float = _DEFAULT_TIMEOUT,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        backoff_base: float = _DEFAULT_BACKOFF_BASE,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._token = token if token is not None else os.environ.get(_TOKEN_ENV)
        self._chunk_size = max(1, chunk_size)
        self._max_retries = max(1, max_retries)
        self._backoff_base = backoff_base
        self._sleep = sleep
        self._owns_client = client is None
        self._client = client if client is not None else httpx.Client(base_url=base_url, timeout=timeout)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> BrapiClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # --- public API -----------------------------------------------------

    def get_quote(self, symbol: str) -> Quote:
        """Current quote for a single B3 ticker (e.g. ``"PETR4"``)."""
        body = self._request(_QUOTE_PATH, {"symbols": symbol}, symbol=symbol)
        results = self._results(body, symbol)
        return self._parse_quote(results[0])

    def get_quotes(self, symbols: Sequence[str]) -> list[Quote]:
        """Quotes for several tickers, batched by ``chunk_size`` (1 on the free plan).

        A symbol the API cannot resolve fails its whole chunk with
        :class:`QuoteNotFoundError` — with the default chunk size that isolates
        the failure to the offending symbol.
        """
        quotes: list[Quote] = []
        for chunk in _chunked(list(symbols), self._chunk_size):
            joined = ",".join(chunk)
            body = self._request(_QUOTE_PATH, {"symbols": joined}, symbol=joined)
            results = self._results(body, joined)
            quotes.extend(self._parse_quote(result) for result in results)
        return quotes

    def get_index_quote(self, index: str) -> Quote:
        """Current value of a B3 index (e.g. ``"^BVSP"``, ``"IFIX"``).

        Same endpoint as tickers; kept separate so callers read clearly and so a
        future index-specific quirk has a home.
        """
        return self.get_quote(index)

    def get_history(
        self,
        symbol: str,
        *,
        range_: str = "3mo",
        interval: str = "1d",
        start: str | None = None,
        end: str | None = None,
    ) -> list[HistPoint]:
        """Historical OHLCV bars, oldest first.

        ``range_`` and ``interval`` map to the API's ``range``/``interval``; the
        optional ``start``/``end`` (``YYYY-MM-DD``) map to ``startDate``/``endDate``.
        The API returns newest-first — this normalizes to chronological order.
        """
        params: dict[str, str] = {"symbols": symbol, "range": range_, "interval": interval}
        if start is not None:
            params["startDate"] = start
        if end is not None:
            params["endDate"] = end
        body = self._request(_HISTORY_PATH, params, symbol=symbol)
        results = self._results(body, symbol)
        prices = results[0].get("data", {}).get("historicalDataPrice") or []
        points = [self._parse_hist_point(p) for p in prices if p.get("close") is not None]
        points.sort(key=lambda point: point.date)
        return points

    # --- HTTP + error mapping ------------------------------------------

    def _request(self, path: str, params: dict[str, str], *, symbol: str) -> Any:
        if not self._token:
            raise MarketDataError(
                "BRAPI_TOKEN nao configurado; defina a variavel de ambiente ou o .env.",
                provider=_PROVIDER,
            )
        headers = {"Authorization": f"Bearer {self._token}"}
        resp: httpx.Response | None = None
        for attempt in range(self._max_retries):
            try:
                resp = self._client.get(path, params=params, headers=headers)
            except httpx.HTTPError as exc:
                raise NetworkError(_PROVIDER, str(exc)) from exc
            if resp.status_code == 429 and attempt < self._max_retries - 1:
                self._sleep(self._backoff_base * (2**attempt))
                continue
            break
        assert resp is not None  # loop runs at least once (max_retries >= 1)
        return self._decode(resp, symbol)

    def _decode(self, resp: httpx.Response, symbol: str) -> Any:
        try:
            body = json.loads(resp.text, parse_float=Decimal)
        except json.JSONDecodeError as exc:
            raise MarketDataError(f"Resposta invalida da brapi (HTTP {resp.status_code}).", provider=_PROVIDER) from exc
        if resp.status_code != 200 or (isinstance(body, dict) and body.get("error")):
            self._raise_api_error(resp.status_code, body, symbol)
        return body

    def _raise_api_error(self, status: int, body: Any, symbol: str) -> NoReturn:
        message = code = ""
        if isinstance(body, dict):
            message = str(body.get("message") or "")
            code = str(body.get("code") or "")
        if status == 429:
            raise RateLimitError(_PROVIDER)
        if status == 404 or code == "NOT_FOUND":
            raise QuoteNotFoundError(symbol, provider=_PROVIDER)
        raise MarketDataError(
            f"Erro da brapi: {message or code or f'HTTP {status}'}",
            provider=_PROVIDER,
            code=code,
        )

    # --- parsing --------------------------------------------------------

    def _results(self, body: Any, symbol: str) -> list[dict[str, Any]]:
        if not isinstance(body, dict) or not body.get("results"):
            raise QuoteNotFoundError(symbol, provider=_PROVIDER)
        return body["results"]

    def _parse_quote(self, result: dict[str, Any]) -> Quote:
        data = result.get("data") or {}
        price = data.get("regularMarketPrice")
        resolved = str(result.get("symbol") or "")
        requested = str(result.get("requestedSymbol") or resolved)
        if price is None:
            raise QuoteNotFoundError(requested or resolved, provider=_PROVIDER)
        return Quote(
            symbol=resolved,
            requested_symbol=requested,
            price=_dec(price),
            currency=str(data.get("currency") or ""),
            time=_parse_time(data.get("regularMarketTime")),
            previous_close=_dec_opt(data.get("regularMarketPreviousClose")),
            change=_dec_opt(data.get("regularMarketChange")),
            change_percent=_dec_opt(data.get("regularMarketChangePercent")),
        )

    def _parse_hist_point(self, point: dict[str, Any]) -> HistPoint:
        return HistPoint(
            date=datetime.fromtimestamp(int(point["date"]), tz=UTC),
            open=_dec(point["open"]),
            high=_dec(point["high"]),
            low=_dec(point["low"]),
            close=_dec(point["close"]),
            volume=int(point.get("volume") or 0),
            adjusted_close=_dec_opt(point.get("adjustedClose")),
        )
