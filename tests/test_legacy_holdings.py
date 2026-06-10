from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import psycopg
import pytest

from bogle.models import get_holding, get_holdings
from bogle.repositories.assets import AssetRepository
from bogle.repositories.transactions import TransactionRepository

D1 = datetime(2025, 1, 15, tzinfo=UTC)
D2 = datetime(2025, 2, 15, tzinfo=UTC)


class TestHoldings:
    def test_single_transaction(
        self, conn: psycopg.Connection, repo: AssetRepository, trepo: TransactionRepository
    ) -> None:
        repo.add("PETR4", Decimal("0.15"))
        trepo.add_buy("PETR4", D1, Decimal("100"), Decimal("30"), fees=Decimal("10"))

        rows = get_holdings(conn)
        assert len(rows) == 1
        h = rows[0]
        assert h["ticker"] == "PETR4"
        assert h["target_weight"] == Decimal("0.1500")
        assert h["total_shares"] == Decimal("100.00000000")
        assert h["total_cost"] == Decimal("3010.0000")
        # avg_cost_per_share = 3010 / 100 = 30.10
        assert h["avg_cost_per_share"] == pytest.approx(Decimal("30.10"))

    def test_multiple_transactions_aggregated(
        self, conn: psycopg.Connection, repo: AssetRepository, trepo: TransactionRepository
    ) -> None:
        repo.add("PETR4", Decimal("0.15"))
        trepo.add_buy("PETR4", D1, Decimal("100"), Decimal("30"), fees=Decimal("10"))
        trepo.add_buy("PETR4", D2, Decimal("50"), Decimal("32"), fees=Decimal("5"))

        h = get_holding(conn, "PETR4")
        assert h is not None
        assert h["total_shares"] == Decimal("150.00000000")
        assert h["total_cost"] == Decimal("4615.0000")  # 3010 + 1605
        assert h["avg_cost_per_share"] == pytest.approx(Decimal("4615") / Decimal("150"))

    def test_multiple_tickers(
        self, conn: psycopg.Connection, repo: AssetRepository, trepo: TransactionRepository
    ) -> None:
        repo.add("PETR4", Decimal("0.15"))
        repo.add("VALE3", Decimal("0.10"))
        trepo.add_buy("PETR4", D1, Decimal("100"), Decimal("30"))
        trepo.add_buy("VALE3", D1, Decimal("50"), Decimal("60"))

        rows = get_holdings(conn)
        tickers = sorted(r["ticker"] for r in rows)
        assert tickers == ["PETR4", "VALE3"]

    def test_no_transactions_means_no_holding(self, conn: psycopg.Connection, repo: AssetRepository) -> None:
        repo.add("PETR4", Decimal("0.15"))
        assert get_holding(conn, "PETR4") is None
        assert get_holdings(conn) == []

    def test_get_holding_ticker_uppercased(
        self, conn: psycopg.Connection, repo: AssetRepository, trepo: TransactionRepository
    ) -> None:
        repo.add("PETR4", Decimal("0.15"))
        trepo.add_buy("PETR4", D1, Decimal("100"), Decimal("30"))
        assert get_holding(conn, "petr4") is not None


class TestHoldingsWithTransactionTypes:
    """Canarios para a interacao da view com os tipos da migracao 003.

    A semantica completa (BUY - SELL, filtro de posicao ativa) chega na
    issue 3.2; aqui so garantimos que a view nao quebra com os tipos novos.
    """

    def test_income_only_ticker_does_not_crash_the_view(
        self, conn: psycopg.Connection, repo: AssetRepository, trepo: TransactionRepository
    ) -> None:
        # Provento registrado antes de qualquer compra: SUM(shares) = 0;
        # sem o NULLIF da 003 a view inteira morria com DivisionByZero.
        repo.add("MXRF11", Decimal("0.05"))
        trepo.add_rendimento("MXRF11", D1, Decimal("80"))

        h = get_holding(conn, "MXRF11")
        assert h is not None
        assert h["total_shares"] == Decimal("0")
        assert h["avg_cost_per_share"] is None

    def test_income_only_ticker_does_not_break_portfolio_listing(
        self, conn: psycopg.Connection, repo: AssetRepository, trepo: TransactionRepository
    ) -> None:
        repo.add("PETR4", Decimal("0.15"))
        repo.add("MXRF11", Decimal("0.05"))
        trepo.add_buy("PETR4", D1, Decimal("100"), Decimal("30"))
        trepo.add_dividend("MXRF11", D2, Decimal("10"))

        rows = get_holdings(conn)
        assert sorted(r["ticker"] for r in rows) == ["MXRF11", "PETR4"]
