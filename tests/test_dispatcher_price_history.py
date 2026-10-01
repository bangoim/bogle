"""The dispatcher over a price store (issue #82).

The store here is an in-memory fake with the repository's write rules (add-only,
replace, confirm), so these tests pin the dispatcher's *policy*: when it goes to
a provider, which one, and what it is allowed to change. The rules themselves are
tested against Postgres in ``test_price_history_repository.py``.

Reference day: Thursday 2026-10-01. The last 30 sessions before it run from
2026-08-19 to 2026-09-30.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, override

import pytest

from bogle.analytics.business_days import is_business_day
from bogle.data.cache import DiskCache
from bogle.data.dispatcher import PriceDispatcher
from bogle.data.models import HistPoint, Quote, StoredClose, StoredSpan, TesouroQuote
from bogle.domain.assets import Asset, AssetType
from bogle.domain.errors import MarketDataError, NetworkError, QuoteNotFoundError

TODAY = date(2026, 10, 1)
YESTERDAY = date(2026, 9, 30)
WINDOW_START = date(2026, 8, 19)


def bar(day: date, close: str) -> HistPoint:
    # Meia-noite de Sao Paulo em UTC, como o yfinance entrega.
    moment = datetime(day.year, day.month, day.day, 3, tzinfo=UTC)
    value = Decimal(close)
    return HistPoint(date=moment, open=value, high=value, low=value, close=value, volume=1)


def sessions(start: date, end: date, close: str = "100") -> list[HistPoint]:
    """A bar per business day in ``[start, end]``, all at ``close``."""
    out = []
    day = start
    while day <= end:
        if is_business_day(day):
            out.append(bar(day, close))
        day += timedelta(days=1)
    return out


class FakeHistory:
    """Bars per symbol, filtered like the providers filter them; logs each request.

    ``fail`` makes every history request raise, like a provider that is down.
    """

    def __init__(
        self,
        bars: dict[str, list[HistPoint]] | None = None,
        *,
        quotes: dict[str, Quote] | None = None,
        fail: bool = False,
    ) -> None:
        self.bars = bars or {}
        self.quotes = quotes or {}
        self.fail = fail
        self.calls: list[tuple[str, str | None, str | None, str]] = []
        self.quote_calls: list[str] = []

    def get_quote(self, symbol: str) -> Quote:
        self.quote_calls.append(symbol)
        if self.fail:
            raise NetworkError("fake", "fora do ar")
        if symbol not in self.quotes:
            raise QuoteNotFoundError(symbol, provider="fake")
        return self.quotes[symbol]

    def get_index_quote(self, index: str) -> Quote:
        raise QuoteNotFoundError(index, provider="fake")

    def get_history(
        self,
        symbol: str,
        *,
        range_: str = "3mo",
        interval: str = "1d",
        start: str | None = None,
        end: str | None = None,
    ) -> list[HistPoint]:
        self.calls.append((symbol, start, end, range_))
        if self.fail:
            raise NetworkError("fake", "fora do ar")
        points = self.bars.get(symbol, [])
        if start is not None:
            points = [p for p in points if p.date.date() >= date.fromisoformat(start)]
        if end is not None:  # exclusivo, como no yfinance
            points = [p for p in points if p.date.date() < date.fromisoformat(end)]
        if not points:
            raise QuoteNotFoundError(symbol, provider="fake")
        return list(points)


class FakeStore:
    """``price_history`` in memory, with the repository's write rules."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[date, tuple[Decimal, str, date]]] = {}

    def put(self, symbol: str, day: date, close: str, source: str = "yfinance", loaded_on: date = YESTERDAY) -> None:
        self.rows.setdefault(symbol, {})[day] = (Decimal(close), source, loaded_on)

    def row(self, symbol: str, day: date) -> tuple[Decimal, str, date] | None:
        return self.rows.get(symbol, {}).get(day)

    def span(self, symbol: str) -> StoredSpan | None:
        table = self.rows.get(symbol)
        if not table:
            return None
        return StoredSpan(first=min(table), last=max(table), loaded_on=max(row[2] for row in table.values()))

    def closes(self, symbol: str, start: date, end: date) -> list[StoredClose]:
        table = self.rows.get(symbol, {})
        return [StoredClose(day, table[day][0], table[day][1]) for day in sorted(table) if start <= day <= end]

    def save(
        self,
        symbol: str,
        closes: Sequence[StoredClose],
        *,
        loaded_on: date,
        replace: bool = False,
        confirm: tuple[date, date] | None = None,
    ) -> None:
        table = self.rows.setdefault(symbol, {})
        for row in closes:
            close = row.close.quantize(Decimal("0.01"))
            old = table.get(row.date)
            if old is None:
                table[row.date] = (close, row.source, loaded_on)
            elif replace:
                table[row.date] = (close, old[1] if old[0] == close else row.source, loaded_on)
        if confirm is not None:
            for day, (close, source, _) in list(table.items()):
                if confirm[0] <= day <= confirm[1]:
                    table[day] = (close, source, loaded_on)


