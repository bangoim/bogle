"""Tests for the on-the-fly position. Runs against bogle_test; market data comes
from a real PriceDispatcher wired to fake clients (no network).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import DictRow

from bogle.data.cache import DiskCache
from bogle.data.dispatcher import PriceDispatcher
from bogle.data.fixed_income import present_value
from bogle.data.models import HistPoint, Quote, SeriesPoint
from bogle.domain.assets import AssetType, Indexer
from bogle.domain.errors import NetworkError, QuoteNotFoundError
from bogle.position import get_portfolio_summary
from bogle.repositories.assets import AssetRepository
from bogle.repositories.transactions import TransactionRepository

_DT = datetime(2026, 7, 20, tzinfo=UTC)
BUY = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)  # noon UTC -> same calendar day in America/Sao_Paulo
DIV = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
ON_DATE = date(2026, 2, 1)


class FakeBrapi:
    def __init__(self, prices: dict[str, Any] | None = None, index_prices: dict[str, Decimal] | None = None) -> None:
        self.prices = prices or {}
        self.index_prices = index_prices or {}

    def get_quote(self, symbol: str) -> Quote:
        value = self.prices.get(symbol)
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise QuoteNotFoundError(symbol, provider="fake")
        return Quote(symbol=symbol, requested_symbol=symbol, price=value, currency="BRL", time=_DT)

    def get_index_quote(self, index: str) -> Quote:
        value = self.index_prices.get(index)
        if value is None:
            raise QuoteNotFoundError(index, provider="fake")
        return Quote(symbol=index, requested_symbol=index, price=value, currency="BRL", time=_DT)


class FakeYF:
    def __init__(self, quotes: dict[str, Decimal] | None = None, history: dict[str, list[HistPoint]] | None = None):
        self.quotes = quotes or {}
        self.history = history or {}

    def get_quote(self, symbol: str) -> Quote:
        value = self.quotes.get(symbol)
        if value is None:
            raise QuoteNotFoundError(symbol, provider="fake")
        return Quote(symbol=symbol, requested_symbol=symbol, price=value, currency="BRL", time=_DT)

    def get_history(self, symbol: str, **_kwargs: Any) -> list[HistPoint]:
        return list(self.history.get(symbol, []))


class FakeTesouro:
    def get_quote(self, title: str) -> Any:
        raise QuoteNotFoundError(title, provider="tesouro")


class FakeBcb:
    def __init__(self, cdi: Any = ()) -> None:
        self.cdi = list(cdi)

    def get_cdi(self, start: date | None = None, end: date | None = None) -> list[SeriesPoint]:
        return list(self.cdi)

    def get_selic(self, start: date | None = None, end: date | None = None) -> list[SeriesPoint]:
        return []

    def get_ipca(self, start: date | None = None, end: date | None = None) -> list[SeriesPoint]:
        return []


def cdi_series() -> list[SeriesPoint]:
    points, day = [], date(2026, 1, 5)
    while day < ON_DATE:
        if day.weekday() < 5:
            points.append(SeriesPoint(day, Decimal("0.0004")))
        day += timedelta(days=1)
    return points


def petr4_history() -> list[HistPoint]:
    def bar(day: date, close: str) -> HistPoint:
        c = Decimal(close)
        return HistPoint(
            date=datetime(day.year, day.month, day.day, tzinfo=UTC), open=c, high=c, low=c, close=c, volume=0
        )

    return [bar(date(2026, 1, 5), "20"), bar(ON_DATE, "22")]


def make_dispatcher(
    tmp_path: Path, *, brapi: FakeBrapi, yf: FakeYF | None = None, bcb: FakeBcb | None = None
) -> PriceDispatcher:
    return PriceDispatcher(
        brapi=brapi,
        yfinance=yf or FakeYF(history={"PETR4.SA": petr4_history()}),
        tesouro=FakeTesouro(),
        bcb=bcb or FakeBcb(cdi=cdi_series()),
        quote_cache=DiskCache("quotes", base_dir=tmp_path),
        clock=lambda: ON_DATE,
    )


def seed_portfolio(repo: AssetRepository, trepo: TransactionRepository) -> None:
    repo.add("PETR4", Decimal("0.4"), asset_type=AssetType.STOCK)
    trepo.add_buy("PETR4", BUY, Decimal("10"), Decimal("20"))
    trepo.add_dividend("PETR4", DIV, Decimal("5"))
    repo.add(
        "CDB01",
        Decimal("0.4"),
        asset_type=AssetType.CDB,
        issuer="Banco Teste",
        indexer=Indexer.CDI,
        rate=Decimal("1.10"),
        is_prefixed=False,
        daily_liquidity=True,
        purchase_date=BUY,
    )
    trepo.add_buy("CDB01", BUY, Decimal("1"), Decimal("1000"))


class TestPortfolioSummary:
    def test_variable_income_position(
        self, conn: psycopg.Connection[DictRow], repo: AssetRepository, trepo: TransactionRepository, tmp_path: Path
    ) -> None:
        seed_portfolio(repo, trepo)
        summary = get_portfolio_summary(
            conn, make_dispatcher(tmp_path, brapi=FakeBrapi({"PETR4": Decimal("22")})), on_date=ON_DATE
        )
        petr4 = next(p for p in summary.positions if p.ticker == "PETR4")
        assert petr4.quantity == Decimal("10")
        assert petr4.price == Decimal("22")
        assert petr4.market_value == Decimal("220")
        assert petr4.total_invested == Decimal("200")
        assert petr4.pnl == Decimal("20")
        assert petr4.pnl_percent == Decimal("0.1")
        assert petr4.dividends == Decimal("5")
        assert petr4.twr is not None

    def test_fixed_income_position_uses_present_value(
        self, conn: psycopg.Connection[DictRow], repo: AssetRepository, trepo: TransactionRepository, tmp_path: Path
    ) -> None:
        seed_portfolio(repo, trepo)
        summary = get_portfolio_summary(
            conn, make_dispatcher(tmp_path, brapi=FakeBrapi({"PETR4": Decimal("22")})), on_date=ON_DATE
        )
        cdb = next(p for p in summary.positions if p.ticker == "CDB01")
        expected = present_value(
            Decimal("1000"), indexer=Indexer.CDI, rate=Decimal("1.10"), is_prefixed=False,
            purchase_date=date(2026, 1, 5), on_date=ON_DATE, cdi=cdi_series(),
        )  # fmt: skip
        assert cdb.market_value == expected
        assert cdb.price == expected  # quantity == 1
        assert cdb.twr is not None

    def test_weights_sum_to_one(
        self, conn: psycopg.Connection[DictRow], repo: AssetRepository, trepo: TransactionRepository, tmp_path: Path
    ) -> None:
        seed_portfolio(repo, trepo)
        summary = get_portfolio_summary(
            conn, make_dispatcher(tmp_path, brapi=FakeBrapi({"PETR4": Decimal("22")})), on_date=ON_DATE
        )
        total = sum((p.current_weight for p in summary.positions if p.current_weight is not None), Decimal("0"))
        assert abs(total - Decimal("1")) < Decimal("1e-9")

    def test_drift_is_current_minus_target(
        self, conn: psycopg.Connection[DictRow], repo: AssetRepository, trepo: TransactionRepository, tmp_path: Path
    ) -> None:
        seed_portfolio(repo, trepo)
        summary = get_portfolio_summary(
            conn, make_dispatcher(tmp_path, brapi=FakeBrapi({"PETR4": Decimal("22")})), on_date=ON_DATE
        )
        for p in summary.positions:
            if p.current_weight is None:
                assert p.drift is None
            else:
                assert p.drift == p.current_weight - p.target_weight

    def test_totals(
        self, conn: psycopg.Connection[DictRow], repo: AssetRepository, trepo: TransactionRepository, tmp_path: Path
    ) -> None:
        seed_portfolio(repo, trepo)
        summary = get_portfolio_summary(
            conn, make_dispatcher(tmp_path, brapi=FakeBrapi({"PETR4": Decimal("22")})), on_date=ON_DATE
        )
        cdb_value = next(p.market_value for p in summary.positions if p.ticker == "CDB01")
        assert cdb_value is not None
        assert summary.total_value == Decimal("220") + cdb_value
        assert summary.total_invested == Decimal("1200")
        assert summary.total_dividends == Decimal("5")
        assert summary.total_pnl == (Decimal("220") - Decimal("200")) + (cdb_value - Decimal("1000"))


class TestGracefulDegradation:
    def test_empty_portfolio_no_division_by_zero(self, conn: psycopg.Connection[DictRow], tmp_path: Path) -> None:
        summary = get_portfolio_summary(conn, make_dispatcher(tmp_path, brapi=FakeBrapi()), on_date=ON_DATE)
        assert summary.positions == []
        assert summary.total_value == Decimal("0")
        assert summary.total_pnl == Decimal("0")

    def test_price_failure_degrades_to_none(
        self, conn: psycopg.Connection[DictRow], repo: AssetRepository, trepo: TransactionRepository, tmp_path: Path
    ) -> None:
        seed_portfolio(repo, trepo)
        # brapi raises and yfinance has no quote/history for PETR4 -> unpriced.
        brapi = FakeBrapi({"PETR4": NetworkError("down")})
        dispatcher = make_dispatcher(tmp_path, brapi=brapi, yf=FakeYF())
        summary = get_portfolio_summary(conn, dispatcher, on_date=ON_DATE)
        petr4 = next(p for p in summary.positions if p.ticker == "PETR4")
        cdb = next(p for p in summary.positions if p.ticker == "CDB01")
        assert petr4.price is None
        assert petr4.market_value is None
        assert petr4.current_weight is None
        assert petr4.pnl is None
        assert petr4.twr is None  # no history -> no valuator
        # The priced position still carries the whole weight.
        assert cdb.current_weight == Decimal("1")
