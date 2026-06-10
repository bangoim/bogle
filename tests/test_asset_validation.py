from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from bogle.domain.assets import AssetType, Indexer
from bogle.domain.errors import ValidationError
from bogle.domain.validation import validate_asset_metadata

PURCHASE = datetime(2026, 4, 1, tzinfo=UTC)
MATURITY = datetime(2027, 4, 1, tzinfo=UTC)


class TestVariableIncome:
    @pytest.mark.parametrize("asset_type", [AssetType.STOCK, AssetType.BDR, AssetType.FII, AssetType.ETF])
    def test_bare_is_valid(self, asset_type: AssetType) -> None:
        metadata = validate_asset_metadata(asset_type)
        assert metadata.issuer is None
        assert metadata.indexer is None
        assert metadata.is_prefixed is None

    def test_single_irrelevant_field_raises(self) -> None:
        with pytest.raises(ValidationError, match="--issuer nao se aplica ao tipo STOCK"):
            validate_asset_metadata(AssetType.STOCK, issuer="Petrobras")

    def test_all_irrelevant_fields_listed_at_once(self) -> None:
        with pytest.raises(ValidationError) as exc:
            validate_asset_metadata(
                AssetType.ETF,
                issuer="X",
                indexer=Indexer.CDI,
                rate=Decimal("1.1"),
                is_prefixed=False,
                daily_liquidity=True,
                purchase_date=PURCHASE,
                maturity_date=MATURITY,
            )
        message = str(exc.value)
        for option in (
            "--issuer",
            "--indexer",
            "--rate",
            "--prefixed/--no-prefixed",
            "--daily-liquidity/--no-daily-liquidity",
            "--purchase-date",
            "--maturity-date",
        ):
            assert option in message


class TestTesouro:
    def test_postfixed_happy_path(self) -> None:
        metadata = validate_asset_metadata(
            AssetType.TESOURO,
            indexer=Indexer.IPCA_PLUS,
            rate=Decimal("0.065"),
            purchase_date=PURCHASE,
            maturity_date=MATURITY,
        )
        # is_prefixed normalizado para False (pos-fixado e o default).
        assert metadata.is_prefixed is False
        assert metadata.indexer is Indexer.IPCA_PLUS

    def test_prefixed_happy_path(self) -> None:
        metadata = validate_asset_metadata(
            AssetType.TESOURO,
            is_prefixed=True,
            rate=Decimal("0.12"),
            purchase_date=PURCHASE,
            maturity_date=MATURITY,
        )
        assert metadata.is_prefixed is True
        assert metadata.indexer is None

    def test_all_missing_fields_listed_at_once(self) -> None:
        with pytest.raises(ValidationError) as exc:
            validate_asset_metadata(AssetType.TESOURO)
        message = str(exc.value)
        for option in ("--indexer", "--rate", "--purchase-date", "--maturity-date"):
            assert option in message

    def test_issuer_is_irrelevant(self) -> None:
        with pytest.raises(ValidationError, match="--issuer nao se aplica ao tipo TESOURO"):
            validate_asset_metadata(
                AssetType.TESOURO,
                issuer="Tesouro Nacional",
                indexer=Indexer.SELIC,
                rate=Decimal("0.001"),
                purchase_date=PURCHASE,
                maturity_date=MATURITY,
            )

    @pytest.mark.parametrize("daily_liquidity", [True, False])
    def test_daily_liquidity_is_irrelevant(self, daily_liquidity: bool) -> None:
        with pytest.raises(ValidationError, match="nao se aplica ao tipo TESOURO"):
            validate_asset_metadata(
                AssetType.TESOURO,
                indexer=Indexer.SELIC,
                rate=Decimal("0.001"),
                daily_liquidity=daily_liquidity,
                purchase_date=PURCHASE,
                maturity_date=MATURITY,
            )

    def test_prefixed_with_indexer_raises(self) -> None:
        with pytest.raises(ValidationError, match="--indexer nao deve ser informado junto com --prefixed"):
            validate_asset_metadata(
                AssetType.TESOURO,
                is_prefixed=True,
                indexer=Indexer.IPCA_PLUS,
                rate=Decimal("0.12"),
                purchase_date=PURCHASE,
                maturity_date=MATURITY,
            )

    def test_indexer_prefixado_suggests_flag(self) -> None:
        with pytest.raises(ValidationError, match="use --prefixed em vez de --indexer PREFIXADO"):
            validate_asset_metadata(
                AssetType.TESOURO,
                indexer=Indexer.PREFIXADO,
                rate=Decimal("0.12"),
                purchase_date=PURCHASE,
                maturity_date=MATURITY,
            )


