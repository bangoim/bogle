"""Client for Tesouro Direto prices via the Tesouro Transparente open data.

The direct Tesouro Direto API (``treasurybondsinfo.json``) now sits behind a
Cloudflare challenge and is unreachable from a plain HTTP client, so this uses the
official open-data CSV "Precos e Taxas dos Titulos Publicos" instead. Two
consequences: the file is the *full history* (~14 MB, no server-side filter), and
prices are the "manha" (morning) values for the most recent business day — D-1,
not intraday. Fine for passive rebalancing.

Wire format (``;``-delimited, confirmed live 2026-07-20):
``Tipo Titulo;Data Vencimento;Data Base;Taxa Compra Manha;Taxa Venda Manha;
PU Compra Manha;PU Venda Manha;PU Base Manha`` — numbers use a decimal comma
(``5962,51``) and dates are ``dd/mm/yyyy``. Columns are located by header keyword,
not position, to tolerate small layout changes.

Only the latest snapshot (rows on the most recent ``Data Base``) is parsed and
cached; the 14 MB file itself is never cached. The TTL is long because the data
changes at most once per business day.
"""

from __future__ import annotations

import csv
import io
import time
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import httpx

from bogle.data.cache import DiskCache
from bogle.data.models import TesouroQuote
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


def _title_name(bond_type: str, maturity: date) -> str:
    return f"{bond_type} {maturity.year}"


def _normalize(name: str) -> str:
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
    """Downloads and parses the Tesouro Transparente CSV, caching the latest snapshot.

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
    ) -> None:
        self._max_retries = max(1, max_retries)
        self._backoff_base = backoff_base
        self._sleep = sleep
        self._cache = cache if cache is not None else DiskCache(_PROVIDER)
        self._cache_ttl = cache_ttl
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
        target = _normalize(title)
        for entry in self._snapshot():
            if _normalize(entry["title"]) == target:
                return self._to_quote(entry)
        raise QuoteNotFoundError(title, provider=_PROVIDER)

    # --- fetch + cache + parse -----------------------------------------

    def _snapshot(self) -> list[dict[str, Any]]:
        cached = self._cache.get(_CACHE_KEY)
        if cached is not None:
            return cached
        snapshot = self._reduce_to_latest(self._download_csv())
        self._cache.set(_CACHE_KEY, snapshot, self._cache_ttl)
        return snapshot

    def _download_csv(self) -> str:
        headers = {"User-Agent": _USER_AGENT}
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
        if resp.status_code != 200:
            raise MarketDataError(f"Tesouro Transparente retornou HTTP {resp.status_code}.", provider=_PROVIDER)
        return resp.text

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
            raise MarketDataError("CSV do Tesouro sem linhas validas.", provider=_PROVIDER)

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
