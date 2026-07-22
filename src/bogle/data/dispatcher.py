"""Price dispatcher (issue #18): one entry point that prices any asset.

``get_price(asset)`` routes by ``asset_type`` and returns a ``Decimal``:

- STOCK/BDR/FII/ETF -> brapi quote (per share), falling back to yfinance (``.SA``
  for B3) when brapi fails.
- TESOURO -> the redemption unit price (``pu_venda``, mark-to-market).
- CDB/RDB/LCI/LCA/CAIXINHA -> the gross corrected value of ``principal`` via the
  fixed-income present-value engine (BCB series fetched for the period).

The first two return a *per-unit* price (multiply by the holding's shares); the
fixed-income branch returns the value of the given ``principal`` — and since a
private fixed-income holding uses the ``shares = 1`` convention, ``shares *
get_price(...)`` stays uniform across every type.

Quotes are cached on disk with a short TTL (5 min) so repeated runs within a
window do not re-hit the quote APIs; BCB/Tesouro already cache internally.

Clients are accepted as structural protocols so tests inject fakes without a
network.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol

from bogle.data.cache import DiskCache
from bogle.data.fixed_income import accumulated_ipca_factor, accumulated_rate_factor, present_value
from bogle.data.models import HistPoint, Quote, SeriesPoint, TesouroQuote

if TYPE_CHECKING:
    # Imported lazily at call time to avoid an import cycle (analytics.twr imports
    # data.models, which triggers this package's __init__).
    from bogle.analytics.twr import Valuator
from bogle.domain.assets import (
    PRIVATE_FIXED_INCOME_TYPES,
    VARIABLE_INCOME_TYPES,
    Asset,
    AssetType,
    Indexer,
)
from bogle.domain.errors import MarketDataError, QuoteNotFoundError

_ZERO = Decimal("0")
_QUOTE_TTL = 5 * 60  # intraday quotes: 5 minutes
_SERIES_LOOKBACK_DAYS = 120  # enough recent BCB history for an index point-in-time read

# Macro rate series served by the BCB client.
_BCB_INDEXES = frozenset({"CDI", "SELIC", "IPCA"})
# Market index name -> brapi symbol (see #18 notes: ^BVSP with caret, others without).
_INDEX_SYMBOLS = {"IBOV": "^BVSP", "IBOVESPA": "^BVSP", "IFIX": "IFIX", "SMLL": "SMLL", "IDIV": "IDIV"}
# Market index name -> yfinance symbol, for long history (accumulated returns).
# B3 sector indices (IFIX/SMLL/IDIV) have no reliable free history: unmapped
# names fall back to the ticker rule (``.SA``) and fail with a friendly error.
_YAHOO_INDEX_SYMBOLS = {"IBOV": "^BVSP", "IBOVESPA": "^BVSP"}


class QuoteSource(Protocol):
    def get_quote(self, symbol: str) -> Quote: ...


class HistorySource(Protocol):
    def get_quote(self, symbol: str) -> Quote: ...
    def get_history(
        self, symbol: str, *, range_: str = ..., interval: str = ..., start: str | None = ..., end: str | None = ...
    ) -> list[HistPoint]: ...


class IndexSource(Protocol):
    def get_index_quote(self, index: str) -> Quote: ...


class TesouroSource(Protocol):
    def get_quote(self, title: str) -> TesouroQuote: ...


class BrapiLike(QuoteSource, IndexSource, Protocol):
    """brapi exposes both quote and index-quote lookups."""


class SeriesSource(Protocol):
    def get_cdi(self, start: date | None = None, end: date | None = None) -> list[SeriesPoint]: ...
    def get_selic(self, start: date | None = None, end: date | None = None) -> list[SeriesPoint]: ...
    def get_ipca(self, start: date | None = None, end: date | None = None) -> list[SeriesPoint]: ...


def _yahoo_symbol(ticker: str) -> str:
    """brapi ticker -> yfinance symbol (B3 tickers need the ``.SA`` suffix)."""
    return ticker if "." in ticker else f"{ticker}.SA"


def _as_date(value: date) -> date:
    return value.date() if isinstance(value, datetime) else value


def _latest_on_or_before(points: Sequence[SeriesPoint], on: date) -> Decimal | None:
    best: SeriesPoint | None = None
    for point in points:
        if point.date <= on and (best is None or point.date > best.date):
            best = point
    return best.value if best is not None else None


def _close_on_or_before(history: Sequence[HistPoint], on: date) -> Decimal | None:
    """Close of the latest bar dated on or before ``on`` (weekend/holiday rule)."""
    best_close: Decimal | None = None
    best_date: date | None = None
    for point in history:
        point_date = _as_date(point.date)
        if point_date <= on and (best_date is None or point_date > best_date):
            best_date, best_close = point_date, point.close
    return best_close


@dataclass(frozen=True, slots=True)
class PriceInfo:
    """A price plus its provenance, for the position footer.

    ``source`` is ``"brapi"`` / ``"yfinance"`` / ``"tesouro"`` / ``"calculado"``;
    ``as_of`` is the quote's timestamp (``None`` for a computed fixed-income value).
    """

    price: Decimal
    source: str
    as_of: datetime | None = None


def _price_info_to_cache(info: PriceInfo) -> dict[str, Any]:
    return {"price": str(info.price), "source": info.source, "as_of": info.as_of.isoformat() if info.as_of else None}


def _price_info_from_cache(data: dict[str, Any]) -> PriceInfo:
    raw = data.get("as_of")
    return PriceInfo(Decimal(data["price"]), data["source"], datetime.fromisoformat(raw) if raw else None)


class PriceDispatcher:
    def __init__(
        self,
        *,
        brapi: BrapiLike,
        yfinance: HistorySource,
        tesouro: TesouroSource,
        bcb: SeriesSource,
        quote_cache: DiskCache | None = None,
        quote_ttl: float = _QUOTE_TTL,
        clock: Callable[[], date] | None = None,
    ) -> None:
        self._brapi = brapi
        self._yfinance = yfinance
        self._tesouro = tesouro
        self._bcb = bcb
        self._cache = quote_cache if quote_cache is not None else DiskCache("quotes")
        self._quote_ttl = quote_ttl
        self._today = clock if clock is not None else date.today

    # --- prices ---------------------------------------------------------

    def get_price(self, asset: Asset, *, principal: Decimal | None = None, on_date: date | None = None) -> Decimal:
        return self.get_price_info(asset, principal=principal, on_date=on_date).price

    def get_price_info(
        self, asset: Asset, *, principal: Decimal | None = None, on_date: date | None = None
    ) -> PriceInfo:
        if asset.asset_type in VARIABLE_INCOME_TYPES:
            return self._variable_income_info(asset.ticker)
        if asset.asset_type is AssetType.TESOURO:
            return self._tesouro_info(asset.ticker)
        if asset.asset_type in PRIVATE_FIXED_INCOME_TYPES:
            return PriceInfo(self._fixed_income_value(asset, principal, on_date), "calculado", None)
        raise ValueError(f"tipo de ativo sem preco: {asset.asset_type}")

    def _variable_income_info(self, ticker: str) -> PriceInfo:
        key = f"quote:{ticker}"
        cached = self._cache.get(key)
        if cached is not None:
            return _price_info_from_cache(cached)
        try:
            quote, source = self._brapi.get_quote(ticker), "brapi"
        except MarketDataError:
            # brapi failed (not found / network / plan limit) -> Yahoo fallback.
            quote, source = self._yfinance.get_quote(_yahoo_symbol(ticker)), "yfinance"
        info = PriceInfo(quote.price, source, quote.time)
        self._cache.set(key, _price_info_to_cache(info), self._quote_ttl)
        return info

    def _tesouro_info(self, title: str) -> PriceInfo:
        quote = self._tesouro.get_quote(title)
        price = quote.pu_venda or quote.pu_base  # mark-to-market = redemption price
        if price is None:
            raise QuoteNotFoundError(title, provider="tesouro")
        base = quote.base_date
        return PriceInfo(price, "tesouro", datetime(base.year, base.month, base.day))

    def _fixed_income_value(self, asset: Asset, principal: Decimal | None, on_date: date | None) -> Decimal:
        if principal is None:
            raise ValueError("principal e obrigatorio para precificar renda fixa privada.")
        if asset.purchase_date is None or asset.rate is None:
            raise ValueError(f"ativo de renda fixa '{asset.ticker}' sem purchase_date/rate.")
        start = _as_date(asset.purchase_date)
        end = on_date if on_date is not None else self._today()
        cdi: Sequence[SeriesPoint] = ()
        selic: Sequence[SeriesPoint] = ()
        ipca: Sequence[SeriesPoint] = ()
        if not asset.is_prefixed:
            if asset.indexer in (Indexer.CDI, Indexer.CDI_PLUS):
                cdi = self._bcb.get_cdi(start, end)
            elif asset.indexer is Indexer.SELIC:
                selic = self._bcb.get_selic(start, end)
            elif asset.indexer is Indexer.IPCA_PLUS:
                ipca = self._bcb.get_ipca(date(start.year, start.month, 1), end)
        return present_value(
            principal,
            indexer=asset.indexer,
            rate=asset.rate,
            is_prefixed=bool(asset.is_prefixed),
            purchase_date=start,
            on_date=end,
            cdi=cdi,
            selic=selic,
            ipca=ipca,
        )

    # --- indices --------------------------------------------------------

    def get_index_value(self, index: str, on_date: date | None = None) -> Decimal:
        """Point-in-time value of an index.

        CDI/SELIC/IPCA return the BCB series value (a fraction) on or before
        ``on_date``; market indices (IBOV/IFIX/SMLL/IDIV) return the brapi quote
        price. Accumulation for benchmark comparisons is a later concern (epic 8).
        """
        key = index.upper()
        if key in _BCB_INDEXES:
            end = on_date if on_date is not None else self._today()
            fetch = {"CDI": self._bcb.get_cdi, "SELIC": self._bcb.get_selic, "IPCA": self._bcb.get_ipca}[key]
            points = fetch(end - timedelta(days=_SERIES_LOOKBACK_DAYS), end)
            value = _latest_on_or_before(points, end)
            if value is None:
                raise QuoteNotFoundError(index, provider="bcb")
            return value
        return self._brapi.get_index_quote(_INDEX_SYMBOLS.get(key, index)).price

    # --- accumulated index returns (issue #67) --------------------------

    def get_index_return(self, index: str, start: date, end: date) -> Decimal:
        """Accumulated return of an index over ``[start, end]``, as a fraction.

        CDI/SELIC compound the daily BCB series; IPCA composes the monthly
        variation (same engine as the fixed-income present value). Market
        indices/tickers use the first and last close of the yfinance history —
        indices without free history (IFIX/SMLL/IDIV) raise a friendly error.
        """
        key = index.upper()
        if key == "IPCA":
            points = self._bcb.get_ipca(date(start.year, start.month, 1), end)
            if not points:
                raise QuoteNotFoundError(index, provider="bcb")
            return accumulated_ipca_factor(points, start, end) - Decimal("1")
        if key in _BCB_INDEXES:
            fetch = self._bcb.get_cdi if key == "CDI" else self._bcb.get_selic
            points = fetch(start, end)
            if not points:
                raise QuoteNotFoundError(index, provider="bcb")
            return accumulated_rate_factor(points, start, end, name=key) - Decimal("1")
        history = self._index_history(key, start, end)
        first = _close_on_or_before(history, start)
        last = _close_on_or_before(history, end)
        if first is None or last is None or first == _ZERO:
            raise MarketDataError(
                f"Sem historico de '{key}' no inicio do periodo ({start.isoformat()}); "
                "as fontes gratuitas nao cobrem esse indice/janela.",
                provider="yfinance",
            )
        return last / first - Decimal("1")

    def get_index_series(self, index: str, grid: Sequence[date]) -> list[Decimal]:
        """Index level at each grid date (for base-100 charts).

        Rate indices (CDI/SELIC/IPCA) return the growth factor accumulated from
        the first grid date (base 1); market indices return the close on or
        before each date.
        """
        if not grid:
            return []
        key = index.upper()
        start, end = grid[0], grid[-1]
        if key == "IPCA":
            points = self._bcb.get_ipca(date(start.year, start.month, 1), end)
            if not points:
                raise QuoteNotFoundError(index, provider="bcb")
            return [accumulated_ipca_factor(points, start, on) for on in grid]
        if key in _BCB_INDEXES:
            fetch = self._bcb.get_cdi if key == "CDI" else self._bcb.get_selic
            points = fetch(start, end)
            if not points:
                raise QuoteNotFoundError(index, provider="bcb")
            return [accumulated_rate_factor(points, start, on, name=key) for on in grid]
        history = self._index_history(key, start, end)
        levels = []
        for on in grid:
            close = _close_on_or_before(history, on)
            if close is None:
                raise MarketDataError(
                    f"Sem historico de '{key}' em {on.isoformat()}; as fontes gratuitas nao cobrem esse indice/janela.",
                    provider="yfinance",
                )
            levels.append(close)
        return levels

    def _index_history(self, key: str, start: date, end: date) -> list[HistPoint]:
        # Pad the fetch a week back so "close on or before start" has a bar even
        # when the window opens on a weekend/holiday.
        symbol = _YAHOO_INDEX_SYMBOLS.get(key, _yahoo_symbol(key))
        try:
            history = self._yfinance.get_history(
                symbol, start=(start - timedelta(days=7)).isoformat(), end=(end + timedelta(days=1)).isoformat()
            )
        except MarketDataError:
            history = []
        if not history:
            raise MarketDataError(f"Sem historico gratuito para '{key}' (simbolo {symbol}).", provider="yfinance")
        return history

    # --- historical valuation (for TWR) --------------------------------

    def build_twr_valuator(self, asset: Asset, *, unit_principal: Decimal, start: date, end: date) -> Valuator | None:
        """A valuator ``(holdings, on_date) -> Decimal`` for the TWR engine, or None.

        Variable income marks to the ticker's historical close (long history via
        yfinance); private fixed income marks to its present value on each date
        (BCB series fetched once for the whole window). Returns ``None`` for
        TESOURO — no free historical price series is wired — so the caller reports
        TWR as unavailable.
        """
        from bogle.analytics.twr import price_history_valuator

        if asset.asset_type in VARIABLE_INCOME_TYPES:
            history = self._variable_income_history(asset.ticker, start, end)
            return price_history_valuator({asset.ticker: history}) if history else None
        if asset.asset_type in PRIVATE_FIXED_INCOME_TYPES:
            return self._fixed_income_valuator(asset, unit_principal, end)
        return None

    def _variable_income_history(self, ticker: str, start: date, end: date) -> list[HistPoint]:
        # Long history via yfinance (.SA for B3); brapi's free plan only covers ~3 months.
        try:
            return self._yfinance.get_history(
                _yahoo_symbol(ticker), start=start.isoformat(), end=(end + timedelta(days=1)).isoformat()
            )
        except MarketDataError:
            return []

    def _fixed_income_valuator(self, asset: Asset, unit_principal: Decimal, end: date) -> Valuator | None:
        if asset.purchase_date is None or asset.rate is None:
            return None
        purchase = _as_date(asset.purchase_date)
        cdi: Sequence[SeriesPoint] = ()
        selic: Sequence[SeriesPoint] = ()
        ipca: Sequence[SeriesPoint] = ()
        if not asset.is_prefixed:
            if asset.indexer in (Indexer.CDI, Indexer.CDI_PLUS):
                cdi = self._bcb.get_cdi(purchase, end)
            elif asset.indexer is Indexer.SELIC:
                selic = self._bcb.get_selic(purchase, end)
            elif asset.indexer is Indexer.IPCA_PLUS:
                ipca = self._bcb.get_ipca(date(purchase.year, purchase.month, 1), end)
        rate = asset.rate
        is_prefixed = bool(asset.is_prefixed)
        indexer = asset.indexer
        ticker = asset.ticker

        def valuate(holdings: Mapping[str, Decimal], on_date: date) -> Decimal:
            shares = holdings.get(ticker, _ZERO)
            if shares == _ZERO:
                return _ZERO
            pv_per_unit = present_value(
                unit_principal,
                indexer=indexer,
                rate=rate,
                is_prefixed=is_prefixed,
                purchase_date=purchase,
                on_date=on_date,
                cdi=cdi,
                selic=selic,
                ipca=ipca,
            )
            return shares * pv_per_unit

        return valuate
