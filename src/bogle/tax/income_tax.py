"""Per-operation income tax (IR) for the asset types bogle supports (issue 4.3).

Pure functions; the rules are documented in ``docs/tax_rules.md`` (issue 4.1) and
reflect the 2026 legislation. Cost basis comes ready from
``bogle.domain.cost_basis`` (issue 4.5) — this module does not recompute it.

Out of scope (Epico 11 / informative): monthly consolidated apuration, loss
carry-forward, DARF, the R$50k/month-per-payer dividend threshold and the IRPFM
(both annual aggregates), and day trade. These functions compute one operation at
a time and never aggregate across the month — the caller supplies any monthly
total it needs (e.g. ``monthly_stock_sales_total``).

All values are ``Decimal`` and are NOT rounded to centavos; rounding belongs to
the DARF layer (Epico 11).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from bogle.domain.assets import (
    FIXED_INCOME_TYPES,
    Asset,
    AssetType,
)
from bogle.domain.errors import ValidationError
from bogle.domain.transactions import Transaction

_ZERO = Decimal("0")

# Capital-gain rate on the sale of variable-income assets.
_SALE_RATES: dict[AssetType, Decimal] = {
    AssetType.STOCK: Decimal("0.15"),
    AssetType.BDR: Decimal("0.15"),
    AssetType.ETF: Decimal("0.15"),
    AssetType.FII: Decimal("0.20"),
}

# Monthly stock-sale value at or below which the gain is IR-exempt (stocks only).
_STOCK_MONTHLY_EXEMPTION = Decimal("20000")

# IR retained at source on JCP, fixed at 15%.
_JCP_RATE = Decimal("0.15")

# Fixed-income types whose yield is IR-exempt for individuals.
_FIXED_INCOME_EXEMPT = frozenset({AssetType.LCI, AssetType.LCA})


def income_tax_on_sale(
    asset: Asset,
    sale: Transaction,
    cost_basis: Decimal,
    monthly_stock_sales_total: Decimal,
) -> Decimal:
    """IR on the capital gain of a variable-income sale.

    ``gain = sale proceeds (total_investment) - sale fees - cost_basis``. A loss
    (or zero gain) owes nothing. Rates: 15% for STOCK/BDR/ETF, 20% for FII.

    Stocks are exempt when ``monthly_stock_sales_total`` (the gross sold across
    the whole month, aggregated by the caller) is <= R$20.000; above it the full
    gain is taxed (the exemption is all-or-nothing, not just on the excess). The
    R$20k exemption applies to STOCK only — never to BDR, ETF or FII.

    Raises ``ValidationError`` for fixed income (use ``income_tax_on_fixed_income``).
    """
    rate = _SALE_RATES.get(asset.asset_type)
    if rate is None:
        raise ValidationError(
            f"income_tax_on_sale aplica-se a renda variavel (STOCK, BDR, ETF, FII); "
            f"tipo {asset.asset_type} nao suportado. Para renda fixa use income_tax_on_fixed_income."
        )
    gain = sale.total_investment - sale.fees - cost_basis
    if gain <= _ZERO:
        return _ZERO
    if asset.asset_type is AssetType.STOCK and monthly_stock_sales_total <= _STOCK_MONTHLY_EXEMPTION:
        return _ZERO
    return gain * rate


def income_tax_on_dividend(asset: Asset, amount: Decimal) -> Decimal:
    """IR on a stock dividend, per operation.

    Within bogle's scope this is always zero: until 01/01/2026 dividends were
    exempt, and from 2026 (Lei 15.270/2025) the 10% IRRF only applies above
    R$50.000/month from the same payer — a monthly aggregate that belongs to the
    annual apuration (Epico 11). The tax actually retained at source is recorded
    on the transaction's ``tax_withheld``; this function does not re-derive it.
    """
    return _ZERO


def income_tax_on_jcp(asset: Asset, amount: Decimal) -> Decimal:
    """IR on JCP (juros sobre capital proprio): 15% retained at source, definitive
    and unchanged by the 2026 reform."""
    return amount * _JCP_RATE


def income_tax_on_fii_rendimento(asset: Asset, amount: Decimal) -> Decimal:
    """IR on an FII monthly distribution: exempt for individuals meeting the legal
    requirements (listed fund, >50 cotistas, holder below 10%). Always zero."""
    return _ZERO


def income_tax_on_fixed_income(
    asset: Asset,
    purchase_date: datetime,
    redemption_date: datetime,
    income: Decimal,
) -> Decimal:
    """IR on fixed-income yield via the regressive table on the income.

    LCI/LCA are exempt (returns 0). For TESOURO/CDB/RDB/CAIXINHA the rate falls
    with the elapsed calendar days between purchase and redemption: 22,5% up to
    180d, 20% to 360d, 17,5% to 720d, 15% above 720d. A non-positive ``income``
    owes nothing.

    ``purchase_date`` is the BUY's ``transaction_date``. Raises ``ValidationError``
    for variable income.
    """
    if asset.asset_type not in FIXED_INCOME_TYPES:
        raise ValidationError(
            f"income_tax_on_fixed_income aplica-se a renda fixa "
            f"(TESOURO, CDB, RDB, LCI, LCA, CAIXINHA); tipo {asset.asset_type} nao suportado."
        )
    if asset.asset_type in _FIXED_INCOME_EXEMPT:
        return _ZERO
    if income <= _ZERO:
        return _ZERO
    days = (redemption_date.date() - purchase_date.date()).days
    return income * _regressive_rate(days)


def _regressive_rate(days: int) -> Decimal:
    """Regressive IR rate by elapsed days (Lei 11.033/2004, art. 1)."""
    if days <= 180:
        return Decimal("0.225")
    if days <= 360:
        return Decimal("0.20")
    if days <= 720:
        return Decimal("0.175")
    return Decimal("0.15")
