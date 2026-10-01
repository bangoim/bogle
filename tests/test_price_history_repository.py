"""``price_history`` against the test database (issue #82): the write rules the
dispatcher relies on, and the store that opens a connection per call."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import psycopg
import pytest
from psycopg import errors as pg_errors
from psycopg.rows import DictRow

from bogle.data.models import StoredClose
from bogle.db import get_connection
from bogle.repositories.price_history import PriceHistoryRepository, PriceHistoryStore
from tests.conftest import TEST_DATABASE_URL

MON, TUE, WED = date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30)
YESTERDAY, TODAY = date(2026, 9, 30), date(2026, 10, 1)


def close(day: date, value: str, source: str = "yfinance") -> StoredClose:
    return StoredClose(date=day, close=Decimal(value), source=source)


@pytest.fixture
def history(conn: psycopg.Connection[DictRow]) -> PriceHistoryRepository:
    return PriceHistoryRepository(conn)


def rows(conn: psycopg.Connection[DictRow], symbol: str = "B5P211") -> dict[date, tuple[Decimal, str, date]]:
    with conn.cursor() as cur:
        cur.execute("SELECT date, close, source, loaded_on FROM price_history WHERE symbol = %s", (symbol,))
        return {row["date"]: (row["close"], row["source"], row["loaded_on"]) for row in cur.fetchall()}


class TestReads:
    def test_an_unknown_symbol_has_no_span(self, history: PriceHistoryRepository) -> None:
        assert history.span("B5P211") is None

    def test_span_is_first_last_and_the_latest_load(self, history: PriceHistoryRepository) -> None:
        history.save("B5P211", [close(MON, "112.69")], loaded_on=date(2026, 9, 29))
        history.save("B5P211", [close(WED, "113.21")], loaded_on=YESTERDAY)
        span = history.span("B5P211")
        assert span is not None
        assert (span.first, span.last, span.loaded_on) == (MON, WED, YESTERDAY)

    def test_closes_come_in_order_and_inside_the_range(self, history: PriceHistoryRepository) -> None:
        history.save("B5P211", [close(WED, "113.21"), close(MON, "112.69"), close(TUE, "112.96")], loaded_on=TODAY)
        assert [row.date for row in history.closes("B5P211", MON, TUE)] == [MON, TUE]

    def test_symbols_do_not_mix(self, history: PriceHistoryRepository) -> None:
        history.save("B5P211", [close(MON, "112.69")], loaded_on=TODAY)
        history.save("^BVSP", [close(MON, "140000")], loaded_on=TODAY)
        assert [row.close for row in history.closes("^BVSP", MON, WED)] == [Decimal("140000.00")]


class TestWrites:
    def test_closes_are_rounded_to_cents(
        self, history: PriceHistoryRepository, conn: psycopg.Connection[DictRow]
    ) -> None:
        history.save("B5P211", [close(TUE, "112.95999908")], loaded_on=TODAY)
        assert rows(conn)[TUE][0] == Decimal("112.96")

    def test_add_only_never_overwrites(
        self, history: PriceHistoryRepository, conn: psycopg.Connection[DictRow]
    ) -> None:
        history.save("B5P211", [close(TUE, "112.96")], loaded_on=YESTERDAY)
        history.save("B5P211", [close(TUE, "999"), close(WED, "113.21")], loaded_on=TODAY)
        assert rows(conn) == {
            TUE: (Decimal("112.96"), "yfinance", YESTERDAY),
            WED: (Decimal("113.21"), "yfinance", TODAY),
        }

    def test_replace_corrects_a_changed_close_and_takes_its_source(
        self, history: PriceHistoryRepository, conn: psycopg.Connection[DictRow]
    ) -> None:
        history.save("B5P211", [close(TUE, "112.90")], loaded_on=YESTERDAY)
        history.save("B5P211", [close(TUE, "112.96", "brapi")], loaded_on=TODAY, replace=True)
        assert rows(conn)[TUE] == (Decimal("112.96"), "brapi", TODAY)

    def test_replace_with_the_same_close_keeps_who_wrote_it_first(
        self, history: PriceHistoryRepository, conn: psycopg.Connection[DictRow]
    ) -> None:
        history.save("B5P211", [close(TUE, "112.96")], loaded_on=YESTERDAY)
        history.save("B5P211", [close(TUE, "112.96", "brapi")], loaded_on=TODAY, replace=True)
        assert rows(conn)[TUE] == (Decimal("112.96"), "yfinance", TODAY)

    def test_confirm_stamps_every_row_of_the_range_and_nothing_else(
        self, history: PriceHistoryRepository, conn: psycopg.Connection[DictRow]
    ) -> None:
        history.save("B5P211", [close(MON, "112.69"), close(TUE, "112.96"), close(WED, "113.21")], loaded_on=YESTERDAY)
        history.save("^BVSP", [close(TUE, "140000")], loaded_on=YESTERDAY)
        # Nenhum close novo: so a marca de "conferido hoje" em TUE..WED do B5P211.
        history.save("B5P211", [], loaded_on=TODAY, confirm=(TUE, WED))
        stored = rows(conn)
        assert stored[MON][2] == YESTERDAY
        assert stored[TUE][2] == stored[WED][2] == TODAY
        assert rows(conn, "^BVSP")[TUE][2] == YESTERDAY

    def test_a_bad_row_takes_the_whole_batch_down(
        self, history: PriceHistoryRepository, conn: psycopg.Connection[DictRow]
    ) -> None:
        history.save("B5P211", [close(MON, "112.69")], loaded_on=YESTERDAY)
        with pytest.raises(pg_errors.CheckViolation):
            history.save(
                "B5P211", [close(TUE, "112.96"), close(WED, "0")], loaded_on=TODAY, replace=True, confirm=(MON, WED)
            )
        # Nem a linha boa, nem a marca de carga: uma janela meio gravada leria
        # como conferida hoje.
        assert rows(conn) == {MON: (Decimal("112.69"), "yfinance", YESTERDAY)}


class TestStore:
    @pytest.fixture
    def opened(self, conn: psycopg.Connection[DictRow]) -> Iterator[list[psycopg.Connection[DictRow]]]:
        connections: list[psycopg.Connection[DictRow]] = []
        yield connections
        for c in connections:
            c.close()

    def test_each_operation_opens_and_closes_its_own_connection(
        self, opened: list[psycopg.Connection[DictRow]]
    ) -> None:
        def connect() -> psycopg.Connection[DictRow]:
            c = get_connection(TEST_DATABASE_URL)
            opened.append(c)
            return c

        store = PriceHistoryStore(connect)
        store.save("B5P211", [close(TUE, "112.96")], loaded_on=TODAY, confirm=(TUE, TUE))
        assert [row.close for row in store.closes("B5P211", MON, WED)] == [Decimal("112.96")]
        span = store.span("B5P211")
        assert span is not None and span.loaded_on == TODAY
        assert len(opened) == 3
        assert all(c.closed for c in opened)
