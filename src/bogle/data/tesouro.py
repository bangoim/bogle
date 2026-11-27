"""Client for Tesouro Direto prices via the Tesouro Transparente open data.

The direct Tesouro Direto API (``treasurybondsinfo.json``) now sits behind a
Cloudflare challenge and is unreachable from a plain HTTP client, so this uses the
official open-data CSV "Precos e Taxas dos Titulos Publicos" instead. Two
consequences: the file is the *full history* (~15 MB, no server-side filter), and
prices are the "manha" (morning) values for the most recent business day — D-1,
not intraday. Fine for passive rebalancing.

Wire format (``;``-delimited, confirmed live 2026-07-20):
``Tipo Titulo;Data Vencimento;Data Base;Taxa Compra Manha;Taxa Venda Manha;
PU Compra Manha;PU Venda Manha;PU Base Manha`` — numbers use a decimal comma
(``5962,51``) and dates are ``dd/mm/yyyy``. Columns are located by header keyword,
not position, to tolerate small layout changes.

The file is downloaded at most once a day and kept on disk (``csv`` entry of the
cache, ~15 MB) with the validators the server sent (``ETag``, ``Last-Modified``).
Everything else is read from that copy: the snapshot of the latest ``Data Base``
(:meth:`TesouroClient.get_quote`, :meth:`TesouroClient.list_titles`) and the daily
series of one title (:meth:`TesouroClient.get_series`, issue #87).

The server only publishes D-1 in the afternoon. A copy whose latest ``Data Base``
is older than the previous business day may be asked about again, conditionally
(``If-None-Match`` / ``If-Modified-Since``): a 304 keeps the file, a 200 replaces
it. That recheck is throttled to one every 30 minutes and one per client instance.
"""

from __future__ import annotations

import csv
import io
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from bogle.analytics.business_days import previous_business_day
from bogle.data.cache import DiskCache
from bogle.data.models import SeriesPoint, TesouroQuote
from bogle.domain.errors import MarketDataError, NetworkError, QuoteNotFoundError

_PROVIDER = "tesouro"
_BASE_URL = "https://www.tesourotransparente.gov.br"
_CSV_PATH = (
    "/ckan/dataset/df56aa42-484a-4a59-8184-7676580c81e3"
    "/resource/796d2059-14e9-44e3-80c9-2d9e30b405c1/download/PrecoTaxaTesouroDireto.csv"
)
_API_DATE_FMT = "%d/%m/%Y"
_USER_AGENT = "Mozilla/5.0 (compatible; bogle)"

_DEFAULT_TIMEOUT = 60.0  # the CSV is ~14 MB
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_BACKOFF_BASE = 0.5
_DEFAULT_CACHE_TTL = 6 * 60 * 60  # 6h — data updates about once per business day
_CACHE_KEY = "latest-snapshot"
_CSV_KEY = "csv"
_CSV_TTL = 2 * 24 * 60 * 60  # the entry is only used on the day it was fetched; this is housekeeping
_RECHECK_EVERY = timedelta(minutes=30)

_REQUIRED_COLUMNS = ("tipo", "maturity", "base_date")


def _parse_decimal(raw: str | None) -> Decimal | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    # Brazilian number format: '.' groups thousands, ',' is the decimal separator.
    return Decimal(text.replace(".", "").replace(",", "."))


def _parse_rate(raw: str | None) -> Decimal | None:
    value = _parse_decimal(raw)
    return value / 100 if value is not None else None


def _parse_date(raw: str) -> date:
    return datetime.strptime(raw.strip(), _API_DATE_FMT).date()


def mark_price(pu_venda: Decimal | None, pu_base: Decimal | None) -> Decimal | None:
    """The unit price a title is marked at: the redemption price (``PU Venda``).

    A title on the eve of its maturity publishes ``PU Venda = 0,00``; ``PU Base``
    stands in for it. ``None`` when neither is a positive price, which is not a
    price at all (and ``price_history`` refuses it). The Position and the daily
    series use this one rule, so the same date reads the same PU.
    """
    price = pu_venda or pu_base
    return price if price is not None and price > 0 else None


