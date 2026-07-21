"""Client for Yahoo Finance via the ``yfinance`` library.

Two roles (decision G): the free source of *long* history for B3 tickers (pass the
``.SA`` suffix, e.g. ``PETR4.SA``), and a fallback *quote* for instruments brapi
does not cover (e.g. international tickers). brapi stays the primary intraday quote
source; this wraps an unofficial, community-maintained scraper that can break, so
callers should treat it as best-effort.

Same shape as :class:`bogle.data.brapi.BrapiClient` (requirement of #15/#18):
``get_quote``/``get_quotes``/``get_index_quote``/``get_history`` returning the same
``Quote``/``HistPoint`` types.

Caveats baked in here:
- yfinance returns floats, so values are ``Decimal(str(float))`` — exact to what
  the float prints, unlike brapi's exact JSON. Fine for history feeding TWR.
- ``fast_info`` exposes no market timestamp, so a quote's ``time`` is stamped at
  fetch time (injectable ``clock``), not the exchange's quote time.
- yfinance does not report symbol renames, so ``symbol == requested_symbol``.

``yfinance`` (and its pandas/numpy tail) is imported lazily so importing this
module — or running a CLI command that never prices anything — stays cheap.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from bogle.data.models import HistPoint, Quote
from bogle.domain.errors import NetworkError, QuoteNotFoundError

_PROVIDER = "yfinance"

TickerFactory = Callable[[str], Any]


def _default_ticker_factory(symbol: str) -> Any:
    import yfinance  # lazy: only pulls pandas/numpy when a quote is actually fetched

    return yfinance.Ticker(symbol)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _dec_opt(value: Any) -> Decimal | None:
    return None if value is None or _is_nan(value) else _dec(value)


def _is_nan(value: Any) -> bool:
    # numpy float64 subclasses float, so this covers pandas NaN cells too.
    return isinstance(value, float) and value != value


def _attr(obj: Any, name: str) -> Any:
    # FastInfo attribute access can raise when a field is unavailable.
    try:
        return getattr(obj, name)
    except Exception:
        return None


def _to_utc_datetime(timestamp: Any) -> datetime:
    dt = timestamp.to_pydatetime()
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


class YFinanceClient:
    """Thin adapter over ``yfinance.Ticker`` with the common client interface.

    Tests inject a ``ticker_factory`` returning objects that expose ``fast_info``
    and ``history(...)`` (real pandas DataFrames), plus a fixed ``clock`` — so no
    network and no reliance on the live Yahoo endpoint.
    """

    def __init__(
        self,
        *,
        ticker_factory: TickerFactory | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._ticker_factory = ticker_factory if ticker_factory is not None else _default_ticker_factory
        self._clock = clock if clock is not None else _utcnow

    # --- public API -----------------------------------------------------

    def get_quote(self, symbol: str) -> Quote:
        """Best-effort current quote (fallback source; see module docstring)."""
        ticker = self._ticker_factory(symbol)
        try:
            info = ticker.fast_info
            last = _attr(info, "last_price")
            previous = _attr(info, "previous_close")
            currency = _attr(info, "currency")
        except Exception as exc:  # yfinance/network hiccup
            raise NetworkError(_PROVIDER, str(exc)) from exc
        if last is None or _is_nan(last):
            raise QuoteNotFoundError(symbol, provider=_PROVIDER)
        price = _dec(last)
        previous_close = _dec_opt(previous)
        change = change_percent = None
        if previous_close is not None and previous_close != 0:
            change = price - previous_close
            change_percent = change / previous_close * 100
        return Quote(
            symbol=symbol,
            requested_symbol=symbol,
            price=price,
            currency=str(currency or ""),
            time=self._clock(),
            previous_close=previous_close,
            change=change,
            change_percent=change_percent,
        )

    def get_quotes(self, symbols: Sequence[str]) -> list[Quote]:
        return [self.get_quote(symbol) for symbol in symbols]

    def get_index_quote(self, index: str) -> Quote:
        """Current value of an index (e.g. ``"^BVSP"``); same path as a quote."""
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

        ``range_`` maps to yfinance's ``period`` (ignored when ``start``/``end`` are
        given). Raises :class:`QuoteNotFoundError` when Yahoo returns no rows (its
        only signal for an unknown symbol).
        """
        ticker = self._ticker_factory(symbol)
        try:
            if start is not None or end is not None:
                frame = ticker.history(start=start, end=end, interval=interval, auto_adjust=False)
            else:
                frame = ticker.history(period=range_, interval=interval, auto_adjust=False)
        except Exception as exc:
            raise NetworkError(_PROVIDER, str(exc)) from exc
        points = self._parse_history(frame)
        if not points:
            raise QuoteNotFoundError(symbol, provider=_PROVIDER)
        return points

    # --- parsing --------------------------------------------------------

    def _parse_history(self, frame: Any) -> list[HistPoint]:
        if frame is None or getattr(frame, "empty", False):
            return []
        points: list[HistPoint] = []
        for timestamp, row in frame.iterrows():
            close = row["Close"]
            if close is None or _is_nan(close):
                continue
            has_adj = "Adj Close" in row
            volume = row["Volume"]
            points.append(
                HistPoint(
                    date=_to_utc_datetime(timestamp),
                    open=_dec(row["Open"]),
                    high=_dec(row["High"]),
                    low=_dec(row["Low"]),
                    close=_dec(close),
                    volume=0 if _is_nan(volume) else int(volume),
                    adjusted_close=_dec_opt(row["Adj Close"]) if has_adj else None,
                )
            )
        points.sort(key=lambda point: point.date)
        return points
