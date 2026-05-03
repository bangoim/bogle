from __future__ import annotations

from decimal import Decimal

import psycopg
import pytest
from psycopg import errors as pg_errors

from bogle.models import (
    add_transaction,
    delete_transaction,
    get_holding,
    get_holdings,
    list_transactions,
)
from bogle.repositories.assets import AssetRepository


class TestAddTransaction:
    def test_returns_id(self, conn: psycopg.Connection, repo: AssetRepository) -> None:
        repo.add("PETR4", Decimal("0.1"))
        tid = add_transaction(conn, "PETR4", "2025-01-15", 100, 30.0)
        assert isinstance(tid, int)
        assert tid > 0

    def test_computed_fields_with_fees(self, conn: psycopg.Connection, repo: AssetRepository) -> None:
        repo.add("PETR4", Decimal("0.1"))
        add_transaction(conn, "PETR4", "2025-01-15", 100, 30.0, fees=5.5)
        rows = list_transactions(conn, "PETR4")
        row = rows[0]
        assert row["shares"] == Decimal("100")
        assert row["unit_price"] == Decimal("30.0000")
        assert row["total_investment"] == Decimal("3000.0000")
        assert row["fees"] == Decimal("5.5000")
        assert row["total_cost"] == Decimal("3005.5000")

    def test_computed_fields_without_fees(self, conn: psycopg.Connection, repo: AssetRepository) -> None:
        repo.add("PETR4", Decimal("0.1"))
        add_transaction(conn, "PETR4", "2025-01-15", 200, 25.0)
        row = list_transactions(conn, "PETR4")[0]
        assert row["total_investment"] == Decimal("5000.0000")
        assert row["fees"] == Decimal("0.0000")
        assert row["total_cost"] == Decimal("5000.0000")

    def test_ticker_uppercased(self, conn: psycopg.Connection, repo: AssetRepository) -> None:
        repo.add("VALE3", Decimal("0.1"))
        add_transaction(conn, "vale3", "2025-06-01", 50, 60.0)
        rows = list_transactions(conn, "VALE3")
        assert len(rows) == 1
        assert rows[0]["ticker"] == "VALE3"

    def test_fk_violation_when_asset_missing(self, conn: psycopg.Connection) -> None:
        with pytest.raises(pg_errors.ForeignKeyViolation):
            add_transaction(conn, "NOPE", "2025-01-15", 10, 10.0)


class TestListTransactions:
    def test_empty(self, conn: psycopg.Connection) -> None:
        assert list_transactions(conn) == []

    def test_all(self, conn: psycopg.Connection, repo: AssetRepository) -> None:
        repo.add("PETR4", Decimal("0.1"))
        repo.add("VALE3", Decimal("0.1"))
        add_transaction(conn, "PETR4", "2025-01-15", 100, 30.0)
        add_transaction(conn, "VALE3", "2025-01-16", 50, 60.0)
        assert len(list_transactions(conn)) == 2

    def test_filter_by_ticker(self, conn: psycopg.Connection, repo: AssetRepository) -> None:
        repo.add("PETR4", Decimal("0.1"))
        repo.add("VALE3", Decimal("0.1"))
        add_transaction(conn, "PETR4", "2025-01-15", 100, 30.0)
        add_transaction(conn, "VALE3", "2025-01-16", 50, 60.0)
        rows = list_transactions(conn, "PETR4")
        assert len(rows) == 1
        assert rows[0]["ticker"] == "PETR4"

    def test_ordered_by_purchase_date(self, conn: psycopg.Connection, repo: AssetRepository) -> None:
        repo.add("PETR4", Decimal("0.1"))
        add_transaction(conn, "PETR4", "2025-03-01", 10, 30.0)
        add_transaction(conn, "PETR4", "2025-01-01", 20, 28.0)
        rows = list_transactions(conn, "PETR4")
        # purchase_date é TIMESTAMPTZ; comparamos a ordem cronológica
        assert rows[0]["purchase_date"] < rows[1]["purchase_date"]


class TestDeleteTransaction:
    def test_existing_returns_true(self, conn: psycopg.Connection, repo: AssetRepository) -> None:
        repo.add("PETR4", Decimal("0.1"))
        tid = add_transaction(conn, "PETR4", "2025-01-15", 100, 30.0)
        assert delete_transaction(conn, tid) is True
        assert list_transactions(conn) == []

    def test_missing_returns_false(self, conn: psycopg.Connection) -> None:
        assert delete_transaction(conn, 9999) is False


class TestHoldings:
    def test_single_transaction(self, conn: psycopg.Connection, repo: AssetRepository) -> None:
        repo.add("PETR4", Decimal("0.15"))
        add_transaction(conn, "PETR4", "2025-01-15", 100, 30.0, fees=10.0)

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
        self, conn: psycopg.Connection, repo: AssetRepository
    ) -> None:
        repo.add("PETR4", Decimal("0.15"))
        add_transaction(conn, "PETR4", "2025-01-15", 100, 30.0, fees=10.0)
        add_transaction(conn, "PETR4", "2025-02-15", 50, 32.0, fees=5.0)

        h = get_holding(conn, "PETR4")
        assert h is not None
        assert h["total_shares"] == Decimal("150.00000000")
        assert h["total_cost"] == Decimal("4615.0000")  # 3010 + 1605
        assert h["avg_cost_per_share"] == pytest.approx(Decimal("4615") / Decimal("150"))

    def test_multiple_tickers(self, conn: psycopg.Connection, repo: AssetRepository) -> None:
        repo.add("PETR4", Decimal("0.15"))
        repo.add("VALE3", Decimal("0.10"))
        add_transaction(conn, "PETR4", "2025-01-15", 100, 30.0)
        add_transaction(conn, "VALE3", "2025-01-16", 50, 60.0)

        rows = get_holdings(conn)
        tickers = sorted(r["ticker"] for r in rows)
        assert tickers == ["PETR4", "VALE3"]

    def test_no_transactions_means_no_holding(
        self, conn: psycopg.Connection, repo: AssetRepository
    ) -> None:
        repo.add("PETR4", Decimal("0.15"))
        assert get_holding(conn, "PETR4") is None
        assert get_holdings(conn) == []

    def test_get_holding_ticker_uppercased(
        self, conn: psycopg.Connection, repo: AssetRepository
    ) -> None:
        repo.add("PETR4", Decimal("0.15"))
        add_transaction(conn, "PETR4", "2025-01-15", 100, 30.0)
        assert get_holding(conn, "petr4") is not None