class FakeTesouro:
    def get_quote(self, title: str) -> TesouroQuote:
        raise QuoteNotFoundError(title, provider="tesouro")


class FakeBcb:
    def get_cdi(self, start: date | None = None, end: date | None = None) -> list[Any]:
        return []

    def get_selic(self, start: date | None = None, end: date | None = None) -> list[Any]:
        return []

    def get_ipca(self, start: date | None = None, end: date | None = None) -> list[Any]:
        return []


def make_dispatcher(
    tmp_path: Path,
    *,
    yahoo: FakeHistory | None = None,
    brapi: FakeHistory | None = None,
    store: FakeStore | None = None,
    today: date = TODAY,
) -> PriceDispatcher:
    return PriceDispatcher(
        brapi=brapi or FakeHistory(),
        yfinance=yahoo or FakeHistory(),
        tesouro=FakeTesouro(),
        bcb=FakeBcb(),
        quote_cache=DiskCache("quotes", base_dir=tmp_path),
        price_store=store,
        clock=lambda: today,
    )


def etf(ticker: str) -> Asset:
    return Asset(ticker=ticker, target_weight=Decimal("0.3"), asset_type=AssetType.ETF)


def pricing(dispatcher: PriceDispatcher, ticker: str, *, start: date, covering: date | None = None) -> Any:
    return dispatcher.build_historical_pricing(
        etf(ticker), unit_principal=Decimal("0"), start=start, end=TODAY, covering=covering
    )