def _title_name(bond_type: str, maturity: date) -> str:
    return f"{bond_type} {maturity.year}"


def normalize_title(name: str) -> str:
    return " ".join(name.split()).casefold()


def _column_index(header: list[str]) -> dict[str, int]:
    idx: dict[str, int] = {}
    for position, column in enumerate(header):
        name = column.strip().lower()
        if "tipo" in name:
            idx["tipo"] = position
        elif "data" in name and "vencimento" in name:
            idx["maturity"] = position
        elif "data" in name and "base" in name:
            idx["base_date"] = position
        elif "pu" in name and "compra" in name:
            idx["pu_compra"] = position
        elif "pu" in name and "venda" in name:
            idx["pu_venda"] = position
        elif "pu" in name and "base" in name:
            idx["pu_base"] = position
        elif "taxa" in name and "compra" in name:
            idx["rate_compra"] = position
        elif "taxa" in name and "venda" in name:
            idx["rate_venda"] = position
    missing = [c for c in _REQUIRED_COLUMNS if c not in idx]
    if missing:
        raise MarketDataError(f"CSV do Tesouro sem colunas esperadas: {missing}.", provider=_PROVIDER)
    return idx


def _cell(row: list[str], idx: dict[str, int], key: str) -> str | None:
    position = idx.get(key)
    if position is None or position >= len(row):
        return None
    return row[position].strip() or None


