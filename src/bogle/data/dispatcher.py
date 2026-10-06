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

Daily closes (variable income and IBOV) are kept in the database when a
:class:`PriceStore` is given (issue #82): Yahoo loads the long history, brapi
is the source of truth for everything since the last load (at least the last 30
sessions, up to the 3 months its free plan serves), once a day, and every other
read of the day comes from the table. Without a store the history is fetched on
every call, as before.

Today's session is never stored. A caller that wants it (the Home summary) asks
:meth:`PriceDispatcher.build_historical_pricing` for ``live=True``, which adds
brapi's D-0 quote as the series' last point for that one answer.

Clients are accepted as structural protocols so tests inject fakes without a
network.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol
from zoneinfo import ZoneInfo

from bogle.analytics.business_days import is_business_day, previous_business_day
from bogle.data.cache import DiskCache
from bogle.data.fixed_income import accumulated_ipca_factor, accumulated_rate_factor, present_value
from bogle.data.models import HistPoint, Quote, SeriesPoint, StoredClose, StoredSpan, TesouroQuote
from bogle.db import DEFAULT_TIMEZONE

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

_REVALIDATED_SESSIONS = 30
"""The fewest recent sessions the day's load re-downloads from brapi."""
_BRAPI_WINDOW_RANGE = "3mo"
"""brapi's range for the day's load: the widest its free plan serves."""
_BRAPI_REACH = timedelta(days=90)
"""How far back that range safely reaches. An absence longer than this leaves a
hole brapi cannot fill, and Yahoo fills the older part of it."""
_REACH_PAD = timedelta(days=7)
"""How far before the date a series has to reach the fetch starts: the bar "on
or before" a weekend or a holiday is a few days earlier."""


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


class BrapiLike(HistorySource, IndexSource, Protocol):
    """brapi exposes quotes, index quotes and (a few months of) history."""


class PriceStore(Protocol):
    """Where daily closes are kept between runs (see :mod:`bogle.repositories.price_history`)."""

    def span(self, symbol: str) -> StoredSpan | None: ...
    def closes(self, symbol: str, start: date, end: date) -> list[StoredClose]: ...
    def save(
        self,
        symbol: str,
        closes: Sequence[StoredClose],
        *,
        loaded_on: date,
        replace: bool = ...,
        confirm: tuple[date, date] | None = ...,
    ) -> None: ...


class SeriesSource(Protocol):
    def get_cdi(self, start: date | None = None, end: date | None = None) -> list[SeriesPoint]: ...
    def get_selic(self, start: date | None = None, end: date | None = None) -> list[SeriesPoint]: ...
    def get_ipca(self, start: date | None = None, end: date | None = None) -> list[SeriesPoint]: ...


def _yahoo_symbol(ticker: str) -> str:
    """brapi ticker -> yfinance symbol (B3 tickers need the ``.SA`` suffix)."""
    return ticker if "." in ticker else f"{ticker}.SA"


def _reaches(history: Sequence[HistPoint], since: date) -> bool:
    """Whether the series has a bar on or before ``since`` — what makes it usable."""
    return bool(history) and _as_date(history[0].date) <= since


def _as_date(value: date) -> date:
    return value.date() if isinstance(value, datetime) else value


def _latest_on_or_before(points: Sequence[SeriesPoint], on: date) -> Decimal | None:
    best: SeriesPoint | None = None
    for point in points:
        if point.date <= on and (best is None or point.date > best.date):
            best = point
    return best.value if best is not None else None


def _window_start(today: date) -> date:
    """First of the last :data:`_REVALIDATED_SESSIONS` sessions before ``today``."""
    day = previous_business_day(today)
    for _ in range(_REVALIDATED_SESSIONS - 1):
        day = previous_business_day(day)
    return day


def _stored(history: Sequence[HistPoint], source: str, *, start: date, before: date) -> list[StoredClose]:
    """The bars of ``history`` worth keeping: in ``[start, before)``, as stored closes.

    ``before`` is today: during the session both providers answer with a partial
    bar for it, and a close that is not a close yet must never reach the table.
    """
    return [
        StoredClose(date=_as_date(point.date), close=point.close, source=source)
        for point in history
        if start <= _as_date(point.date) < before
    ]


def _as_hist_point(row: StoredClose) -> HistPoint:
    """A stored close in the shape the valuators read.

    Only the close is kept, so open/high/low repeat it and the volume is zero:
    nothing downstream of the dispatcher reads them.
    """
    moment = datetime(row.date.year, row.date.month, row.date.day, tzinfo=UTC)
    return HistPoint(date=moment, open=row.close, high=row.close, low=row.close, close=row.close, volume=0)


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


@dataclass(frozen=True, slots=True)
class HistoricalPricing:
    """A valuator plus what the provider's series turned out to cover.

    ``series_start`` is filled only when the whole series was asked for and it
    *still* begins after the date the caller needs (``covering``): the shortfall
    is the series itself, not a bad answer, and asking a third time cannot change
    it. ``None`` means "not known to be definitive" — either the series covers,
    or the second request failed and the next attempt may well succeed.
    """

    valuator: Valuator | None
    series_start: date | None = None
    series_end: date | None = None
    """Freshest bar in the series behind ``valuator``, from the same fetch — free.

    The other end of the same question, and the one the caller reports: a
    provider publishes a session's bar on its own schedule, and asked for a date
    it does not have yet the valuator carries the last close forward without a
    word. Knowing how fresh the series really is turns that into something the
    screen can say. ``None`` for a computed source (private fixed income), which
    has a value for every date and can never lag.
    """
    quote_time: datetime | None = None
    """When ``live`` was asked and the series got today's point: the time of the
    brapi quote behind it (aware, as the provider stamps it)."""
    quote_failed: bool = False
    """``live`` was asked on a trading day and brapi did not answer: the series
    stops at the last stored close, and the caller says why."""


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
        ignore_cached_quotes: bool = False,
        price_store: PriceStore | None = None,
        clock: Callable[[], date] | None = None,
    ) -> None:
        self._brapi = brapi
        self._yfinance = yfinance
        self._tesouro = tesouro
        self._bcb = bcb
        self._cache = quote_cache if quote_cache is not None else DiskCache("quotes")
        self._quote_ttl = quote_ttl
        # Nao lê o cache, mas escreve nele: e o "Atualizar" de uma tela de preco
        # ao vivo, cujo unico proposito e ver a cotacao de agora. As telas
        # seguintes voltam a aproveitar os 5 minutos.
        self._ignore_cached_quotes = ignore_cached_quotes
        self._store = price_store
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
        raise ValueError(f"tipo de ativo sem preço: {asset.asset_type}")

    def _variable_income_info(self, ticker: str) -> PriceInfo:
        key = f"quote:{ticker}"
        cached = None if self._ignore_cached_quotes else self._cache.get(key)
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
            raise ValueError("principal é obrigatório para precificar renda fixa privada.")
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
                f"Sem histórico de '{key}' no início do período ({start.isoformat()}); "
                "as fontes gratuitas não cobrem esse índice/janela.",
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
                    f"Sem histórico de '{key}' em {on.isoformat()}; as fontes gratuitas não cobrem esse índice/janela.",
                    provider="yfinance",
                )
            levels.append(close)
        return levels

    def _index_history(self, key: str, start: date, end: date) -> list[HistPoint]:
        # Pad the fetch a week back so "close on or before start" has a bar even
        # when the window opens on a weekend/holiday.
        padded = start - timedelta(days=7)
        symbol = _YAHOO_INDEX_SYMBOLS.get(key, _yahoo_symbol(key))
        if self._store is not None and key in _YAHOO_INDEX_SYMBOLS:
            # So o IBOV vai para o banco: os outros indices nao tem historico
            # gratuito, e o que cai no `.SA` aqui e engano de quem chamou.
            history, _ = self._stored_history(
                symbol, yahoo_symbol=symbol, brapi_symbol=_INDEX_SYMBOLS[key], start=padded, end=end, reach=start
            )
        else:
            try:
                history = self._yfinance.get_history(
                    symbol, start=padded.isoformat(), end=(end + timedelta(days=1)).isoformat()
                )
            except MarketDataError:
                history = []
        if not history:
            raise MarketDataError(f"Sem histórico gratuito para '{key}' (símbolo {symbol}).", provider="yfinance")
        return history

    def latest_history_date(self, ticker: str, start: date, end: date) -> date | None:
        """Date of the freshest real daily bar for a variable-income ticker.

        ``None`` when there is no bar (e.g. fixed income, which has no yfinance
        history). Used to report how fresh a chart/table really is, since the
        window end may be forward-filled from an older close.
        """
        # So a ponta interessa aqui: com o banco, um ticker listado depois de
        # `start` nao precisa ir ao provedor buscar um comeco que nao existe.
        history, _ = self._variable_income_history(ticker, start, end, reach=end)
        return _as_date(history[-1].date) if history else None

    def latest_index_date(self, index: str, start: date, end: date) -> date | None:
        """Date of the freshest real data point backing an index, or ``None``.

        Mirrors the source routing of :meth:`get_index_series` (BCB series for
        rate indices, yfinance history for market ones) but returns only the
        last real date rather than the grid-aligned levels.
        """
        key = index.upper()
        if key == "IPCA":
            points = self._bcb.get_ipca(date(start.year, start.month, 1), end)
            return points[-1].date if points else None
        if key in _BCB_INDEXES:
            fetch = self._bcb.get_cdi if key == "CDI" else self._bcb.get_selic
            points = fetch(start, end)
            return points[-1].date if points else None
        try:
            history = self._index_history(key, start, end)
        except MarketDataError:
            return None
        return _as_date(history[-1].date) if history else None

    # --- historical valuation (for TWR) --------------------------------

    def build_twr_valuator(
        self, asset: Asset, *, unit_principal: Decimal, start: date, end: date, covering: date | None = None
    ) -> Valuator | None:
        """Just the valuator of :meth:`build_historical_pricing` — see it for the rules.

        Kept for the callers that only need to price or not price (the per-ticker
        TWR of the position table), and do not report *why* when they cannot.
        """
        return self.build_historical_pricing(
            asset, unit_principal=unit_principal, start=start, end=end, covering=covering
        ).valuator

    def build_historical_pricing(
        self,
        asset: Asset,
        *,
        unit_principal: Decimal,
        start: date,
        end: date,
        covering: date | None = None,
        live: bool = False,
    ) -> HistoricalPricing:
        """A valuator ``(holdings, on_date) -> Decimal`` for the TWR engine, or None.

        Variable income marks to the ticker's historical close (long history via
        yfinance); private fixed income marks to its present value on each date
        (BCB series fetched once for the whole window). Returns ``None`` for
        TESOURO — no free historical price series is wired — so the caller reports
        TWR as unavailable.

        ``covering`` is the date the series has to reach back to for the caller to
        be able to use it (usually when the position starts). Given it, a first
        answer that falls short is asked again a different way — see
        :meth:`_variable_income_history` — and the result says whether the
        remaining shortfall is the provider's whole series or just a bad answer.

        ``live`` adds today's point to a variable-income series, from brapi's D-0
        quote (see :meth:`_with_live_quote`). It is never stored.
        """
        from bogle.analytics.twr import price_history_valuator

        if asset.asset_type in VARIABLE_INCOME_TYPES:
            history, series_start = self._variable_income_history(asset.ticker, start, end, covering=covering)
            quote_time, quote_failed = None, False
            if live:
                history, quote_time, quote_failed = self._with_live_quote(asset.ticker, history, end)
            valuator = price_history_valuator({asset.ticker: history}) if history else None
            series_end = _as_date(history[-1].date) if history else None
            return HistoricalPricing(valuator, series_start, series_end, quote_time, quote_failed)
        if asset.asset_type in PRIVATE_FIXED_INCOME_TYPES:
            return HistoricalPricing(self._fixed_income_valuator(asset, unit_principal, end))
        return HistoricalPricing(None)

    def _with_live_quote(
        self, ticker: str, history: list[HistPoint], end: date
    ) -> tuple[list[HistPoint], datetime | None, bool]:
        """``history`` with today's point from brapi's D-0 quote, when there is one.

        Only on a trading day, only when the window reaches today, and only for a
        quote brapi stamps with today's date. Before the session opens brapi still
        answers with the previous close, which the table already has: that is not
        a failure, just nothing to add. A quote that came from the Yahoo fallback
        is not used either, since it carries the time it was fetched and not the
        time it was traded, and so cannot say which session it belongs to.

        The quote goes through the same 5-minute cache as the Position screen, so
        the two screens show the same price. A bar for today already in
        ``history`` (Yahoo answers with a partial one when there is no store) is
        replaced by the quote.
        """
        today = self._today()
        if end < today or not is_business_day(today):
            return history, None, False
        try:
            info = self._variable_income_info(ticker)
        except MarketDataError:
            return history, None, True
        if info.source != "brapi" or info.as_of is None:
            return history, None, True
        if info.as_of.astimezone(ZoneInfo(DEFAULT_TIMEZONE)).date() != today:
            return history, None, False
        moment = datetime(today.year, today.month, today.day, tzinfo=UTC)
        point = HistPoint(date=moment, open=info.price, high=info.price, low=info.price, close=info.price, volume=0)
        return [*(bar for bar in history if _as_date(bar.date) < today), point], info.as_of, False

    def _variable_income_history(
        self, ticker: str, start: date, end: date, *, covering: date | None = None, reach: date | None = None
    ) -> tuple[list[HistPoint], date | None]:
        """Historical closes for ``[start, end]``, and the series' start when it is known short.

        From the database when there is a store (see :meth:`_stored_history`;
        ``reach`` is how far back it has to go, ``covering`` by default), straight
        from the provider otherwise (:meth:`_fetch_history`).
        """
        symbol = _yahoo_symbol(ticker)
        if self._store is not None:
            return self._stored_history(
                ticker,
                yahoo_symbol=symbol,
                brapi_symbol=ticker,
                start=start,
                end=end,
                reach=reach or covering or start,
                covering=covering,
            )
        return self._fetch_history(symbol, start, end, covering=covering)

    def _fetch_history(
        self, symbol: str, start: date, end: date, *, covering: date | None = None
    ) -> tuple[list[HistPoint], date | None]:
        """Historical closes for ``[start, end]`` from Yahoo, best-effort, and the series' start.

        Long history via yfinance (.SA for B3); brapi's free plan only covers ~3
        months. Yahoo sometimes answers a dated range with just its last weeks —
        no error, simply a short series — and the caller would drop the position
        from every historical number because of it. When ``covering`` says how far
        back the series has to reach, a short answer is asked again as the whole
        series (a different request shape, which sometimes comes back complete)
        and trimmed here.

        It is a second chance, not a guarantee. When the whole series comes back
        and *also* starts too late, its first date is returned alongside the
        points: at that point the provider has said everything it has, and only
        the caller's message changes — there is nothing left to retry. A second
        request that fails outright returns ``None`` instead, which keeps the
        ticker in the "worth trying again" bucket.
        """
        history = self._history_or_empty(symbol, start=start, end=end)
        if covering is None or _reaches(history, covering):
            return history, None
        widest = [point for point in self._history_or_empty(symbol) if start <= _as_date(point.date) <= end]
        if _reaches(widest, covering):
            return widest, None
        # `widest` empty means the second request brought nothing (an error, or a
        # provider that only answers dated ranges): the series' real start stays
        # unknown, so it is not reported as definitive.
        return (widest or history), (_as_date(widest[0].date) if widest else None)

    # --- persisted history (issue #82) -----------------------------------

    def _stored_history(
        self,
        name: str,
        *,
        yahoo_symbol: str,
        brapi_symbol: str,
        start: date,
        end: date,
        reach: date,
        covering: date | None = None,
    ) -> tuple[list[HistPoint], date | None]:
        """``[start, end]`` out of the database, topped up from the providers first.

        Two reasons to go to a provider, and only these two:

        - **The table does not reach ``reach``** (nothing stored yet, or a report
          asking further back than anyone did). That is data never seen, so it is
          fetched whenever it is missing, from Yahoo, which carries the long
          history. A series the provider really does not have that far back is
          asked again on every call that needs it, as it always was.
        - **No load today yet**: the once-a-day load of :meth:`_daily_load`.

        Everything else is a read. The series' start is reported (as the old
        path did) only when the whole series was asked for in this call and the
        table still falls short of ``covering`` after the day's load.
        """
        store = self._store
        assert store is not None  # so chamado com banco
        today = self._today()
        span = store.span(name)
        series_start: date | None = None
        if span is None or span.first > reach:
            lower = reach - _REACH_PAD
            upper = span.first - timedelta(days=1) if span is not None else today - timedelta(days=1)
            if lower <= upper:
                fetched, series_start = self._fetch_history(yahoo_symbol, lower, upper, covering=covering)
                store.save(name, _stored(fetched, "yfinance", start=lower, before=today), loaded_on=today)
        if span is None or span.loaded_on < today:
            self._daily_load(
                name,
                yahoo_symbol=yahoo_symbol,
                brapi_symbol=brapi_symbol,
                last=span.last if span is not None else None,
                today=today,
            )
        history = [_as_hist_point(row) for row in store.closes(name, start, end)]
        if covering is None or _reaches(history, covering) or not history:
            return history, None
        return history, (_as_date(history[0].date) if series_start is not None else None)

    def _daily_load(self, name: str, *, yahoo_symbol: str, brapi_symbol: str, last: date | None, today: date) -> None:
        """The first load of the day: brapi, from the last stored session to D-1.

        brapi is the source of truth here. The load starts after the last session
        in the table, but never later than the last 30 sessions, which are
        re-downloaded every day: what brapi has goes in, a stored close it
        disagrees with is corrected to its value, and every row of the range is
        stamped as loaded today, the mark that spares the rest of the day's
        screens. It is one request either way (the free plan's ``3mo``). An
        absence of 90 days or more starts before what that range reaches; the
        older part of the hole is long history, and comes from Yahoo.

        With brapi down (or answering nothing for the range) Yahoo covers the
        sessions missing from it, without correcting anything. With both down the
        range is neither written nor stamped, so the next screen tries again; the
        table still has everything loaded before. (The one exception is a long
        absence whose older part Yahoo filled a moment earlier in this same load:
        those rows carry today's date, and the day counts as loaded. Yahoo
        answering and then failing within the same load is not worth a flag of
        its own.)
        """
        store = self._store
        assert store is not None
        yesterday = today - timedelta(days=1)
        window_start = _window_start(today)
        load_from = window_start if last is None or last >= window_start else last + timedelta(days=1)
        brapi_floor = today - _BRAPI_REACH
        if load_from < brapi_floor:
            old = self._history_or_empty(yahoo_symbol, start=load_from, end=brapi_floor - timedelta(days=1))
            store.save(name, _stored(old, "yfinance", start=load_from, before=brapi_floor), loaded_on=today)
            load_from = brapi_floor
        try:
            recent = self._brapi.get_history(brapi_symbol, range_=_BRAPI_WINDOW_RANGE)
        except MarketDataError:
            recent = []
        window = _stored(recent, "brapi", start=load_from, before=today)
        if window:
            store.save(name, window, loaded_on=today, replace=True, confirm=(load_from, yesterday))
            return
        fallback = self._history_or_empty(yahoo_symbol, start=load_from, end=yesterday)
        window = _stored(fallback, "yfinance", start=load_from, before=today)
        if window:
            store.save(name, window, loaded_on=today, confirm=(load_from, yesterday))

    def _history_or_empty(self, symbol: str, *, start: date | None = None, end: date | None = None) -> list[HistPoint]:
        try:
            if start is None:
                return self._yfinance.get_history(symbol, range_="max")
            return self._yfinance.get_history(
                symbol, start=start.isoformat(), end=(end + timedelta(days=1)).isoformat() if end else None
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
