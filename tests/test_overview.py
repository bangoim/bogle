"""Tests for ``bogle.reports.overview`` (issue #73): the four headline numbers
the TUI opens with, measured at a reference close (D-1).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from psycopg.rows import DictRow

from bogle.domain.assets import AssetType, Indexer
from bogle.reports.overview import compute_overview
from bogle.repositories.assets import AssetRepository
from bogle.repositories.transactions import TransactionRepository
from tests.test_valuation import FakeYfinance, bar, make_dispatcher

AS_OF = date(2026, 7, 20)

HISTORY = {
    "PETR4.SA": [
        bar("2025-01-06", "20"),  # compra
        bar("2025-07-18", "22"),  # ~12m antes da referencia
        bar("2026-07-17", "25"),  # ultima barra antes da referencia
    ]
}


@pytest.fixture
def seeded(conn: psycopg.Connection[DictRow]) -> None:
    AssetRepository(conn).add("PETR4", Decimal("0.5"))
    # Meio-dia UTC: a sessao le TIMESTAMPTZ em America/Sao_Paulo e meia-noite UTC
    # regrediria a data local para o dia anterior a primeira barra do fake.
    TransactionRepository(conn).add_buy(
        "PETR4", shares=Decimal("10"), unit_price=Decimal("20"), date=datetime(2025, 1, 6, 12, tzinfo=UTC)
    )


class TestComputeOverview:
    def test_patrimony_variation_and_returns(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        dispatcher = make_dispatcher(tmp_path, yfinance=FakeYfinance(dict(HISTORY)))
        overview = compute_overview(conn, dispatcher, as_of=AS_OF)

        assert overview.as_of == AS_OF
        assert overview.inception == date(2025, 1, 6)
        assert overview.invested == Decimal("200")  # 10 x 20, sem fees
        assert overview.patrimony == Decimal("250")  # 10 x 25 (fechamento de 17/jul)
        assert overview.variation == Decimal("50")
        assert overview.variation_percent == Decimal("0.25")
        assert overview.twr_total == Decimal("0.25")  # 20 -> 25
        assert overview.twr_12m == Decimal("25") / Decimal("22") - 1  # 22 -> 25
        assert overview.excluded == []
        assert not overview.is_empty

    def test_empty_ledger_has_no_numbers(self, conn: psycopg.Connection[DictRow], tmp_path: Any) -> None:
        overview = compute_overview(conn, make_dispatcher(tmp_path), as_of=AS_OF)
        assert overview.is_empty
        assert overview.inception is None
        assert overview.patrimony is None
        assert overview.variation is None
        assert overview.variation_percent is None
        assert overview.twr_12m is None
        assert overview.twr_total is None

    def test_reference_older_than_the_first_transaction_has_no_close(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        # Primeira transacao em 2025-01-06: um D-1 anterior a isso nao tem o que avaliar.
        overview = compute_overview(conn, make_dispatcher(tmp_path), as_of=date(2024, 12, 31))
        assert not overview.is_empty
        assert overview.patrimony is None
        assert overview.twr_total is None

    def test_ticker_without_history_is_excluded_from_every_number(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        # TESOURO nao tem serie historica gratuita (issue #17): fica fora do
        # patrimonio E do capital investido, para a variacao seguir comparavel.
        AssetRepository(conn).add(
            "TESOURO-IPCA-2035",
            Decimal("0.3"),
            asset_type=AssetType.TESOURO,
            indexer=Indexer.IPCA_PLUS,
            rate=Decimal("0.065"),
            is_prefixed=False,
            purchase_date=datetime(2025, 2, 3, 12, tzinfo=UTC),
            maturity_date=datetime(2035, 5, 15, 12, tzinfo=UTC),
        )
        TransactionRepository(conn).add_buy(
            "TESOURO-IPCA-2035",
            shares=Decimal("1"),
            unit_price=Decimal("5000"),
            date=datetime(2025, 2, 3, 12, tzinfo=UTC),
        )
        dispatcher = make_dispatcher(tmp_path, yfinance=FakeYfinance(dict(HISTORY)))
        overview = compute_overview(conn, dispatcher, as_of=AS_OF)

        assert overview.excluded == ["TESOURO-IPCA-2035"]
        assert overview.invested == Decimal("200")  # so PETR4, nao os 5000 do titulo
        assert overview.patrimony == Decimal("250")

    def test_no_position_with_history_leaves_patrimony_null(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        # Fake sem historico para PETR4: nada avaliavel, mas nao e carteira vazia.
        overview = compute_overview(conn, make_dispatcher(tmp_path), as_of=AS_OF)
        assert overview.excluded == ["PETR4"]
        assert overview.patrimony is None
        assert overview.variation is None
        assert overview.twr_total is None
        assert not overview.is_empty


class TestVariationPercent:
    def test_negative_invested_capital_has_no_percentage(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        # Venda lucrativa deixa o investido negativo (ver Holding); porcentagem
        # sobre essa base nao significaria nada.
        TransactionRepository(conn).add_sale(
            "PETR4", shares=Decimal("9"), unit_price=Decimal("25"), date=datetime(2026, 7, 17, 12, tzinfo=UTC)
        )
        dispatcher = make_dispatcher(tmp_path, yfinance=FakeYfinance(dict(HISTORY)))
        overview = compute_overview(conn, dispatcher, as_of=AS_OF)
        assert overview.invested < 0
        assert overview.variation is not None
        assert overview.variation_percent is None