class TestFirstLoad:
    def test_an_empty_table_is_filled_once_and_the_rest_of_the_day_reads_it(self, tmp_path: Path) -> None:
        yahoo = FakeHistory({"B5P211.SA": sessions(date(2026, 6, 1), TODAY, "112.50")})
        brapi = FakeHistory({"B5P211": sessions(date(2026, 7, 1), TODAY, "112.50")})
        store = FakeStore()
        dispatcher = make_dispatcher(tmp_path, yahoo=yahoo, brapi=brapi, store=store)

        first = pricing(dispatcher, "B5P211", start=date(2026, 7, 1), covering=date(2026, 7, 1))
        assert first.series_end == YESTERDAY
        assert len(yahoo.calls) == 1 and len(brapi.calls) == 1

        # Segunda tela do dia: so leitura, nenhum provedor.
        second = pricing(dispatcher, "B5P211", start=date(2026, 7, 1), covering=date(2026, 7, 1))
        assert second.series_end == YESTERDAY
        assert len(yahoo.calls) == 1 and len(brapi.calls) == 1

    def test_the_long_history_comes_from_yahoo_and_the_window_from_brapi(self, tmp_path: Path) -> None:
        yahoo = FakeHistory({"B5P211.SA": sessions(date(2026, 6, 1), TODAY, "112.50")})
        brapi = FakeHistory({"B5P211": sessions(date(2026, 7, 1), TODAY, "112.60")})
        store = FakeStore()
        pricing(make_dispatcher(tmp_path, yahoo=yahoo, brapi=brapi, store=store), "B5P211", start=date(2026, 7, 1))

        # Fora da janela fica o que o Yahoo trouxe; dentro dela, a brapi corrigiu.
        assert store.row("B5P211", date(2026, 8, 18)) == (Decimal("112.50"), "yfinance", TODAY)
        assert store.row("B5P211", WINDOW_START) == (Decimal("112.60"), "brapi", TODAY)
        assert store.row("B5P211", YESTERDAY) == (Decimal("112.60"), "brapi", TODAY)

    def test_todays_bar_never_reaches_the_table(self, tmp_path: Path) -> None:
        # Durante o pregao os dois devolvem uma barra parcial de hoje.
        yahoo = FakeHistory({"B5P211.SA": sessions(date(2026, 9, 1), TODAY)})
        brapi = FakeHistory({"B5P211": sessions(date(2026, 9, 1), TODAY)})
        store = FakeStore()
        pricing(make_dispatcher(tmp_path, yahoo=yahoo, brapi=brapi, store=store), "B5P211", start=date(2026, 9, 1))
        assert store.row("B5P211", TODAY) is None
        assert max(store.rows["B5P211"]) == YESTERDAY

    def test_closes_are_kept_in_cents(self, tmp_path: Path) -> None:
        yahoo = FakeHistory({"B5P211.SA": [bar(date(2026, 7, 1), "112.95999908")]})
        store = FakeStore()
        pricing(make_dispatcher(tmp_path, yahoo=yahoo, store=store), "B5P211", start=date(2026, 7, 1))
        assert store.row("B5P211", date(2026, 7, 1)) == (Decimal("112.96"), "yfinance", TODAY)


