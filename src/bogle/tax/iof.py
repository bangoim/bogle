"""Regressive IOF on fixed-income redemptions (issue 4.4).

Pure function; the rule is documented in ``docs/tax_rules.md`` (issue 4.2). IOF
falls on the *income* (never the principal) and only when the redemption happens
within the first 30 calendar days of the purchase, decreasing day by day to zero
on day 30. Variable income (STOCK, BDR, FII, ETF) is always exempt.

Decimal throughout — the table is built from strings, never from float (``Decimal(0.96)``
would carry binary imprecision).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from bogle.domain.assets import VARIABLE_INCOME_TYPES, AssetType

_ZERO = Decimal("0")

# IOF rate by elapsed calendar day (Decreto 6.306/2007, Anexo). Day 30 is 0%;
# day 31 onward is out of the table (also 0%).
IOF_RATES: dict[int, Decimal] = {
    1: Decimal("0.96"), 2: Decimal("0.93"), 3: Decimal("0.90"), 4: Decimal("0.86"),
    5: Decimal("0.83"), 6: Decimal("0.80"), 7: Decimal("0.76"), 8: Decimal("0.73"),
    9: Decimal("0.70"), 10: Decimal("0.66"), 11: Decimal("0.63"), 12: Decimal("0.60"),
    13: Decimal("0.56"), 14: Decimal("0.53"), 15: Decimal("0.50"), 16: Decimal("0.46"),
    17: Decimal("0.43"), 18: Decimal("0.40"), 19: Decimal("0.36"), 20: Decimal("0.33"),
    21: Decimal("0.30"), 22: Decimal("0.26"), 23: Decimal("0.23"), 24: Decimal("0.20"),
    25: Decimal("0.16"), 26: Decimal("0.13"), 27: Decimal("0.10"), 28: Decimal("0.06"),
    29: Decimal("0.03"), 30: Decimal("0.00"),
}  # fmt: skip


def iof_on_redemption(
    purchase_date: datetime,
    redemption_date: datetime,
    income: Decimal,
    asset_type: AssetType,
) -> Decimal:
    """Regressive IOF on the ``income`` of a redemption.

    Zero for variable income. For fixed income, zero unless the redemption falls
    within days 1..29 of the purchase (day 30 onward is already 0%), and zero on
    the same day (``days <= 0``) since no yield has accrued yet.

    ``days = (redemption_date - purchase_date)`` counted in calendar days;
    ``purchase_date`` is the BUY's ``transaction_date`` (not ``Asset.purchase_date``).
    """
    if asset_type in VARIABLE_INCOME_TYPES:
        return _ZERO
    days = (redemption_date.date() - purchase_date.date()).days
    if income <= _ZERO:
        return _ZERO
    return income * IOF_RATES.get(days, _ZERO)
