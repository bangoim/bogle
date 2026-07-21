"""Tests for the PriceDispatcher. Clients are fakes (no network); the quote cache
is pinned to tmp_path.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from bogle.data.cache import DiskCache
from bogle.data.dispatcher import PriceDispatcher
from bogle.data.fixed_income import present_value
from bogle.data.models import Quote, SeriesPoint, TesouroQuote
from bogle.domain.assets import Asset, AssetType, Indexer
from bogle.domain.errors import NetworkError, QuoteNotFoundError

_DT = datetime(2026, 7, 20, tzinfo=UTC)
TODAY = date(2026, 1, 7)
CDI = [SeriesPoint(date(2026, 1, 5), Decimal("0.0004")), SeriesPoint(date(2026, 1, 6), Decimal("0.0004"))]


class FakeQuoteClient:
    def __init__(self, prices: dict[str, Any] | None = None, index_prices: dict[str, Decimal] | None = None) -> None:
        self.prices = prices or {}
        self.index_prices = index_prices or {}
        self.quote_calls: list[str] = []
        self.index_calls: list[str] = []

    def get_quote(self, symbol: str) -> Quote:
        self.quote_calls.append(symbol)
        value = self.prices.get(symbol)
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise QuoteNotFoundError(symbol, provider="fake")
        return Quote(symbol=symbol, requested_symbol=symbol, price=value, currency="BRL", time=_DT)

    def get_index_quote(self, index: str) -> Quote:
        self.index_calls.append(index)
        value = self.index_prices.get(index)
        if value is None:
            raise QuoteNotFoundError(index, provider="fake")
        return Quote(symbol=index, requested_symbol=index, price=value, currency="BRL", time=_DT)


class FakeTesouro:
    def __init__(self, quote: TesouroQuote | None) -> None:
        self.quote = quote

    def get_quote(self, title: str) -> TesouroQuote:
        if self.quote is None:
            raise QuoteNotFoundError(title, provider="tesouro")
        return self.quote


class FakeBcb:
    def __init__(self, cdi: Any = (), selic: Any = (), ipca: Any = ()) -> None:
        self.cdi, self.selic, self.ipca = list(cdi), list(selic), list(ipca)
        self.calls: list[tuple[str, date | None, date | None]] = []

    def get_cdi(self, start: date | None = None, end: date | None = None) -> list[SeriesPoint]:
        self.calls.append(("cdi", start, end))
        return list(self.cdi)

    def get_selic(self, start: date | None = None, end: date | None = None) -> list[SeriesPoint]:
        self.calls.append(("selic", start, end))
        return list(self.selic)

    def get_ipca(self, start: date | None = None, end: date | None = None) -> list[SeriesPoint]:
        self.calls.append(("ipca", start, end))
        return list(self.ipca)


def make_dispatcher(
    tmp_path: Path,
    *,
    brapi: FakeQuoteClient | None = None,
    yfinance: FakeQuoteClient | None = None,
    tesouro: FakeTesouro | None = None,
    bcb: FakeBcb | None = None,
) -> PriceDispatcher:
    return PriceDispatcher(
        brapi=brapi or FakeQuoteClient(),
        yfinance=yfinance or FakeQuoteClient(),
        tesouro=tesouro or FakeTesouro(None),
        bcb=bcb or FakeBcb(),
        quote_cache=DiskCache("quotes", base_dir=tmp_path),
        clock=lambda: TODAY,
    )


def stock(ticker: str = "PETR4") -> Asset:
    return Asset(ticker=ticker, target_weight=Decimal("0.1"), asset_type=AssetType.STOCK)


def cdb(indexer: Indexer = Indexer.CDI, *, rate: str = "1.10", prefixed: bool = False) -> Asset:
    return Asset(
        ticker="CDB X",
        target_weight=Decimal("0.1"),
        asset_type=AssetType.CDB,
        indexer=None if prefixed else indexer,
        rate=Decimal(rate),
        is_prefixed=prefixed,
        daily_liquidity=True,
        purchase_date=datetime(2026, 1, 5, tzinfo=UTC),
    )


class TestVariableIncome:
    def test_price_from_brapi(self, tmp_path: Path) -> None:
        d = make_dispatcher(tmp_path, brapi=FakeQuoteClient({"PETR4": Decimal("41.15")}))
        assert d.get_price(stock()) == Decimal("41.15")

    def test_second_call_hits_cache_not_api(self, tmp_path: Path) -> None:
        brapi = FakeQuoteClient({"PETR4": Decimal("41.15")})
        d = make_dispatcher(tmp_path, brapi=brapi)
        d.get_price(stock())
        d.get_price(stock())
        assert brapi.quote_calls == ["PETR4"]

    def test_falls_back_to_yfinance_on_brapi_failure(self, tmp_path: Path) -> None:
        brapi = FakeQuoteClient({"PETR4": NetworkError("brapi")})
        yfinance = FakeQuoteClient({"PETR4.SA": Decimal("40.00")})
        d = make_dispatcher(tmp_path, brapi=brapi, yfinance=yfinance)
        assert d.get_price(stock()) == Decimal("40.00")
        assert yfinance.quote_calls == ["PETR4.SA"]

    def test_falls_back_when_brapi_not_found(self, tmp_path: Path) -> None:
        brapi = FakeQuoteClient({})  # PETR4 absent -> QuoteNotFoundError
        yfinance = FakeQuoteClient({"PETR4.SA": Decimal("39.00")})
        d = make_dispatcher(tmp_path, brapi=brapi, yfinance=yfinance)
        assert d.get_price(stock()) == Decimal("39.00")


class TestTesouro:
    def _quote(self, *, pu_venda: str | None, pu_base: str | None = None) -> TesouroQuote:
        return TesouroQuote(
            title="Tesouro IPCA+ 2035",
            bond_type="Tesouro IPCA+",
            maturity=date(2035, 5, 15),
            base_date=date(2026, 7, 17),
            pu_venda=Decimal(pu_venda) if pu_venda else None,
            pu_base=Decimal(pu_base) if pu_base else None,
        )

    def test_uses_pu_venda(self, tmp_path: Path) -> None:
        d = make_dispatcher(tmp_path, tesouro=FakeTesouro(self._quote(pu_venda="2404.58")))
        asset = Asset(ticker="Tesouro IPCA+ 2035", target_weight=Decimal("0.1"), asset_type=AssetType.TESOURO)
        assert d.get_price(asset) == Decimal("2404.58")

    def test_falls_back_to_pu_base(self, tmp_path: Path) -> None:
        d = make_dispatcher(tmp_path, tesouro=FakeTesouro(self._quote(pu_venda=None, pu_base="2400.00")))
        asset = Asset(ticker="Tesouro IPCA+ 2035", target_weight=Decimal("0.1"), asset_type=AssetType.TESOURO)
        assert d.get_price(asset) == Decimal("2400.00")


class TestPrivateFixedIncome:
    def test_cdi_matches_present_value(self, tmp_path: Path) -> None:
        d = make_dispatcher(tmp_path, bcb=FakeBcb(cdi=CDI))
        pv = d.get_price(cdb(Indexer.CDI, rate="1.10"), principal=Decimal("1000"))
        expected = present_value(
            Decimal("1000"), indexer=Indexer.CDI, rate=Decimal("1.10"), is_prefixed=False,
            purchase_date=date(2026, 1, 5), on_date=TODAY, cdi=CDI,
        )  # fmt: skip
        assert pv == expected

    def test_fetches_cdi_for_the_holding_period(self, tmp_path: Path) -> None:
        bcb = FakeBcb(cdi=CDI)
        d = make_dispatcher(tmp_path, bcb=bcb)
        d.get_price(cdb(Indexer.CDI), principal=Decimal("1000"))
        assert bcb.calls == [("cdi", date(2026, 1, 5), TODAY)]

    def test_prefixed_needs_no_series(self, tmp_path: Path) -> None:
        bcb = FakeBcb()
        d = make_dispatcher(tmp_path, bcb=bcb)
        pv = d.get_price(cdb(prefixed=True, rate="0.12"), principal=Decimal("1000"))
        expected = present_value(
            Decimal("1000"), indexer=None, rate=Decimal("0.12"), is_prefixed=True,
            purchase_date=date(2026, 1, 5), on_date=TODAY,
        )  # fmt: skip
        assert pv == expected
        assert bcb.calls == []

    def test_ipca_fetches_from_month_start(self, tmp_path: Path) -> None:
        ipca = [SeriesPoint(date(2026, 1, 1), Decimal("0.005"))]
        bcb = FakeBcb(ipca=ipca)
        d = make_dispatcher(tmp_path, bcb=bcb)
        d.get_price(cdb(Indexer.IPCA_PLUS, rate="0.06"), principal=Decimal("1000"))
        assert bcb.calls == [("ipca", date(2026, 1, 1), TODAY)]

    def test_missing_principal_raises(self, tmp_path: Path) -> None:
        d = make_dispatcher(tmp_path, bcb=FakeBcb(cdi=CDI))
        with pytest.raises(ValueError, match="principal"):
            d.get_price(cdb(Indexer.CDI))


class TestIndexValue:
    def test_cdi_returns_latest_series_value(self, tmp_path: Path) -> None:
        series = [SeriesPoint(date(2026, 1, 5), Decimal("0.0004")), SeriesPoint(date(2026, 1, 6), Decimal("0.00045"))]
        d = make_dispatcher(tmp_path, bcb=FakeBcb(cdi=series))
        assert d.get_index_value("CDI") == Decimal("0.00045")

    def test_ibov_routes_to_brapi_caret_symbol(self, tmp_path: Path) -> None:
        brapi = FakeQuoteClient(index_prices={"^BVSP": Decimal("130000")})
        d = make_dispatcher(tmp_path, brapi=brapi)
        assert d.get_index_value("IBOV") == Decimal("130000")
        assert brapi.index_calls == ["^BVSP"]

    def test_unknown_market_index_passes_symbol_through(self, tmp_path: Path) -> None:
        brapi = FakeQuoteClient(index_prices={"XPTO": Decimal("10")})
        d = make_dispatcher(tmp_path, brapi=brapi)
        assert d.get_index_value("XPTO") == Decimal("10")
        assert brapi.index_calls == ["XPTO"]
