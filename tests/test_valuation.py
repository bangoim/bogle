"""Tests for the reports foundation (issue #67): index accumulation on the
dispatcher and portfolio-level historical valuation."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from psycopg.rows import DictRow

from bogle.data.cache import DiskCache
from bogle.data.dispatcher import PriceDispatcher
from bogle.data.models import HistPoint, SeriesPoint
from bogle.domain.errors import MarketDataError, QuoteNotFoundError
from bogle.reports.valuation import (
    NO_SOURCE,
    NOTHING_RETURNED,
    SHORT_SERIES,
    build_portfolio_valuation,
    date_grid,
    first_transaction_date,
    patrimony_at,
    patrimony_series,
    portfolio_twr,
)
from bogle.repositories.assets import AssetRepository
from bogle.repositories.transactions import TransactionRepository

_ZERO = Decimal("0")


def bar(day: str, close: str) -> HistPoint:
    d = date.fromisoformat(day)
    return HistPoint(
        date=datetime(d.year, d.month, d.day, tzinfo=UTC),
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=0,
    )


class FakeYfinance:
    def __init__(self, histories: dict[str, list[HistPoint]] | None = None) -> None:
        self.histories = histories or {}
        self.calls: list[str] = []

    def get_quote(self, symbol: str) -> Any:
        raise QuoteNotFoundError(symbol, provider="fake")

    def get_history(self, symbol: str, **_kwargs: Any) -> list[HistPoint]:
        self.calls.append(symbol)
        return list(self.histories.get(symbol, []))


class FakeBcb:
    def __init__(self, cdi: Any = (), selic: Any = (), ipca: Any = ()) -> None:
        self.cdi, self.selic, self.ipca = list(cdi), list(selic), list(ipca)

    def get_cdi(self, start: date | None = None, end: date | None = None) -> list[SeriesPoint]:
        return list(self.cdi)

    def get_selic(self, start: date | None = None, end: date | None = None) -> list[SeriesPoint]:
        return list(self.selic)

    def get_ipca(self, start: date | None = None, end: date | None = None) -> list[SeriesPoint]:
        return list(self.ipca)


class _Unused:
    def get_quote(self, *_args: Any, **_kwargs: Any) -> Any:
        raise QuoteNotFoundError("unused", provider="fake")

    def get_index_quote(self, *_args: Any, **_kwargs: Any) -> Any:
        raise QuoteNotFoundError("unused", provider="fake")


def make_dispatcher(tmp_path: Any, *, yfinance: Any = None, bcb: FakeBcb | None = None) -> PriceDispatcher:
    return PriceDispatcher(
        brapi=_Unused(),
        yfinance=yfinance if yfinance is not None else FakeYfinance(),
        tesouro=_Unused(),
        bcb=bcb if bcb is not None else FakeBcb(),
        quote_cache=DiskCache("quotes", base_dir=tmp_path),
    )


class TestIndexReturn:
    def test_cdi_compounds_daily_series(self, tmp_path: Any) -> None:
        cdi = [
            SeriesPoint(date(2026, 1, 5), Decimal("0.0004")),
            SeriesPoint(date(2026, 1, 6), Decimal("0.0004")),
        ]
        dispatcher = make_dispatcher(tmp_path, bcb=FakeBcb(cdi=cdi))
        result = dispatcher.get_index_return("CDI", date(2026, 1, 5), date(2026, 1, 7))
        assert result == (Decimal("1.0004") * Decimal("1.0004")) - 1

    def test_ibov_uses_first_and_last_close(self, tmp_path: Any) -> None:
        yf = FakeYfinance({"^BVSP": [bar("2026-01-05", "100000"), bar("2026-07-01", "110000")]})
        dispatcher = make_dispatcher(tmp_path, yfinance=yf)
        result = dispatcher.get_index_return("IBOV", date(2026, 1, 5), date(2026, 7, 20))
        assert result == Decimal("0.1")
        assert yf.calls == ["^BVSP"]

    def test_listed_etf_uses_sa_suffix(self, tmp_path: Any) -> None:
        yf = FakeYfinance({"IVVB11.SA": [bar("2026-01-05", "200"), bar("2026-07-01", "300")]})
        dispatcher = make_dispatcher(tmp_path, yfinance=yf)
        result = dispatcher.get_index_return("IVVB11", date(2026, 1, 5), date(2026, 7, 20))
        assert result == Decimal("0.5")

    def test_index_without_history_is_friendly(self, tmp_path: Any) -> None:
        dispatcher = make_dispatcher(tmp_path)
        with pytest.raises(MarketDataError, match="Sem historico gratuito para 'IFIX'"):
            dispatcher.get_index_return("IFIX", date(2026, 1, 5), date(2026, 7, 20))

    def test_history_starting_after_window_is_friendly(self, tmp_path: Any) -> None:
        yf = FakeYfinance({"^BVSP": [bar("2026-06-01", "100000")]})
        dispatcher = make_dispatcher(tmp_path, yfinance=yf)
        with pytest.raises(MarketDataError, match="no inicio do periodo"):
            dispatcher.get_index_return("IBOV", date(2026, 1, 5), date(2026, 7, 20))

    def test_empty_bcb_series(self, tmp_path: Any) -> None:
        dispatcher = make_dispatcher(tmp_path)
        with pytest.raises(QuoteNotFoundError):
            dispatcher.get_index_return("CDI", date(2026, 1, 5), date(2026, 7, 20))


class TestLatestDataDate:
    def test_history_date_is_last_bar(self, tmp_path: Any) -> None:
        yf = FakeYfinance({"PETR4.SA": [bar("2026-06-22", "20"), bar("2026-07-17", "25")]})
        d = make_dispatcher(tmp_path, yfinance=yf).latest_history_date("PETR4", date(2026, 6, 1), date(2026, 7, 20))
        assert d == date(2026, 7, 17)

    def test_history_date_none_when_no_bars(self, tmp_path: Any) -> None:
        d = make_dispatcher(tmp_path).latest_history_date("PETR4", date(2026, 6, 1), date(2026, 7, 20))
        assert d is None

    def test_index_date_market(self, tmp_path: Any) -> None:
        yf = FakeYfinance({"^BVSP": [bar("2026-07-01", "100"), bar("2026-07-18", "110")]})
        d = make_dispatcher(tmp_path, yfinance=yf).latest_index_date("IBOV", date(2026, 6, 1), date(2026, 7, 20))
        assert d == date(2026, 7, 18)

    def test_index_date_bcb(self, tmp_path: Any) -> None:
        cdi = [SeriesPoint(date(2026, 7, 16), Decimal("0.001")), SeriesPoint(date(2026, 7, 17), Decimal("0.001"))]
        d = make_dispatcher(tmp_path, bcb=FakeBcb(cdi=cdi)).latest_index_date(
            "CDI", date(2026, 6, 1), date(2026, 7, 20)
        )
        assert d == date(2026, 7, 17)

    def test_index_date_none_on_missing_history(self, tmp_path: Any) -> None:
        d = make_dispatcher(tmp_path).latest_index_date("IFIX", date(2026, 6, 1), date(2026, 7, 20))
        assert d is None


class TestIndexSeries:
    def test_cdi_series_is_cumulative_factor(self, tmp_path: Any) -> None:
        cdi = [
            SeriesPoint(date(2026, 1, 5), Decimal("0.001")),
            SeriesPoint(date(2026, 1, 6), Decimal("0.001")),
        ]
        dispatcher = make_dispatcher(tmp_path, bcb=FakeBcb(cdi=cdi))
        grid = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)]
        levels = dispatcher.get_index_series("CDI", grid)
        assert levels[0] == Decimal("1")  # [start, start) = nada acumulado
        assert levels[1] == Decimal("1.001")
        assert levels[2] == Decimal("1.001") * Decimal("1.001")

    def test_market_series_carries_last_close_over_gaps(self, tmp_path: Any) -> None:
        yf = FakeYfinance({"^BVSP": [bar("2026-01-05", "100"), bar("2026-01-07", "110")]})
        dispatcher = make_dispatcher(tmp_path, yfinance=yf)
        grid = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)]
        assert dispatcher.get_index_series("IBOV", grid) == [Decimal("100"), Decimal("100"), Decimal("110")]

    def test_empty_grid(self, tmp_path: Any) -> None:
        assert make_dispatcher(tmp_path).get_index_series("CDI", []) == []


class TestDateGrid:
    def test_daily_includes_both_ends(self) -> None:
        grid = date_grid(date(2026, 1, 1), date(2026, 1, 4), "daily")
        assert grid == [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3), date(2026, 1, 4)]

    def test_weekly_anchors_on_end(self) -> None:
        grid = date_grid(date(2026, 1, 1), date(2026, 1, 20), "weekly")
        assert grid[-1] == date(2026, 1, 20)
        assert grid[0] == date(2026, 1, 1)
        assert date(2026, 1, 13) in grid

    def test_monthly_anchors_on_end(self) -> None:
        grid = date_grid(date(2026, 1, 15), date(2026, 7, 22), "monthly")
        assert grid[-1] == date(2026, 7, 22)
        assert grid[0] == date(2026, 1, 15)
        assert date(2026, 6, 22) in grid


class TestPortfolioValuation:
    @pytest.fixture
    def seeded(self, conn: psycopg.Connection[DictRow]) -> None:
        from bogle.domain.assets import AssetType

        assets = AssetRepository(conn)
        assets.add("PETR4", Decimal("0.5"))
        assets.add("TESOURO SELIC 2029", Decimal("0.3"), asset_type=AssetType.TESOURO)
        transactions = TransactionRepository(conn)
        transactions.add_buy(
            "PETR4", shares=Decimal("10"), unit_price=Decimal("20"), date=datetime(2026, 1, 5, tzinfo=UTC)
        )
        transactions.add_buy(
            "TESOURO SELIC 2029", shares=Decimal("1"), unit_price=Decimal("1000"), date=datetime(2026, 1, 5, tzinfo=UTC)
        )

    def test_excludes_tesouro_and_its_transactions(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        yf = FakeYfinance({"PETR4.SA": [bar("2026-01-05", "20"), bar("2026-07-01", "25")]})
        valuation = build_portfolio_valuation(
            conn, make_dispatcher(tmp_path, yfinance=yf), start=date(2026, 1, 5), end=date(2026, 7, 20)
        )
        assert valuation.excluded == ["TESOURO SELIC 2029"]
        assert {t.ticker for t in valuation.transactions} == {"PETR4"}

    def test_patrimony_and_twr(self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any) -> None:
        yf = FakeYfinance({"PETR4.SA": [bar("2026-01-05", "20"), bar("2026-07-01", "25")]})
        valuation = build_portfolio_valuation(
            conn, make_dispatcher(tmp_path, yfinance=yf), start=date(2026, 1, 5), end=date(2026, 7, 20)
        )
        assert patrimony_at(valuation, date(2026, 7, 20)) == Decimal("250")
        assert patrimony_at(valuation, date(2026, 1, 2)) == _ZERO  # antes da primeira compra
        assert portfolio_twr(valuation) == Decimal("0.25")
        series = patrimony_series(valuation, [date(2026, 1, 2), date(2026, 1, 5), date(2026, 7, 20)])
        assert [p.value for p in series] == [_ZERO, Decimal("200"), Decimal("250")]

    def test_a_series_that_starts_after_the_position_is_excluded(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        # O provider pode devolver uma serie curta demais para a janela (listagem
        # nova, simbolo fino). Sem tratar isso, o valuator so descobre no meio da
        # caminhada do TWR e estoura com ValueError, que nenhum frontend espera:
        # o comando termina em traceback e a interface morre junto.
        yf = FakeYfinance({"PETR4.SA": [bar("2026-07-01", "25"), bar("2026-07-15", "26")]})
        valuation = build_portfolio_valuation(
            conn, make_dispatcher(tmp_path, yfinance=yf), start=date(2026, 1, 5), end=date(2026, 7, 20)
        )
        assert valuation.excluded == ["PETR4", "TESOURO SELIC 2029"]
        assert valuation.valuator is None
        assert portfolio_twr(valuation) is None

    def test_a_position_opened_inside_the_window_only_needs_history_from_there(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        # Comprado depois do inicio da janela: exigir preco no inicio dela
        # excluiria uma posicao perfeitamente avaliavel.
        AssetRepository(conn).add("VALE3", Decimal("0.2"))
        # Meio-dia UTC: a sessao le TIMESTAMPTZ em America/Sao_Paulo, e meia-noite
        # UTC regrediria a data local para o dia anterior ao da primeira barra.
        TransactionRepository(conn).add_buy(
            "VALE3", shares=Decimal("5"), unit_price=Decimal("60"), date=datetime(2026, 6, 10, 12, tzinfo=UTC)
        )
        yf = FakeYfinance(
            {
                "PETR4.SA": [bar("2026-01-05", "20"), bar("2026-07-01", "25")],
                "VALE3.SA": [bar("2026-06-10", "60"), bar("2026-07-01", "66")],
            }
        )
        valuation = build_portfolio_valuation(
            conn, make_dispatcher(tmp_path, yfinance=yf), start=date(2026, 1, 5), end=date(2026, 7, 20)
        )
        assert valuation.excluded == ["TESOURO SELIC 2029"]
        assert patrimony_at(valuation, date(2026, 7, 20)) == Decimal("250") + Decimal("330")

    def test_all_excluded_gives_none(self, conn: psycopg.Connection[DictRow], tmp_path: Any) -> None:
        from bogle.domain.assets import AssetType

        AssetRepository(conn).add("TESOURO IPCA 2035", Decimal("0.5"), asset_type=AssetType.TESOURO)
        TransactionRepository(conn).add_buy(
            "TESOURO IPCA 2035", shares=Decimal("1"), unit_price=Decimal("1000"), date=datetime(2026, 1, 5, tzinfo=UTC)
        )
        valuation = build_portfolio_valuation(
            conn, make_dispatcher(tmp_path), start=date(2026, 1, 5), end=date(2026, 7, 20)
        )
        assert valuation.valuator is None
        assert portfolio_twr(valuation) is None
        assert patrimony_series(valuation, [date(2026, 7, 20)]) == []


class TestFirstTransactionDate:
    def test_min_date(self) -> None:
        from tests.test_dividends import make_buy

        txns = [make_buy("PETR4", "2026-03-10"), make_buy("PETR4", "2026-01-05")]
        assert first_transaction_date(txns) == date(2026, 1, 5)

    def test_empty(self) -> None:
        assert first_transaction_date([]) is None


class TestProviderShortSeries:
    """Yahoo answers a dated range with just its last weeks now and then."""

    class ShortThenFull:
        """Short on the dated request, complete when asked for the whole series."""

        def __init__(self, short: list[HistPoint], full: list[HistPoint]) -> None:
            self.short, self.full = short, full
            self.calls: list[str] = []

        def get_quote(self, symbol: str) -> Any:
            raise QuoteNotFoundError(symbol, provider="fake")

        def get_history(self, symbol: str, **kwargs: Any) -> list[HistPoint]:
            dated = kwargs.get("start") is not None
            self.calls.append("dated" if dated else kwargs.get("range_", "max"))
            return list(self.short if dated else self.full)

    def test_a_short_answer_is_asked_again_the_other_way(
        self, conn: psycopg.Connection[DictRow], tmp_path: Any
    ) -> None:
        AssetRepository(conn).add("PETR4", Decimal("0.5"))
        TransactionRepository(conn).add_buy(
            "PETR4", shares=Decimal("10"), unit_price=Decimal("20"), date=datetime(2026, 1, 5, 12, tzinfo=UTC)
        )
        yf = self.ShortThenFull(
            short=[bar("2026-07-01", "25")],  # so as ultimas semanas
            full=[bar("2026-01-05", "20"), bar("2026-07-01", "25")],
        )
        valuation = build_portfolio_valuation(
            conn, make_dispatcher(tmp_path, yfinance=yf), start=date(2026, 1, 5), end=date(2026, 7, 20)
        )
        assert yf.calls == ["dated", "max"]  # pediu de novo, de outro jeito
        assert valuation.excluded == []
        assert patrimony_at(valuation, date(2026, 7, 20)) == Decimal("250")

    def test_a_series_that_covers_is_not_asked_twice(self, conn: psycopg.Connection[DictRow], tmp_path: Any) -> None:
        AssetRepository(conn).add("PETR4", Decimal("0.5"))
        TransactionRepository(conn).add_buy(
            "PETR4", shares=Decimal("10"), unit_price=Decimal("20"), date=datetime(2026, 1, 5, 12, tzinfo=UTC)
        )
        yf = self.ShortThenFull(short=[bar("2026-01-05", "20")], full=[])
        build_portfolio_valuation(
            conn, make_dispatcher(tmp_path, yfinance=yf), start=date(2026, 1, 5), end=date(2026, 7, 20)
        )
        assert yf.calls == ["dated"]

    def test_the_reason_says_which_of_the_three_it_is(self, conn: psycopg.Connection[DictRow], tmp_path: Any) -> None:
        # Tres causas moram sob "sem historico": so duas valem uma segunda tentativa.
        from bogle.domain.assets import AssetType

        assets = AssetRepository(conn)
        assets.add("PETR4", Decimal("0.5"))
        assets.add("TESOURO SELIC 2029", Decimal("0.3"), asset_type=AssetType.TESOURO)
        transactions = TransactionRepository(conn)
        transactions.add_buy(
            "PETR4", shares=Decimal("10"), unit_price=Decimal("20"), date=datetime(2026, 1, 5, 12, tzinfo=UTC)
        )
        transactions.add_buy(
            "TESOURO SELIC 2029",
            shares=Decimal("1"),
            unit_price=Decimal("1000"),
            date=datetime(2026, 1, 5, 12, tzinfo=UTC),
        )
        yf = FakeYfinance({"PETR4.SA": [bar("2026-07-01", "25")]})
        valuation = build_portfolio_valuation(
            conn, make_dispatcher(tmp_path, yfinance=yf), start=date(2026, 1, 5), end=date(2026, 7, 20)
        )
        assert valuation.reasons["PETR4"] == SHORT_SERIES
        assert valuation.reasons["TESOURO SELIC 2029"] == NO_SOURCE
        assert sorted(valuation.reasons) == valuation.excluded

    def test_an_empty_answer_is_its_own_reason(self, conn: psycopg.Connection[DictRow], tmp_path: Any) -> None:
        AssetRepository(conn).add("PETR4", Decimal("0.5"))
        TransactionRepository(conn).add_buy(
            "PETR4", shares=Decimal("10"), unit_price=Decimal("20"), date=datetime(2026, 1, 5, 12, tzinfo=UTC)
        )
        valuation = build_portfolio_valuation(
            conn, make_dispatcher(tmp_path), start=date(2026, 1, 5), end=date(2026, 7, 20)
        )
        assert valuation.reasons == {"PETR4": NOTHING_RETURNED}
