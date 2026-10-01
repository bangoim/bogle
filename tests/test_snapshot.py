"""Tests for ``bogle.reports.snapshot`` (issue #73): the composition behind
``bogle position`` and the TUI's Position screen — position + month profit +
income received (12m).

The per-ticker numbers themselves are covered by ``tests/test_position.py`` and
the month-profit math by ``tests/test_summary.py``; what matters here is the
wiring, and that ``--no-prices`` (``dispatcher=None``) drops exactly the parts
that need price history.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from psycopg.rows import DictRow

from bogle.data.cache import DiskCache
from bogle.data.dispatcher import PriceDispatcher
from bogle.data.models import Quote, TesouroQuote
from bogle.domain.errors import QuoteNotFoundError
from bogle.reports.snapshot import compute_snapshot
from bogle.repositories.assets import AssetRepository
from bogle.repositories.transactions import TransactionRepository
from tests.test_valuation import FakeBcb, FakeYfinance, bar, make_dispatcher

TODAY = date(2026, 7, 20)  # janela do mes: 2026-06-20 -> 2026-07-20

HISTORY = {
    "PETR4.SA": [
        bar("2025-01-06", "20"),  # compra
        bar("2026-06-19", "24"),  # ultimo fechamento antes do inicio da janela
        bar("2026-07-17", "25"),  # ultimo fechamento antes de hoje
    ]
}


class FakeQuotes:
    """A brapi-like source with one fixed price per ticker."""

    def __init__(self, prices: dict[str, str]) -> None:
        self.prices = prices

    def get_quote(self, symbol: str) -> Quote:
        if symbol not in self.prices:
            raise QuoteNotFoundError(symbol, provider="fake")
        return Quote(
            symbol=symbol,
            price=Decimal(self.prices[symbol]),
            currency="BRL",
            time=datetime(2026, 7, 20, 18, tzinfo=UTC),
            requested_symbol=symbol,
        )

    def get_index_quote(self, index: str) -> Quote:
        raise QuoteNotFoundError(index, provider="fake")


class NoTesouro:
    """A Tesouro source that never answers (no Tesouro title in these fixtures)."""

    def get_quote(self, title: str) -> TesouroQuote:
        raise QuoteNotFoundError(title, provider="fake")


def priced_dispatcher(tmp_path: Any, prices: dict[str, str]) -> PriceDispatcher:
    """Like ``make_dispatcher``, but with live quotes available."""
    return PriceDispatcher(
        brapi=FakeQuotes(prices),
        yfinance=FakeYfinance(dict(HISTORY)),
        tesouro=NoTesouro(),
        bcb=FakeBcb(),
        quote_cache=DiskCache("quotes", base_dir=tmp_path),
    )


@pytest.fixture
def seeded(conn: psycopg.Connection[DictRow]) -> None:
    AssetRepository(conn).add("PETR4", Decimal("0.5"))
    TransactionRepository(conn).add_buy(
        "PETR4", shares=Decimal("10"), unit_price=Decimal("20"), date=datetime(2025, 1, 6, 12, tzinfo=UTC)
    )


class TestComputeSnapshot:
    def test_month_profit_is_the_patrimony_delta(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        dispatcher = make_dispatcher(tmp_path, yfinance=FakeYfinance(dict(HISTORY)))
        snapshot = compute_snapshot(conn, dispatcher, today=TODAY)
        assert snapshot.month_profit == Decimal("10")  # 240 -> 250
        assert snapshot.income_12m == Decimal("0")
        assert snapshot.excluded == []
        assert [p.ticker for p in snapshot.summary.positions] == ["PETR4"]

    def test_income_in_the_window_counts_as_profit_and_as_income(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        TransactionRepository(conn).add_dividend("PETR4", datetime(2026, 7, 1, 12, tzinfo=UTC), Decimal("5"))
        dispatcher = make_dispatcher(tmp_path, yfinance=FakeYfinance(dict(HISTORY)))
        snapshot = compute_snapshot(conn, dispatcher, today=TODAY)
        assert snapshot.month_profit == Decimal("15")  # 250 - 240 + 5 recebidos
        assert snapshot.income_12m == Decimal("5")

    def test_without_a_dispatcher_only_the_priced_parts_are_dropped(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        TransactionRepository(conn).add_dividend("PETR4", datetime(2026, 7, 1, 12, tzinfo=UTC), Decimal("5"))
        snapshot = compute_snapshot(conn, None, today=TODAY)
        assert snapshot.month_profit is None  # exige historico de precos
        assert snapshot.income_12m == Decimal("5")  # sai do ledger
        assert snapshot.excluded == []
        assert snapshot.summary.positions[0].price is None

    def test_ticker_without_history_is_reported_as_excluded(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        snapshot = compute_snapshot(conn, make_dispatcher(tmp_path), today=TODAY)
        assert snapshot.excluded == ["PETR4"]
        assert snapshot.month_profit is None

    def test_has_prices_reports_whether_anything_could_be_valued(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        # O que separa "carteira vale zero" de "nao deu para precificar": os dois
        # frontends decidem por aqui se mostram o total ou "-".
        assert not compute_snapshot(conn, None, today=TODAY).has_prices
        with_prices = compute_snapshot(conn, priced_dispatcher(tmp_path, {"PETR4": "25"}), today=TODAY)
        assert with_prices.has_prices
        assert with_prices.summary.total_value == Decimal("250")

    def test_empty_portfolio(self, conn: psycopg.Connection[DictRow], tmp_path: Any) -> None:
        snapshot = compute_snapshot(conn, make_dispatcher(tmp_path), today=TODAY)
        assert snapshot.summary.positions == []
        assert snapshot.income_12m == Decimal("0")
        # Sem holdings nao existe valuator, entao o lucro do mes e indisponivel
        # (renderizado como "-"), nao zero.
        assert snapshot.month_profit is None