class TestPrivateFixedIncome:
    def test_no_daily_liquidity_happy_path(self) -> None:
        metadata = validate_asset_metadata(
            AssetType.CDB,
            issuer="XP Investimentos",
            indexer=Indexer.CDI,
            rate=Decimal("1.10"),
            daily_liquidity=False,
            purchase_date=PURCHASE,
            maturity_date=MATURITY,
        )
        assert metadata.is_prefixed is False
        assert metadata.daily_liquidity is False

    def test_daily_liquidity_does_not_require_maturity(self) -> None:
        metadata = validate_asset_metadata(
            AssetType.CAIXINHA,
            issuer="Nubank",
            indexer=Indexer.CDI,
            rate=Decimal("1.0"),
            daily_liquidity=True,
            purchase_date=PURCHASE,
        )
        assert metadata.maturity_date is None

    def test_daily_liquidity_with_maturity_is_allowed(self) -> None:
        metadata = validate_asset_metadata(
            AssetType.LCI,
            issuer="Banco Inter",
            indexer=Indexer.CDI,
            rate=Decimal("0.92"),
            daily_liquidity=True,
            purchase_date=PURCHASE,
            maturity_date=MATURITY,
        )
        assert metadata.maturity_date == MATURITY

    def test_all_missing_fields_listed_at_once(self) -> None:
        with pytest.raises(ValidationError) as exc:
            validate_asset_metadata(AssetType.CDB)
        message = str(exc.value)
        for option in ("--issuer", "--indexer", "--rate", "--daily-liquidity", "--purchase-date"):
            assert option in message

    def test_no_daily_liquidity_without_maturity_raises(self) -> None:
        with pytest.raises(ValidationError, match="--maturity-date e obrigatorio para RDB sem liquidez diaria"):
            validate_asset_metadata(
                AssetType.RDB,
                issuer="Banco Z",
                indexer=Indexer.CDI,
                rate=Decimal("1.05"),
                daily_liquidity=False,
                purchase_date=PURCHASE,
            )

    def test_prefixed_happy_path(self) -> None:
        metadata = validate_asset_metadata(
            AssetType.LCA,
            issuer="Banco do Brasil",
            is_prefixed=True,
            rate=Decimal("0.105"),
            daily_liquidity=False,
            purchase_date=PURCHASE,
            maturity_date=MATURITY,
        )
        assert metadata.is_prefixed is True
        assert metadata.indexer is None

    def test_explicit_no_prefixed_requires_indexer(self) -> None:
        with pytest.raises(ValidationError, match="--indexer e obrigatorio para CDB pos-fixado"):
            validate_asset_metadata(
                AssetType.CDB,
                issuer="Banco W",
                is_prefixed=False,
                rate=Decimal("1.0"),
                daily_liquidity=True,
                purchase_date=PURCHASE,
            )


class TestExtraErrors:
    def test_extra_errors_are_folded_into_the_report(self) -> None:
        with pytest.raises(ValidationError) as exc:
            validate_asset_metadata(
                AssetType.CDB,
                indexer=Indexer.CDI,
                rate=Decimal("1.0"),
                daily_liquidity=True,
                purchase_date=PURCHASE,
                extra_errors=["--rate deve ser um numero decimal, recebido 'abc'."],
            )
        message = str(exc.value)
        assert "--rate deve ser um numero decimal" in message
        assert "--issuer e obrigatorio para CDB" in message

    def test_extra_errors_alone_still_raise(self) -> None:
        with pytest.raises(ValidationError, match="boom"):
            validate_asset_metadata(AssetType.STOCK, extra_errors=["boom"])
