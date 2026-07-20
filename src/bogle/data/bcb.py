"""Client for the Banco Central do Brasil time-series API (SGS).

The only free source of CDI / IPCA / SELIC series (brapi's macro endpoint is 403
on the free plan). Endpoint:
``GET https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados?formato=json``
with optional ``dataInicial``/``dataFinal`` in ``dd/mm/yyyy``.

Wire format (confirmed live 2026-07-20): a JSON array of
``{"data": "dd/mm/yyyy", "valor": "<number-as-string>"}``. Rate series come in
*percent* (CDI/SELIC per business day, IPCA per month), so values are converted to
a fraction (0.055131% -> 0.00055131) unless ``as_fraction=False``.

Two quirks drive the error handling:
- An invalid series code returns **HTTP 200 with an HTML error page**, not JSON —
  so a body that does not parse as a JSON list is treated as an error, regardless
  of status.
- SGS caps a *dated* request at ~10 years, so wide windows are split and stitched.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

from bogle.data.cache import DiskCache
from bogle.data.models import SeriesPoint
from bogle.domain.errors import MarketDataError, NetworkError

_PROVIDER = "bcb"
_BASE_URL = "https://api.bcb.gov.br"
_SERIES_PATH = "/dados/serie/bcdata.sgs.{code}/dados"
_API_DATE_FMT = "%d/%m/%Y"

# SGS series codes.
CDI_CODE = 12  # CDI, % per business day
IPCA_CODE = 433  # IPCA, % per month
SELIC_CODE = 11  # SELIC, % per business day (432 is the Copom meta, % a.a.)

_DEFAULT_TIMEOUT = 15.0
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_BACKOFF_BASE = 0.5
_DEFAULT_CACHE_TTL = 24 * 60 * 60  # 24h — these series update at most daily
_MAX_WINDOW_YEARS = 10  # SGS rejects dated requests wider than ~10 years


def _windows(start: date, end: date, max_years: int) -> Iterator[tuple[date, date]]:
    """Split ``[start, end]`` into consecutive spans of at most ``max_years``."""
    cursor = start
    while cursor <= end:
        try:
            stop = cursor.replace(year=cursor.year + max_years) - timedelta(days=1)
        except ValueError:  # cursor is Feb 29 and the target year is not a leap year
            stop = cursor.replace(year=cursor.year + max_years, day=28) - timedelta(days=1)
        yield cursor, min(stop, end)
        cursor = min(stop, end) + timedelta(days=1)


class BcbClient:
    """Synchronous SGS client with a 24h disk cache.

    Usable as a context manager. Injecting a ``cache`` (with a temp ``base_dir``)
    and a no-op ``sleep`` keeps tests off the real disk and out of real backoff.
    """

    def __init__(
        self,
        *,
        base_url: str = _BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        backoff_base: float = _DEFAULT_BACKOFF_BASE,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        cache: DiskCache | None = None,
        cache_ttl: float = _DEFAULT_CACHE_TTL,
    ) -> None:
        self._max_retries = max(1, max_retries)
        self._backoff_base = backoff_base
        self._sleep = sleep
        self._cache = cache if cache is not None else DiskCache(_PROVIDER)
        self._cache_ttl = cache_ttl
        self._owns_client = client is None
        self._client = client if client is not None else httpx.Client(base_url=base_url, timeout=timeout)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> BcbClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # --- public API -----------------------------------------------------

    def get_series(
        self,
        code: int,
        start: date | None = None,
        end: date | None = None,
        *,
        as_fraction: bool = True,
    ) -> list[SeriesPoint]:
        """Observations of SGS series ``code``, oldest first.

        ``start``/``end`` are calendar dates; omit both to fetch the whole series.
        Values are returned as fractions unless ``as_fraction=False``.
        """
        raw = self._fetch_raw(code, start, end)
        points = [self._parse_point(item, as_fraction=as_fraction) for item in raw]
        points.sort(key=lambda point: point.date)
        return points

    def get_cdi(self, start: date | None = None, end: date | None = None) -> list[SeriesPoint]:
        """CDI, % per business day expressed as a daily fraction (series 12)."""
        return self.get_series(CDI_CODE, start, end)

    def get_ipca(self, start: date | None = None, end: date | None = None) -> list[SeriesPoint]:
        """IPCA, monthly variation expressed as a fraction (series 433)."""
        return self.get_series(IPCA_CODE, start, end)

    def get_selic(self, start: date | None = None, end: date | None = None) -> list[SeriesPoint]:
        """SELIC, % per business day expressed as a daily fraction (series 11).

        Series 11 is the realized daily SELIC (used for daily capitalization); the
        Copom meta (432, % a.a.) is a different series — fetch it via ``get_series``.
        """
        return self.get_series(SELIC_CODE, start, end)

    # --- fetch + cache + pagination ------------------------------------

    def _fetch_raw(self, code: int, start: date | None, end: date | None) -> list[dict[str, Any]]:
        cache_key = f"{code}:{start.isoformat() if start else ''}:{end.isoformat() if end else ''}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        raw: list[dict[str, Any]] = []
        if start is not None and end is not None:
            for window_start, window_end in _windows(start, end, _MAX_WINDOW_YEARS):
                raw.extend(self._request_window(code, window_start, window_end))
        else:
            raw.extend(self._request_window(code, start, end))
        self._cache.set(cache_key, raw, self._cache_ttl)
        return raw

    def _request_window(self, code: int, start: date | None, end: date | None) -> list[dict[str, Any]]:
        params = {"formato": "json"}
        if start is not None:
            params["dataInicial"] = start.strftime(_API_DATE_FMT)
        if end is not None:
            params["dataFinal"] = end.strftime(_API_DATE_FMT)
        text = self._http_get(_SERIES_PATH.format(code=code), params)
        try:
            body = json.loads(text)
        except json.JSONDecodeError as exc:
            # SGS answers an unknown code with HTTP 200 + an HTML error page.
            raise MarketDataError(
                f"BCB SGS nao retornou JSON para a serie {code} (codigo inexistente?).",
                provider=_PROVIDER,
            ) from exc
        if not isinstance(body, list):
            raise MarketDataError(f"Formato inesperado do BCB SGS para a serie {code}.", provider=_PROVIDER)
        return body

    def _http_get(self, path: str, params: dict[str, str]) -> str:
        resp: httpx.Response | None = None
        for attempt in range(self._max_retries):
            try:
                resp = self._client.get(path, params=params)
            except httpx.HTTPError as exc:
                raise NetworkError(_PROVIDER, str(exc)) from exc
            if resp.status_code == 429 and attempt < self._max_retries - 1:
                self._sleep(self._backoff_base * (2**attempt))
                continue
            break
        assert resp is not None  # loop runs at least once (max_retries >= 1)
        if resp.status_code != 200:
            raise MarketDataError(f"BCB SGS retornou HTTP {resp.status_code}.", provider=_PROVIDER)
        return resp.text

    def _parse_point(self, item: dict[str, Any], *, as_fraction: bool) -> SeriesPoint:
        value = Decimal(str(item["valor"]))
        if as_fraction:
            value = value / 100
        return SeriesPoint(date=datetime.strptime(item["data"], _API_DATE_FMT).date(), value=value)
