from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import psycopg
import pytest
from psycopg.rows import DictRow

from bogle.domain.assets import Asset, AssetType, Indexer
from bogle.domain.errors import (
    AssetAlreadyExistsError,
    AssetHasTransactionsError,
    AssetNotFoundError,
    ValidationError,
    WeightSumExceededError,
)
from bogle.repositories.assets import AssetRepository
from bogle.repositories.transactions import TransactionRepository


class TestAdd:
    def test_basic_insert(self, repo: AssetRepository) -> None:
        asset = repo.add("VTI", Decimal("0.5"))
        assert isinstance(asset, Asset)
        assert asset.ticker == "VTI"
        assert asset.target_weight == Decimal("0.5")
        assert asset.asset_type == AssetType.STOCK

        persisted = repo.get("VTI")
        assert persisted is not None
        assert persisted.ticker == "VTI"
        assert persisted.target_weight == Decimal("0.5")

    def test_ticker_uppercased(self, repo: AssetRepository) -> None:
        repo.add("vti", Decimal("0.1"))
        assert repo.get("VTI") is not None
        assert repo.get("vti") is not None  # get() also uppercases

    def test_with_full_fixed_income_metadata(self, repo: AssetRepository) -> None:
        asset = repo.add(
            "CDB123",
            Decimal("0.15"),
            asset_type=AssetType.CDB,
            issuer="Banco X",
            indexer=Indexer.CDI,
            rate=Decimal("110"),
            is_prefixed=False,
            daily_liquidity=False,
            maturity_date=datetime(2030, 1, 1, tzinfo=UTC),
        )
        assert asset.issuer == "Banco X"
        assert asset.indexer == Indexer.CDI
        assert asset.maturity_date is not None

    def test_duplicate_raises(self, repo: AssetRepository) -> None:
        repo.add("VTI", Decimal("0.1"))
        with pytest.raises(AssetAlreadyExistsError):
            repo.add("VTI", Decimal("0.1"))

    def test_weight_sum_exceeded_rolls_back(self, repo: AssetRepository) -> None:
        repo.add("A1", Decimal("0.6"))
        with pytest.raises(WeightSumExceededError) as exc:
            repo.add("A2", Decimal("0.5"))
        assert exc.value.total > Decimal("1")
        # A2 não foi inserido (rollback)
        assert repo.get("A2") is None
        # A1 continua intacto
        assert repo.get("A1") is not None

    def test_check_variable_income_with_issuer_raises(self, repo: AssetRepository) -> None:
        # STOCK não pode ter issuer (constraint assets_variable_income_clean)
        with pytest.raises(ValidationError):
            repo.add("PETR4", Decimal("0.1"), asset_type=AssetType.STOCK, issuer="Petrobras")

    def test_check_private_fixed_income_without_issuer_raises(self, repo: AssetRepository) -> None:
        # CDB sem issuer (assets_private_fixed_income_requires_issuer)
        with pytest.raises(ValidationError):
            repo.add(
                "CDB_X",
                Decimal("0.1"),
                asset_type=AssetType.CDB,
                indexer=Indexer.CDI,
                is_prefixed=False,
                daily_liquidity=True,
            )

    def test_check_prefixed_with_indexer_raises(self, repo: AssetRepository) -> None:
        # is_prefixed=True com indexer (assets_prefixed_no_indexer)
        with pytest.raises(ValidationError):
            repo.add(
                "TESOURO_PRE",
                Decimal("0.1"),
                asset_type=AssetType.TESOURO,
                is_prefixed=True,
                indexer=Indexer.PREFIXADO,
                daily_liquidity=True,
            )

    def test_check_no_liquidity_without_maturity_raises(self, repo: AssetRepository) -> None:
        # daily_liquidity=False sem maturity_date (assets_no_liquidity_requires_maturity)
        with pytest.raises(ValidationError):
            repo.add(
                "CDB_Y",
                Decimal("0.1"),
                asset_type=AssetType.CDB,
                issuer="Banco Y",
                indexer=Indexer.CDI,
                is_prefixed=False,
                daily_liquidity=False,
                maturity_date=None,
            )


