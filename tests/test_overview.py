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
from bogle.domain.transactions import Transaction, TransactionType
from bogle.reports.overview import compute_overview, invested_at
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

    def test_a_buy_dated_after_the_reference_is_not_in_the_invested_base(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        # O caso mais comum: registrar um aporte hoje e voltar para a Home. Se o
        # capital investido viesse da view holdings (que soma o ledger inteiro),
        # o dinheiro entraria na base sem as cotas entrarem no patrimonio D-1 e a
        # Home mostraria uma perda do tamanho do aporte.
        TransactionRepository(conn).add_buy(
            "PETR4", shares=Decimal("10"), unit_price=Decimal("25"), date=datetime(2026, 7, 21, 12, tzinfo=UTC)
        )
        dispatcher = make_dispatcher(tmp_path, yfinance=FakeYfinance(dict(HISTORY)))
        overview = compute_overview(conn, dispatcher, as_of=AS_OF)  # 2026-07-20, antes da compra
        assert overview.invested == Decimal("200")  # so a compra de 2025
        assert overview.patrimony == Decimal("250")
        assert overview.variation == Decimal("50")

    def test_a_sale_dated_after_the_reference_does_not_shrink_the_base(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        # Espelho do caso acima: a venda ainda nao aconteceu na data de
        # referencia, entao nem o caixa dela sai do investido nem as cotas saem
        # do patrimonio.
        TransactionRepository(conn).add_sale(
            "PETR4", shares=Decimal("4"), unit_price=Decimal("25"), date=datetime(2026, 7, 21, 12, tzinfo=UTC)
        )
        dispatcher = make_dispatcher(tmp_path, yfinance=FakeYfinance(dict(HISTORY)))
        overview = compute_overview(conn, dispatcher, as_of=AS_OF)
        assert overview.invested == Decimal("200")  # nao desconta os 100 da venda
        assert overview.patrimony == Decimal("250")  # ainda as 10 cotas

    def test_a_position_closed_today_leaves_the_reference_close_unavailable(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        # Limite herdado de build_portfolio_valuation, que parte das posicoes
        # ativas *agora*: zerar o ticker hoje tira ele da avaliacao, inclusive
        # para datas em que ainda era mantido. Mesma politica dos relatorios
        # historicos da CLI (history/compare/return), e a Home avisa em vez de
        # mostrar um numero errado.
        TransactionRepository(conn).add_sale(
            "PETR4", shares=Decimal("10"), unit_price=Decimal("25"), date=datetime(2026, 7, 21, 12, tzinfo=UTC)
        )
        dispatcher = make_dispatcher(tmp_path, yfinance=FakeYfinance(dict(HISTORY)))
        overview = compute_overview(conn, dispatcher, as_of=AS_OF)
        assert overview.patrimony is None
        assert overview.variation is None
        assert not overview.is_empty

    def test_twelve_month_window_anchors_on_inception_and_says_so(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        dispatcher = make_dispatcher(tmp_path, yfinance=FakeYfinance(dict(HISTORY)))
        # Referencia menos de 12 meses depois da primeira transacao (2025-01-06).
        overview = compute_overview(conn, dispatcher, as_of=date(2025, 7, 18))
        assert overview.twr_12m_start == date(2025, 1, 6)
        assert overview.twr_12m_is_shorter
        assert overview.twr_12m == overview.twr_total

    def test_full_twelve_month_window_is_not_flagged(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        dispatcher = make_dispatcher(tmp_path, yfinance=FakeYfinance(dict(HISTORY)))
        overview = compute_overview(conn, dispatcher, as_of=AS_OF)
        assert overview.twr_12m_start == date(2025, 7, 20)
        assert not overview.twr_12m_is_shorter

    def test_excluded_ticker_makes_the_reading_partial(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        overview = compute_overview(conn, make_dispatcher(tmp_path), as_of=AS_OF)
        assert overview.is_partial  # PETR4 sem historico no fake

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


def txn(
    kind: TransactionType,
    on: str,
    *,
    ticker: str = "PETR4",
    shares: str = "0",
    price: str = "0",
    fees: str = "0",
    amount: str | None = None,
) -> Transaction:
    """A ledger row, with the same field semantics the repository writes."""
    gross = Decimal(amount) if amount is not None else Decimal(shares) * Decimal(price)
    return Transaction(
        id=1,
        ticker=ticker,
        transaction_type=kind,
        date=datetime.fromisoformat(on).replace(tzinfo=UTC),
        shares=Decimal(shares),
        unit_price=Decimal(price),
        total_investment=gross,
        fees=Decimal(fees),
        total_cost=gross + Decimal(fees) if kind is TransactionType.BUY else Decimal(fees),
        tax_withheld=Decimal("0"),
    )


class TestInvestedAt:
    """The as-of mirror of the ``holdings`` view."""

    def test_counts_buys_with_their_fees(self) -> None:
        buy = txn(TransactionType.BUY, "2026-01-05", shares="10", price="20", fees="5")
        assert invested_at([buy], date(2026, 3, 1)) == Decimal("205")

    def test_ignores_transactions_after_the_date(self) -> None:
        buy = txn(TransactionType.BUY, "2026-05-05", shares="10", price="20")
        assert invested_at([buy], date(2026, 3, 1)) == Decimal("0")

    def test_sale_returns_its_gross_proceeds_to_the_base(self) -> None:
        txns = [
            txn(TransactionType.BUY, "2026-01-05", shares="10", price="20"),
            txn(TransactionType.SELL, "2026-02-05", shares="4", price="25"),
        ]
        assert invested_at(txns, date(2026, 3, 1)) == Decimal("100")  # 200 - 100

    def test_a_closed_position_leaves_the_base_entirely(self) -> None:
        # Igual a view holdings: posicao zerada nao aparece, entao o lucro
        # realizado nao vira "capital investido negativo".
        txns = [
            txn(TransactionType.BUY, "2026-01-05", shares="10", price="20"),
            txn(TransactionType.SELL, "2026-02-05", shares="10", price="30"),
        ]
        assert invested_at(txns, date(2026, 3, 1)) == Decimal("0")

    def test_income_is_neutral(self) -> None:
        txns = [
            txn(TransactionType.BUY, "2026-01-05", shares="10", price="20"),
            txn(TransactionType.DIVIDEND, "2026-02-05", amount="50"),
        ]
        assert invested_at(txns, date(2026, 3, 1)) == Decimal("200")

    def test_only_the_tickers_still_held_count(self) -> None:
        txns = [
            txn(TransactionType.BUY, "2026-01-05", ticker="PETR4", shares="10", price="20"),
            txn(TransactionType.BUY, "2026-01-05", ticker="MXRF11", shares="100", price="9"),
            txn(TransactionType.SELL, "2026-02-05", ticker="MXRF11", shares="100", price="10"),
        ]
        assert invested_at(txns, date(2026, 3, 1)) == Decimal("200")  # so PETR4
