"""Tests for the yfinance client. No network: a fake ticker factory returns real
pandas DataFrames / fast_info stand-ins, and the clock is injected.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from bogle.data.models import HistPoint, Quote
from bogle.data.yfinance_client import YFinanceClient
from bogle.domain.errors import NetworkError, QuoteNotFoundError

CLOCK = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)


class FakeTicker:
    def __init__(self, *, fast_info: Any = None, history_result: Any = None) -> None:
        self._fast_info = fast_info
        self._history_result = history_result
        self.history_calls: list[dict[str, Any]] = []

    @property
    def fast_info(self) -> Any:
        if isinstance(self._fast_info, Exception):
            raise self._fast_info
        return self._fast_info

    def history(self, **kwargs: Any) -> Any:
        self.history_calls.append(kwargs)
        if isinstance(self._history_result, Exception):
            raise self._history_result
        return self._history_result


def make_client(mapping: dict[str, FakeTicker], clock_value: datetime = CLOCK) -> YFinanceClient:
    return YFinanceClient(ticker_factory=lambda symbol: mapping[symbol], clock=lambda: clock_value)


def fast_info(last: float | None = 41.15, previous: float | None = 41.25, currency: str = "BRL") -> SimpleNamespace:
    return SimpleNamespace(last_price=last, previous_close=previous, currency=currency)


def history_df(rows: list[dict[str, Any]], tz: str | None = None) -> pd.DataFrame:
    index = pd.to_datetime([r["date"] for r in rows])
    if tz is not None:
        index = index.tz_localize(tz)
    return pd.DataFrame(
        {
            "Open": [r["open"] for r in rows],
            "High": [r["high"] for r in rows],
            "Low": [r["low"] for r in rows],
            "Close": [r["close"] for r in rows],
            "Adj Close": [r["adj"] for r in rows],
            "Volume": [r["vol"] for r in rows],
        },
        index=index,
    )


def row(date: str, close: float, *, adj: float | None = None, vol: float = 1000.0) -> dict[str, Any]:
    return {"date": date, "open": close, "high": close, "low": close, "close": close,
            "adj": close if adj is None else adj, "vol": vol}  # fmt: skip


def approx(value: Decimal | None, expected: str, tol: str = "1e-9") -> bool:
    assert value is not None
    return abs(value - Decimal(expected)) < Decimal(tol)


class TestGetQuote:
    def test_parses_fast_info(self) -> None:
        client = make_client({"AAPL": FakeTicker(fast_info=fast_info(last=41.15, previous=41.25))})
        q = client.get_quote("AAPL")
        assert isinstance(q, Quote)
        assert q.symbol == "AAPL"
        assert q.requested_symbol == "AAPL"
        assert q.renamed is False
        assert q.price == Decimal("41.15")
        assert q.currency == "BRL"
        assert q.previous_close == Decimal("41.25")
        assert q.change == Decimal("-0.10")
        assert approx(q.change_percent, str(Decimal("-0.10") / Decimal("41.25") * 100))

    def test_time_is_the_injected_clock(self) -> None:
        client = make_client({"AAPL": FakeTicker(fast_info=fast_info())})
        assert client.get_quote("AAPL").time == CLOCK

    def test_missing_last_price_raises(self) -> None:
        client = make_client({"AAPL": FakeTicker(fast_info=fast_info(last=None))})
        with pytest.raises(QuoteNotFoundError):
            client.get_quote("AAPL")

    def test_nan_last_price_raises(self) -> None:
        client = make_client({"AAPL": FakeTicker(fast_info=fast_info(last=float("nan")))})
        with pytest.raises(QuoteNotFoundError):
            client.get_quote("AAPL")

    def test_no_previous_close_leaves_change_none(self) -> None:
        client = make_client({"AAPL": FakeTicker(fast_info=fast_info(previous=None))})
        q = client.get_quote("AAPL")
        assert q.price == Decimal("41.15")
        assert q.previous_close is None
        assert q.change is None
        assert q.change_percent is None

    def test_fast_info_error_becomes_network_error(self) -> None:
        client = make_client({"AAPL": FakeTicker(fast_info=RuntimeError("yahoo down"))})
        with pytest.raises(NetworkError) as exc_info:
            client.get_quote("AAPL")
        assert exc_info.value.provider == "yfinance"


class TestGetQuotesAndIndex:
    def test_get_quotes_iterates(self) -> None:
        client = make_client(
            {"AAPL": FakeTicker(fast_info=fast_info()), "MSFT": FakeTicker(fast_info=fast_info(last=10.0))}
        )
        quotes = client.get_quotes(["AAPL", "MSFT"])
        assert [q.symbol for q in quotes] == ["AAPL", "MSFT"]

    def test_get_index_quote_uses_same_path(self) -> None:
        client = make_client({"^BVSP": FakeTicker(fast_info=fast_info(last=130000.0, previous=129000.0))})
        q = client.get_index_quote("^BVSP")
        assert q.symbol == "^BVSP"
        assert q.price == Decimal("130000.0")


class TestGetHistory:
    def test_parses_dataframe(self) -> None:
        df = history_df([row("2026-01-05", 50.0, adj=49.0, vol=100.0), row("2026-01-06", 55.0, adj=54.0)])
        client = make_client({"PETR4.SA": FakeTicker(history_result=df)})
        points = client.get_history("PETR4.SA")
        assert len(points) == 2
        assert all(isinstance(p, HistPoint) for p in points)
        assert points[0].close == Decimal("50")
        assert points[0].adjusted_close == Decimal("49")
        assert isinstance(points[0].volume, int)
        assert points[0].volume == 100

    def test_normalizes_to_chronological_order(self) -> None:
        df = history_df([row("2026-01-06", 55.0), row("2026-01-05", 50.0), row("2026-01-07", 60.0)])
        client = make_client({"PETR4.SA": FakeTicker(history_result=df)})
        points = client.get_history("PETR4.SA")
        assert [p.close for p in points] == [Decimal("50"), Decimal("55"), Decimal("60")]

    def test_skips_rows_with_nan_close(self) -> None:
        df = history_df([row("2026-01-05", 50.0), row("2026-01-06", float("nan"))])
        client = make_client({"PETR4.SA": FakeTicker(history_result=df)})
        points = client.get_history("PETR4.SA")
        assert len(points) == 1
        assert points[0].close == Decimal("50")

    def test_period_request_forwards_range(self) -> None:
        ticker = FakeTicker(history_result=history_df([row("2026-01-05", 50.0)]))
        client = make_client({"PETR4.SA": ticker})
        client.get_history("PETR4.SA", range_="1mo", interval="1d")
        call = ticker.history_calls[-1]
        assert call == {"period": "1mo", "interval": "1d", "auto_adjust": False}

    def test_dated_request_forwards_start_end(self) -> None:
        ticker = FakeTicker(history_result=history_df([row("2026-01-05", 50.0)]))
        client = make_client({"PETR4.SA": ticker})
        client.get_history("PETR4.SA", start="2026-01-01", end="2026-02-01")
        call = ticker.history_calls[-1]
        assert call == {"start": "2026-01-01", "end": "2026-02-01", "interval": "1d", "auto_adjust": False}

    def test_empty_dataframe_raises_not_found(self) -> None:
        client = make_client({"NOPE": FakeTicker(history_result=pd.DataFrame())})
        with pytest.raises(QuoteNotFoundError):
            client.get_history("NOPE")

    def test_history_error_becomes_network_error(self) -> None:
        client = make_client({"PETR4.SA": FakeTicker(history_result=ConnectionError("boom"))})
        with pytest.raises(NetworkError):
            client.get_history("PETR4.SA")


class TestTimezoneNormalization:
    def test_naive_index_treated_as_utc(self) -> None:
        df = history_df([row("2026-01-05", 50.0)])
        client = make_client({"PETR4.SA": FakeTicker(history_result=df)})
        points = client.get_history("PETR4.SA")
        assert points[0].date == datetime(2026, 1, 5, tzinfo=UTC)

    def test_aware_index_converted_to_utc(self) -> None:
        # Midnight US/Eastern (UTC-5 in January) -> 05:00 UTC.
        df = history_df([row("2026-01-05", 50.0)], tz="US/Eastern")
        client = make_client({"PETR4.SA": FakeTicker(history_result=df)})
        points = client.get_history("PETR4.SA")
        assert points[0].date == datetime(2026, 1, 5, 5, 0, tzinfo=UTC)


@pytest.mark.live
def test_live_history_petr4() -> None:
    """Smoke test against real Yahoo. Deselected by default; run with -m live."""
    points = YFinanceClient().get_history("PETR4.SA", range_="1mo")
    assert points
    assert all(isinstance(p, HistPoint) for p in points)