class TestDailyRevalidation:
    def seeded(self) -> FakeStore:
        """What yesterday's run left: Yahoo closes up to D-2, loaded yesterday."""
        store = FakeStore()
        for point in sessions(date(2026, 8, 3), date(2026, 9, 29), "52.00"):
            store.put("NB1011", point.date.date(), "52.00", loaded_on=YESTERDAY)
        return store

    def test_a_session_yahoo_skipped_comes_from_brapi(self, tmp_path: Path) -> None:
        # O caso de 30/09/2026: o Yahoo pulou o pregao, a brapi tinha.
        store = self.seeded()
        brapi = FakeHistory({"NB1011": [*sessions(WINDOW_START, date(2026, 9, 29), "52.00"), bar(YESTERDAY, "52.41")]})
        result = pricing(make_dispatcher(tmp_path, brapi=brapi, store=store), "NB1011", start=date(2026, 8, 3))
        assert store.row("NB1011", YESTERDAY) == (Decimal("52.41"), "brapi", TODAY)
        assert result.series_end == YESTERDAY

    def test_a_close_brapi_disagrees_with_is_corrected_and_an_equal_one_keeps_its_source(self, tmp_path: Path) -> None:
        store = self.seeded()
        window = sessions(WINDOW_START, date(2026, 9, 29), "52.00")
        window = [bar(p.date.date(), "51.86") if p.date.date() == date(2026, 9, 25) else p for p in window]
        pricing(
            make_dispatcher(tmp_path, brapi=FakeHistory({"NB1011": window}), store=store),
            "NB1011",
            start=date(2026, 8, 3),
        )
        assert store.row("NB1011", date(2026, 9, 25)) == (Decimal("51.86"), "brapi", TODAY)
        assert store.row("NB1011", date(2026, 9, 24)) == (Decimal("52.00"), "yfinance", TODAY)

    def test_every_row_of_the_window_is_stamped_even_one_brapi_does_not_have(self, tmp_path: Path) -> None:
        store = self.seeded()
        # A brapi nao tem 2026-09-29; a linha que o Yahoo gravou continua, e conta
        # como conferida hoje como as outras.
        brapi = FakeHistory({"NB1011": sessions(WINDOW_START, date(2026, 9, 28), "52.00")})
        pricing(make_dispatcher(tmp_path, brapi=brapi, store=store), "NB1011", start=date(2026, 8, 3))
        assert store.row("NB1011", date(2026, 9, 29)) == (Decimal("52.00"), "yfinance", TODAY)

    def test_nothing_before_the_window_is_touched(self, tmp_path: Path) -> None:
        store = self.seeded()
        # O "3mo" da brapi vai alem dos 30 pregoes; o que ficar antes da janela e
        # definitivo, mesmo que ela discorde.
        brapi = FakeHistory({"NB1011": sessions(date(2026, 8, 3), date(2026, 9, 29), "60.00")})
        pricing(make_dispatcher(tmp_path, brapi=brapi, store=store), "NB1011", start=date(2026, 8, 3))
        assert store.row("NB1011", date(2026, 8, 18)) == (Decimal("52.00"), "yfinance", YESTERDAY)
        assert store.row("NB1011", WINDOW_START) == (Decimal("60.00"), "brapi", TODAY)

    def test_the_next_day_loads_again(self, tmp_path: Path) -> None:
        store = self.seeded()
        brapi = FakeHistory({"NB1011": sessions(WINDOW_START, YESTERDAY, "52.00")})
        dispatcher = make_dispatcher(tmp_path, brapi=brapi, store=store)
        pricing(dispatcher, "NB1011", start=date(2026, 8, 3))
        pricing(dispatcher, "NB1011", start=date(2026, 8, 3))
        assert len(brapi.calls) == 1
        tomorrow = make_dispatcher(tmp_path, brapi=brapi, store=store, today=date(2026, 10, 2))
        pricing(tomorrow, "NB1011", start=date(2026, 8, 3))
        assert len(brapi.calls) == 2

    def test_an_absence_under_90_days_comes_whole_from_brapi(self, tmp_path: Path) -> None:
        # Fechado de 31/07 a 01/10: a brapi gratuita (3 meses) alcanca tudo, e o
        # Yahoo nem e chamado.
        store = FakeStore()
        for point in sessions(date(2026, 6, 1), date(2026, 7, 31)):
            store.put("NB1011", point.date.date(), "50.00", loaded_on=date(2026, 7, 31))
        yahoo = FakeHistory({"NB1011.SA": sessions(date(2026, 6, 1), TODAY, "51.00")})
        brapi = FakeHistory({"NB1011": sessions(date(2026, 7, 1), YESTERDAY, "52.00")})
        pricing(make_dispatcher(tmp_path, yahoo=yahoo, brapi=brapi, store=store), "NB1011", start=date(2026, 6, 1))
        assert yahoo.calls == []
        assert store.row("NB1011", date(2026, 8, 3)) == (Decimal("52.00"), "brapi", TODAY)
        assert store.row("NB1011", WINDOW_START) == (Decimal("52.00"), "brapi", TODAY)
        # O que ja estava no banco antes da ultima carga nao e tocado.
        assert store.row("NB1011", date(2026, 7, 31)) == (Decimal("50.00"), "yfinance", date(2026, 7, 31))

    def test_an_absence_of_90_days_or_more_fills_the_older_part_from_yahoo(self, tmp_path: Path) -> None:
        # Ultima carga em 30/06: de 01/07 a 02/07 fica antes do alcance da brapi
        # (hoje - 90 dias = 03/07), e vem do Yahoo; o resto, da brapi.
        store = FakeStore()
        for point in sessions(date(2026, 6, 1), date(2026, 6, 30)):
            store.put("NB1011", point.date.date(), "50.00", loaded_on=date(2026, 6, 30))
        yahoo = FakeHistory({"NB1011.SA": sessions(date(2026, 6, 1), TODAY, "51.00")})
        brapi = FakeHistory({"NB1011": sessions(date(2026, 7, 1), YESTERDAY, "52.00")})
        pricing(make_dispatcher(tmp_path, yahoo=yahoo, brapi=brapi, store=store), "NB1011", start=date(2026, 6, 1))
        assert yahoo.calls == [("NB1011.SA", "2026-07-01", "2026-07-03", "3mo")]
        assert store.row("NB1011", date(2026, 7, 2)) == (Decimal("51.00"), "yfinance", TODAY)
        assert store.row("NB1011", date(2026, 7, 3)) == (Decimal("52.00"), "brapi", TODAY)
        assert store.row("NB1011", date(2026, 8, 18)) == (Decimal("52.00"), "brapi", TODAY)