class TestList:
    def test_empty(self, repo: AssetRepository) -> None:
        assert repo.list() == []

    def test_ordered_by_ticker(self, repo: AssetRepository) -> None:
        repo.add("VALE3", Decimal("0.1"))
        repo.add("ITUB4", Decimal("0.1"))
        repo.add("PETR4", Decimal("0.15"))
        tickers = [a.ticker for a in repo.list()]
        assert tickers == ["ITUB4", "PETR4", "VALE3"]


class TestGet:
    def test_existing(self, repo: AssetRepository) -> None:
        repo.add("VTI", Decimal("0.3"))
        asset = repo.get("VTI")
        assert asset is not None
        assert asset.ticker == "VTI"

    def test_missing(self, repo: AssetRepository) -> None:
        assert repo.get("XYZ") is None


class TestUpdateWeight:
    def test_success_returns_full_state(self, repo: AssetRepository) -> None:
        repo.add(
            "CDB1",
            Decimal("0.2"),
            asset_type=AssetType.CDB,
            issuer="Banco X",
            indexer=Indexer.CDI,
            is_prefixed=False,
            daily_liquidity=True,
        )
        updated = repo.update_weight("CDB1", Decimal("0.3"))
        assert updated.target_weight == Decimal("0.3")
        # State completo, não só o weight
        assert updated.issuer == "Banco X"
        assert updated.asset_type == AssetType.CDB

    def test_unknown_ticker_raises(self, repo: AssetRepository) -> None:
        with pytest.raises(AssetNotFoundError):
            repo.update_weight("XYZ", Decimal("0.1"))

    def test_weight_sum_exceeded_rolls_back(self, repo: AssetRepository) -> None:
        repo.add("A1", Decimal("0.4"))
        repo.add("A2", Decimal("0.5"))
        with pytest.raises(WeightSumExceededError):
            repo.update_weight("A1", Decimal("0.6"))  # 0.6 + 0.5 = 1.1
        # A1 manteve o peso original (rollback)
        a1 = repo.get("A1")
        assert a1 is not None
        assert a1.target_weight == Decimal("0.4")


class TestUpdateType:
    def test_success_returns_full_state(self, repo: AssetRepository) -> None:
        repo.add("VWRA11", Decimal("0.3"), asset_type=AssetType.STOCK)
        updated = repo.update_type("VWRA11", AssetType.ETF)
        assert updated.asset_type == AssetType.ETF
        assert updated.target_weight == Decimal("0.3")  # peso preservado
        persisted = repo.get("VWRA11")
        assert persisted is not None
        assert persisted.asset_type == AssetType.ETF  # persistido

    def test_case_insensitive_ticker(self, repo: AssetRepository) -> None:
        repo.add("VWRA11", Decimal("0.3"))
        updated = repo.update_type("vwra11", AssetType.FII)
        assert updated.ticker == "VWRA11"
        assert updated.asset_type == AssetType.FII

    def test_unknown_ticker_raises(self, repo: AssetRepository) -> None:
        with pytest.raises(AssetNotFoundError):
            repo.update_type("XYZ", AssetType.ETF)


class TestRemove:
    def test_success(self, repo: AssetRepository) -> None:
        repo.add("VTI", Decimal("0.1"))
        repo.remove("VTI")
        assert repo.get("VTI") is None

    def test_unknown_ticker_raises(self, repo: AssetRepository) -> None:
        with pytest.raises(AssetNotFoundError):
            repo.remove("XYZ")

    def test_with_transactions_raises(self, conn: psycopg.Connection[DictRow], repo: AssetRepository) -> None:
        repo.add("PETR4", Decimal("0.1"))
        TransactionRepository(conn).add_buy("PETR4", datetime(2025, 1, 15, tzinfo=UTC), Decimal("10"), Decimal("30"))
        with pytest.raises(AssetHasTransactionsError):
            repo.remove("PETR4")
