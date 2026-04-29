from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class AssetType(StrEnum):
    """Categories of assets supported by bogle.

    Variable-income instruments (STOCK, BDR, FII, ETF) carry only ticker
    and target weight. Tesouro Direto and private fixed-income
    instruments (CDB, RDB, LCI, LCA, CAIXINHA) carry additional metadata
    (issuer, indexer, rate, ...) checked at the database level.
    """

    STOCK = "STOCK"
    BDR = "BDR"
    FII = "FII"
    ETF = "ETF"
    TESOURO = "TESOURO"
    CDB = "CDB"
    RDB = "RDB"
    LCI = "LCI"
    LCA = "LCA"
    CAIXINHA = "CAIXINHA"


class Indexer(StrEnum):
    CDI = "CDI"
    CDI_PLUS = "CDI+"
    IPCA_PLUS = "IPCA+"
    SELIC = "SELIC"
    PREFIXADO = "PREFIXADO"


# Convenience groups used by validation logic.
VARIABLE_INCOME_TYPES = frozenset({
    AssetType.STOCK, AssetType.BDR, AssetType.FII, AssetType.ETF,
})
PRIVATE_FIXED_INCOME_TYPES = frozenset({
    AssetType.CDB, AssetType.RDB, AssetType.LCI, AssetType.LCA,
    AssetType.CAIXINHA,
})
FIXED_INCOME_TYPES = PRIVATE_FIXED_INCOME_TYPES | {AssetType.TESOURO}


@dataclass(frozen=True, slots=True)
class Asset:
    ticker: str
    target_weight: Decimal
    asset_type: AssetType = AssetType.STOCK
    issuer: str | None = None
    indexer: Indexer | None = None
    rate: Decimal | None = None
    is_prefixed: bool | None = None
    daily_liquidity: bool | None = None
    purchase_date: datetime | None = None
    maturity_date: datetime | None = None