class TestProvidersDown:
    def seeded(self) -> FakeStore:
        store = FakeStore()
        for point in sessions(date(2026, 8, 3), date(2026, 9, 29), "52.00"):
            store.put("NB1011", point.date.date(), "52.00", loaded_on=YESTERDAY)
        return store

    def test_with_brapi_down_yahoo_covers_what_is_missing_without_correcting(self, tmp_path: Path) -> None:
        store = self.seeded()
        yahoo = FakeHistory({"NB1011.SA": sessions(WINDOW_START, YESTERDAY, "53.00")})
        brapi = FakeHistory(fail=True)
        pricing(make_dispatcher(tmp_path, yahoo=yahoo, brapi=brapi, store=store), "NB1011", start=date(2026, 8, 3))
        assert store.row("NB1011", date(2026, 9, 29)) == (Decimal("52.00"), "yfinance", TODAY)
        assert store.row("NB1011", YESTERDAY) == (Decimal("53.00"), "yfinance", TODAY)

    def test_with_both_down_nothing_is_written_and_the_next_call_tries_again(self, tmp_path: Path) -> None:
        store = self.seeded()
        before = dict(store.rows["NB1011"])
        yahoo, brapi = FakeHistory(fail=True), FakeHistory(fail=True)
        dispatcher = make_dispatcher(tmp_path, yahoo=yahoo, brapi=brapi, store=store)
        pricing(dispatcher, "NB1011", start=date(2026, 8, 3))
        assert store.rows["NB1011"] == before
        pricing(dispatcher, "NB1011", start=date(2026, 8, 3))
        assert len(brapi.calls) == 2

    def test_opened_yesterday_with_both_down_today_still_prices_from_d_minus_2(self, tmp_path: Path) -> None:
        store = self.seeded()
        dispatcher = make_dispatcher(tmp_path, yahoo=FakeHistory(fail=True), brapi=FakeHistory(fail=True), store=store)
        result = pricing(dispatcher, "NB1011", start=date(2026, 8, 3), covering=date(2026, 8, 3))
        assert result.series_end == date(2026, 9, 29)
        assert result.series_start is None
        # O D-1 que falta sai no fechamento anterior (e a Home diz que esta defasado).
        assert result.valuator({"NB1011": Decimal("10")}, YESTERDAY) == Decimal("520.00")


