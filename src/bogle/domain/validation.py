"""Per-asset-type validation of asset metadata (issue 2.2).

Valid field combinations per ``asset_type``::

    | asset_type                   | issuer | indexer | rate | is_prefixed | daily_liquidity | purchase_date | maturity_date       |
    |------------------------------|--------|---------|------|-------------|-----------------|---------------|---------------------|
    | STOCK BDR FII ETF            | -      | -       | -    | -           | -               | -             | -                   |
    | TESOURO                      | -      | req*    | req  | req         | -               | req           | req                 |
    | CDB RDB LCI LCA CAIXINHA     | req    | req*    | req  | req         | req             | req           | if !daily_liquidity |

    -     field must NOT be provided (irrelevant for the type).
    req   field is required.
    *     indexer is null only when ``is_prefixed = true`` (a prefixed
          instrument has no indexer); required otherwise.

For fixed income, ``is_prefixed`` defaults to ``false`` (pos-fixado) when
omitted; prefixed instruments are declared with the ``--prefixed`` flag.
``maturity_date`` is optional for private fixed income with daily
liquidity (a CDB com liquidez diaria ainda pode ter vencimento).

The database CHECK constraints in migration 002 enforce a *subset* of
these rules as a last line of defense (e.g. they do not require rate or
purchase_date); this module is the source of truth and exists so the CLI
reports every problem at once, with friendly messages, before touching
the database.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from bogle.domain.assets import (
    PRIVATE_FIXED_INCOME_TYPES,
    VARIABLE_INCOME_TYPES,
    AssetType,
    Indexer,
)
from bogle.domain.errors import ValidationError


@dataclass(frozen=True, slots=True)
class AssetMetadata:
    """Normalized per-type metadata, ready for ``AssetRepository.add``."""

    issuer: str | None = None
    indexer: Indexer | None = None
    rate: Decimal | None = None
    is_prefixed: bool | None = None
    daily_liquidity: bool | None = None
    purchase_date: datetime | None = None
    maturity_date: datetime | None = None


def validate_asset_metadata(
    asset_type: AssetType,
    *,
    issuer: str | None = None,
    indexer: Indexer | None = None,
    rate: Decimal | None = None,
    is_prefixed: bool | None = None,
    daily_liquidity: bool | None = None,
    purchase_date: datetime | None = None,
    maturity_date: datetime | None = None,
    extra_errors: Sequence[str] = (),
) -> AssetMetadata:
    """Validate the field combination for ``asset_type``.

    ``None`` means "not provided by the user". Collects *every* problem
    (missing and irrelevant fields alike) and raises a single
    ``ValidationError`` listing them all; on success returns the
    normalized metadata (e.g. ``is_prefixed`` defaulted for fixed income).

    ``extra_errors`` lets the caller fold upstream problems (e.g. CLI
    parse failures) into the same aggregated report.
    """
    errors: list[str] = list(extra_errors)

    if asset_type in VARIABLE_INCOME_TYPES:
        provided = {
            "--issuer": issuer,
            "--indexer": indexer,
            "--rate": rate,
            "--prefixed/--no-prefixed": is_prefixed,
            "--daily-liquidity/--no-daily-liquidity": daily_liquidity,
            "--purchase-date": purchase_date,
            "--maturity-date": maturity_date,
        }
        errors.extend(
            f"{option} nao se aplica ao tipo {asset_type}." for option, value in provided.items() if value is not None
        )
        _raise_if_any(asset_type, errors)
        return AssetMetadata()

    # Fixed income (TESOURO + CDB/RDB/LCI/LCA/CAIXINHA).
    prefixed = is_prefixed if is_prefixed is not None else False
    if indexer is Indexer.PREFIXADO:
        errors.append("para titulos prefixados use --prefixed em vez de --indexer PREFIXADO.")
    elif prefixed and indexer is not None:
        errors.append("--indexer nao deve ser informado junto com --prefixed (titulo prefixado nao tem indexador).")
    elif not prefixed and indexer is None:
        errors.append(f"--indexer e obrigatorio para {asset_type} pos-fixado (ou use --prefixed).")

    if rate is None:
        errors.append(f"--rate e obrigatorio para {asset_type}.")
    if purchase_date is None:
        errors.append(f"--purchase-date e obrigatorio para {asset_type}.")

    if asset_type in PRIVATE_FIXED_INCOME_TYPES:
        if issuer is None:
            errors.append(f"--issuer e obrigatorio para {asset_type}.")
        if daily_liquidity is None:
            errors.append(f"--daily-liquidity/--no-daily-liquidity e obrigatorio para {asset_type}.")
        elif daily_liquidity is False and maturity_date is None:
            errors.append(f"--maturity-date e obrigatorio para {asset_type} sem liquidez diaria.")
    else:  # TESOURO
        if issuer is not None:
            errors.append("--issuer nao se aplica ao tipo TESOURO.")
        if daily_liquidity is not None:
            errors.append("--daily-liquidity/--no-daily-liquidity nao se aplica ao tipo TESOURO.")
        if maturity_date is None:
            errors.append("--maturity-date e obrigatorio para TESOURO.")

    _raise_if_any(asset_type, errors)
    return AssetMetadata(
        issuer=issuer,
        indexer=None if prefixed else indexer,
        rate=rate,
        is_prefixed=prefixed,
        daily_liquidity=daily_liquidity,
        purchase_date=purchase_date,
        maturity_date=maturity_date,
    )


def _raise_if_any(asset_type: AssetType, errors: list[str]) -> None:
    if errors:
        listing = "\n".join(f"  - {e}" for e in errors)
        raise ValidationError(f"parametros invalidos para o tipo {asset_type}:\n{listing}")


def validate_type_change(ticker: str, current: AssetType, new: AssetType) -> None:
    """Guard a ``bogle update --type`` change (variable-income only).

    Changing an asset's type only touches ``asset_type`` when both the
    current and new types are variable income (STOCK/BDR/FII/ETF), which
    carry no metadata. Any change that crosses the fixed-income boundary
    would require *supplying* metadata (issuer/indexer/rate/dates) or
    *clearing* it — neither of which ``update`` does — so it is rejected
    here in favor of ``bogle remove`` + ``bogle add``.
    """
    if new not in VARIABLE_INCOME_TYPES:
        raise ValidationError(
            f"nao da para trocar {ticker} para o tipo {new} (renda fixa) via update: "
            f"esse tipo exige metadados (issuer/indexer/rate/datas). "
            f"Use 'bogle remove {ticker}' e recadastre com 'bogle add'."
        )
    if current not in VARIABLE_INCOME_TYPES:
        raise ValidationError(
            f"{ticker} e do tipo {current} (renda fixa); trocar de tipo via update deixaria "
            f"metadados orfaos. Use 'bogle remove {ticker}' e recadastre com 'bogle add'."
        )