class TesouroClient:
    """Downloads and parses the Tesouro Transparente CSV, keeping the day's copy on disk.

    Usable as a context manager. Injecting a ``cache`` (temp ``base_dir``) and a
    no-op ``sleep`` keeps tests off the real disk and out of real backoff; the HTTP
    download is mocked with respx.
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
        today: Callable[[], date] = date.today,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._max_retries = max(1, max_retries)
        self._backoff_base = backoff_base
        self._sleep = sleep
        self._cache = cache if cache is not None else DiskCache(_PROVIDER)
        self._cache_ttl = cache_ttl
        self._today = today
        self._now = now
        # Per instance: a screen builds a new client, so the next screen tries again.
        self._text: str | None = None
        self._failure: MarketDataError | None = None
        self._series: dict[str, list[SeriesPoint]] = {}
        self._owns_client = client is None
        self._client = (
            client if client is not None else httpx.Client(base_url=base_url, timeout=timeout, follow_redirects=True)
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> TesouroClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # --- public API -----------------------------------------------------

    def list_titles(self) -> list[str]:
        """Canonical names of the titles offered on the latest ``Data Base``."""
        return sorted(entry["title"] for entry in self._snapshot())

    def get_quote(self, title: str) -> TesouroQuote:
        """Latest quote for ``title`` (e.g. ``"Tesouro IPCA+ 2035"``).

        Matching ignores case and extra whitespace. Raises
        :class:`QuoteNotFoundError` if no current title matches.
        """
        target = normalize_title(title)
        for entry in self._snapshot():
            if normalize_title(entry["title"]) == target:
                return self._to_quote(entry)
        raise QuoteNotFoundError(title, provider=_PROVIDER)

    def get_series(self, title: str) -> list[SeriesPoint]:
        """Daily price of ``title``, oldest first (issue #87).

        The value is :func:`mark_price` of each ``Data Base`` — the same PU
        :meth:`get_quote` gives for the latest one. Days without a positive price
        are skipped. A name published with several maturities (the old titles,
        before 2014) keeps the one of its newest ``Data Base``.

        Matching ignores case and extra whitespace. Raises
        :class:`QuoteNotFoundError` when the file has no row of the title, and
        :class:`MarketDataError` / :class:`NetworkError` when the file cannot be had.
        """
        target = normalize_title(title)
        if target not in self._series:
            self._series[target] = self._read_series(target, title)
        return list(self._series[target])

    # --- fetch + cache + parse -----------------------------------------

    def _snapshot(self) -> list[dict[str, Any]]:
        cached = self._cache.get(_CACHE_KEY)
        if cached is not None:
            return cached
        snapshot = self._reduce_to_latest(self._csv_of_the_day())
        self._cache.set(_CACHE_KEY, snapshot, self._cache_ttl)
        return snapshot

    def _csv_of_the_day(self) -> str:
        """The CSV text: this instance's memo, else the disk copy of today, else the server.

        One download a day. A failed attempt is remembered by the instance (the
        next one retries) and nothing is stamped on disk.
        """
        if self._text is not None:
            return self._text
        if self._failure is not None:
            raise self._failure
        today = self._today()
        entry = self._cache.get(_CSV_KEY)
        if isinstance(entry, dict) and entry.get("fetched_on") == today.isoformat():
            entry = self._recheck(entry, today)
        else:
            try:
                entry = self._download(today)
            except MarketDataError as exc:
                self._failure = exc
                raise
        text: str = entry["text"]
        self._text = text
        return text

    def _recheck(self, entry: dict[str, Any], today: date) -> dict[str, Any]:
        """Ask the server whether a copy without D-1 has changed; keep it on any doubt."""
        if date.fromisoformat(entry["latest_base"]) >= previous_business_day(today):
            return entry
        headers = {}
        if entry.get("etag"):
            headers["If-None-Match"] = entry["etag"]
        if entry.get("last_modified"):
            headers["If-Modified-Since"] = entry["last_modified"]
        now = self._now()
        if not headers or now - datetime.fromisoformat(entry["checked_at"]) < _RECHECK_EVERY:
            return entry
        try:
            resp = self._request(headers)
        except NetworkError:
            return entry
        if resp.status_code == 304:
            entry = {**entry, "checked_at": now.isoformat()}
            self._cache.set(_CSV_KEY, entry, _CSV_TTL)
            return entry
        if resp.status_code == 200:
            try:
                return self._keep(resp, today)
            except MarketDataError:
                return entry
        return entry

    def _download(self, today: date) -> dict[str, Any]:
        resp = self._request({})
        if resp.status_code != 200:
            raise MarketDataError(f"Tesouro Transparente retornou HTTP {resp.status_code}.", provider=_PROVIDER)
        return self._keep(resp, today)

    def _keep(self, resp: httpx.Response, today: date) -> dict[str, Any]:
        """Write a 200 answer to disk, as the day's file and as the latest snapshot.

        Parsed first: a body that is not the CSV is never written.
        """
        text = resp.text
        snapshot = self._reduce_to_latest(text)
        entry = {
            "fetched_on": today.isoformat(),
            "checked_at": self._now().isoformat(),
            "etag": resp.headers.get("etag"),
            "last_modified": resp.headers.get("last-modified"),
            "latest_base": snapshot[0]["base_date"],
            "text": text,
        }
        self._cache.set(_CSV_KEY, entry, _CSV_TTL)
        self._cache.set(_CACHE_KEY, snapshot, self._cache_ttl)
        return entry

    def _request(self, extra_headers: dict[str, str]) -> httpx.Response:
        headers = {"User-Agent": _USER_AGENT, **extra_headers}
        resp: httpx.Response | None = None
        for attempt in range(self._max_retries):
            try:
                resp = self._client.get(_CSV_PATH, headers=headers)
            except httpx.HTTPError as exc:
                raise NetworkError(_PROVIDER, str(exc)) from exc
            if resp.status_code == 429 and attempt < self._max_retries - 1:
                self._sleep(self._backoff_base * (2**attempt))
                continue
            break
        assert resp is not None  # loop runs at least once (max_retries >= 1)
        return resp

    def _read_series(self, target: str, title: str) -> list[SeriesPoint]:
        rows = csv.reader(io.StringIO(self._csv_of_the_day()), delimiter=";")
        try:
            header = next(rows)
        except StopIteration:
            raise MarketDataError("CSV do Tesouro vazio.", provider=_PROVIDER) from None
        idx = _column_index(header)

        # Matched on the name alone first: parsing the dates and prices of all
        # ~179k rows is the slow part, and only one title's are wanted.
        found: list[tuple[date, str, list[str]]] = []
        for row in rows:
            bond_type = _cell(row, idx, "tipo")
            maturity_raw = _cell(row, idx, "maturity")
            base_raw = _cell(row, idx, "base_date")
            if bond_type is None or maturity_raw is None or base_raw is None:
                continue
            if normalize_title(f"{bond_type} {maturity_raw[-4:]}") != target:
                continue
            try:
                found.append((_parse_date(base_raw), maturity_raw, row))
            except ValueError:
                continue
        if not found:
            raise QuoteNotFoundError(title, provider=_PROVIDER)

        newest_maturity = max(found, key=lambda item: item[0])[1]
        by_day: dict[date, Decimal] = {}
        for base_date, maturity_raw, row in found:
            if maturity_raw != newest_maturity:
                continue
            try:
                price = mark_price(
                    _parse_decimal(_cell(row, idx, "pu_venda")), _parse_decimal(_cell(row, idx, "pu_base"))
                )
            except InvalidOperation:
                continue
            if price is not None:
                by_day[base_date] = price
        return [SeriesPoint(day, by_day[day]) for day in sorted(by_day)]

    def _reduce_to_latest(self, text: str) -> list[dict[str, Any]]:
        rows = csv.reader(io.StringIO(text), delimiter=";")
        try:
            header = next(rows)
        except StopIteration:
            raise MarketDataError("CSV do Tesouro vazio.", provider=_PROVIDER) from None
        idx = _column_index(header)

        parsed: list[tuple[date, list[str]]] = []
        latest: date | None = None
        for row in rows:
            base_raw = _cell(row, idx, "base_date")
            if base_raw is None:
                continue
            try:
                base_date = _parse_date(base_raw)
            except ValueError:
                continue
            parsed.append((base_date, row))
            if latest is None or base_date > latest:
                latest = base_date
        if latest is None:
            raise MarketDataError("CSV do Tesouro sem linhas válidas.", provider=_PROVIDER)

        snapshot: list[dict[str, Any]] = []
        for base_date, row in parsed:
            if base_date != latest:
                continue
            bond_type = (_cell(row, idx, "tipo") or "").strip()
            maturity = _parse_date(row[idx["maturity"]])
            snapshot.append(
                {
                    "title": _title_name(bond_type, maturity),
                    "bond_type": bond_type,
                    "maturity": maturity.isoformat(),
                    "base_date": latest.isoformat(),
                    "pu_compra": _cell(row, idx, "pu_compra"),
                    "pu_venda": _cell(row, idx, "pu_venda"),
                    "pu_base": _cell(row, idx, "pu_base"),
                    "rate_compra": _cell(row, idx, "rate_compra"),
                    "rate_venda": _cell(row, idx, "rate_venda"),
                }
            )
        return snapshot

    def _to_quote(self, entry: dict[str, Any]) -> TesouroQuote:
        return TesouroQuote(
            title=entry["title"],
            bond_type=entry["bond_type"],
            maturity=date.fromisoformat(entry["maturity"]),
            base_date=date.fromisoformat(entry["base_date"]),
            pu_compra=_parse_decimal(entry["pu_compra"]),
            pu_venda=_parse_decimal(entry["pu_venda"]),
            pu_base=_parse_decimal(entry["pu_base"]),
            rate_compra=_parse_rate(entry["rate_compra"]),
            rate_venda=_parse_rate(entry["rate_venda"]),
        )