class TestSeriesStart:
    def test_a_first_session_missing_from_yahoo_comes_from_brapi(self, tmp_path: Path) -> None:
        # O caso do MUND11: estreou em 30/09, e o Yahoo so tinha o pregao de hoje.
        yahoo = FakeHistory({"MUND11.SA": [bar(TODAY, "100.23")]})
        brapi = FakeHistory({"MUND11": [bar(YESTERDAY, "99.07"), bar(TODAY, "100.19")]})
        store = FakeStore()
        result = pricing(
            make_dispatcher(tmp_path, yahoo=yahoo, brapi=brapi, store=store),
            "MUND11",
            start=date(2026, 9, 1),
            covering=YESTERDAY,
        )
        assert result.valuator is not None
        assert result.series_start is None
        assert result.valuator({"MUND11": Decimal("1")}, YESTERDAY) == Decimal("99.07")

    def test_a_series_that_starts_after_the_position_is_reported_once_asked_whole(self, tmp_path: Path) -> None:
        yahoo = FakeHistory({"NEW11.SA": sessions(date(2026, 9, 15), YESTERDAY)})
        brapi = FakeHistory({"NEW11": sessions(date(2026, 9, 15), YESTERDAY)})
        result = pricing(
            make_dispatcher(tmp_path, yahoo=yahoo, brapi=brapi, store=FakeStore()),
            "NEW11",
            start=date(2026, 9, 1),
            covering=date(2026, 9, 1),
        )
        assert result.series_start == date(2026, 9, 15)
        # A serie inteira foi pedida (o "max"), e so por isso o comeco e definitivo.
        assert any(call[3] == "max" for call in yahoo.calls)

    def test_a_failed_whole_series_request_is_not_definitive(self, tmp_path: Path) -> None:
        class DatedOnly(FakeHistory):
            @override
            def get_history(
                self,
                symbol: str,
                *,
                range_: str = "3mo",
                interval: str = "1d",
                start: str | None = None,
                end: str | None = None,
            ) -> list[HistPoint]:
                if start is None:
                    raise NetworkError("fake", "fora do ar")
                return super().get_history(symbol, range_=range_, interval=interval, start=start, end=end)

        yahoo = DatedOnly({"NEW11.SA": sessions(date(2026, 9, 15), YESTERDAY)})
        result = pricing(
            make_dispatcher(tmp_path, yahoo=yahoo, store=FakeStore()),
            "NEW11",
            start=date(2026, 9, 1),
            covering=date(2026, 9, 1),
        )
        assert result.series_start is None

    def test_the_freshness_lookup_does_not_chase_a_start_the_series_never_had(self, tmp_path: Path) -> None:
        yahoo = FakeHistory({"NEW11.SA": sessions(date(2026, 9, 15), YESTERDAY)})
        brapi = FakeHistory({"NEW11": sessions(date(2026, 9, 15), YESTERDAY)})
        dispatcher = make_dispatcher(tmp_path, yahoo=yahoo, brapi=brapi, store=FakeStore())
        assert dispatcher.latest_history_date("NEW11", date(2026, 1, 1), TODAY) == YESTERDAY
        calls = len(yahoo.calls)
        assert dispatcher.latest_history_date("NEW11", date(2026, 1, 1), TODAY) == YESTERDAY
        assert len(yahoo.calls) == calls


class TestIndices:
    def test_ibov_is_kept_under_its_symbol(self, tmp_path: Path) -> None:
        yahoo = FakeHistory({"^BVSP": sessions(date(2026, 6, 1), TODAY, "140000")})
        brapi = FakeHistory({"^BVSP": sessions(date(2026, 7, 1), TODAY, "140100")})
        store = FakeStore()
        dispatcher = make_dispatcher(tmp_path, yahoo=yahoo, brapi=brapi, store=store)
        levels = dispatcher.get_index_series("IBOV", [date(2026, 7, 1), YESTERDAY])
        assert levels == [Decimal("140000.00"), Decimal("140100.00")]
        assert brapi.calls[0][0] == "^BVSP"
        assert set(store.rows) == {"^BVSP"}
        dispatcher.get_index_series("IBOV", [date(2026, 7, 1), YESTERDAY])
        assert len(yahoo.calls) == 1 and len(brapi.calls) == 1

    def test_other_indices_never_touch_the_table(self, tmp_path: Path) -> None:
        store = FakeStore()
        dispatcher = make_dispatcher(tmp_path, store=store)
        with pytest.raises(MarketDataError):
            dispatcher.get_index_return("IFIX", date(2026, 7, 1), YESTERDAY)
        assert store.rows == {}


class TestWithoutAStore:
    def test_every_call_goes_to_yahoo_as_before(self, tmp_path: Path) -> None:
        yahoo = FakeHistory({"B5P211.SA": sessions(date(2026, 9, 1), TODAY)})
        brapi = FakeHistory({"B5P211": sessions(date(2026, 9, 1), TODAY)})
        dispatcher = make_dispatcher(tmp_path, yahoo=yahoo, brapi=brapi)
        first = pricing(dispatcher, "B5P211", start=date(2026, 9, 1))
        pricing(dispatcher, "B5P211", start=date(2026, 9, 1))
        # Sem banco nao ha "fechamento de hoje" a recusar: a barra parcial entra.
        assert first.series_end == TODAY
        assert len(yahoo.calls) == 2 and brapi.calls == []
