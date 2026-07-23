"""Present value of private fixed income (issue #18, split out per the plan).

Marks a fixed-income principal to its gross corrected value (decision C) as of a
date, capitalizing from the purchase date by the asset's indexer + rate. Pure
``Decimal`` math; the caller injects the BCB series (CDI/SELIC/IPCA) — same purity
as the TWR engine, so it is tested against hand-calculated values.

Rate convention follows the CLI (``--rate`` "em decimal"):

- CDI / SELIC (pos-fixado): ``rate`` is the multiplier of the index
  (``1.10`` = 110% do CDI).
- CDI+ : ``rate`` is the annual spread as a fraction (``0.02`` = CDI + 2% a.a.).
- IPCA+: ``rate`` is the annual real rate as a fraction (``0.06`` = IPCA + 6% a.a.).
- PREFIXADO: ``rate`` is the annual rate as a fraction (``0.12`` = 12% a.a.).

Conventions:

- Pos-fixado (CDI/SELIC/CDI+) compound the *actual* BCB daily series, whose dates
  are already business days, over ``[purchase_date, on_date)`` — so no holiday
  calendar is needed there. PREFIXADO and the real-rate leg of IPCA+ use the 252
  business-day convention via :mod:`bogle.analytics.business_days`.
- IPCA correction is accumulated from the published monthly IPCA, pro-rated per
  month by business days (``du_in_window / du_in_month``); a month with no
  published value yet uses the latest available as a projection. This is a
  documented approximation — the exact ANBIMA VNA anchors on the 15th with a
  projected IPCA that is not freely available.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from bogle.analytics.business_days import business_days_between
from bogle.data.models import SeriesPoint
from bogle.domain.assets import Indexer

_ONE = Decimal("1")
_BUSINESS_DAYS_YEAR = Decimal(252)


def present_value(
    principal: Decimal,
    *,
    indexer: Indexer | None,
    rate: Decimal,
    is_prefixed: bool,
    purchase_date: date,
    on_date: date,
    cdi: Sequence[SeriesPoint] = (),
    selic: Sequence[SeriesPoint] = (),
    ipca: Sequence[SeriesPoint] = (),
) -> Decimal:
    """Gross corrected value of ``principal`` on ``on_date``.

    Returns ``principal`` unchanged when ``on_date <= purchase_date``. Raises
    ``ValueError`` for an unsupported indexer or a missing required series.
    """
    if on_date <= purchase_date:
        return principal
    if is_prefixed:
        return principal * _power_factor(rate, purchase_date, on_date)
    if indexer is Indexer.CDI:
        return principal * _accumulate(cdi, purchase_date, on_date, multiplier=rate, name="CDI")
    if indexer is Indexer.SELIC:
        return principal * _accumulate(selic, purchase_date, on_date, multiplier=rate, name="SELIC")
    if indexer is Indexer.CDI_PLUS:
        cdi_factor = _accumulate(cdi, purchase_date, on_date, multiplier=_ONE, name="CDI")
        return principal * cdi_factor * _power_factor(rate, purchase_date, on_date)
    if indexer is Indexer.IPCA_PLUS:
        ipca_factor = _ipca_factor(ipca, purchase_date, on_date)
        return principal * ipca_factor * _power_factor(rate, purchase_date, on_date)
    raise ValueError(f"indexer nao suportado para valor presente: {indexer!r}")


def accumulated_rate_factor(series: Sequence[SeriesPoint], start: date, end: date, *, name: str = "indice") -> Decimal:
    """Compound ``(1 + value)`` over the series in ``[start, end)`` — the growth
    factor of a daily-rate index (CDI/SELIC) at 100%. Shared with the reports
    epic (#67) so index accumulation is never reimplemented."""
    return _accumulate(series, start, end, multiplier=_ONE, name=name)


def accumulated_ipca_factor(series: Sequence[SeriesPoint], start: date, end: date) -> Decimal:
    """IPCA correction factor over ``[start, end)`` (monthly composition with the
    documented business-day pro-rata). Shared with the reports epic (#67)."""
    return _ipca_factor(series, start, end)


def _accumulate(series: Sequence[SeriesPoint], start: date, end: date, *, multiplier: Decimal, name: str) -> Decimal:
    """Compound ``(1 + multiplier * value)`` over series points in ``[start, end)``."""
    if not series:
        raise ValueError(f"serie {name} vazia; impossivel valorar de {start} a {end}.")
    factor = _ONE
    for point in series:
        if start <= point.date < end:
            factor *= _ONE + multiplier * point.value
    return factor


def _power_factor(annual_rate: Decimal, start: date, end: date) -> Decimal:
    """``(1 + annual_rate) ** (business_days / 252)`` — the 252 convention."""
    business_days = business_days_between(start, end)
    exponent = Decimal(business_days) / _BUSINESS_DAYS_YEAR
    return (_ONE + annual_rate) ** exponent


def _first_of_next_month(year: int, month: int) -> date:
    return date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)


def _months_in_range(start: date, end: date) -> list[tuple[int, int]]:
    """(year, month) pairs touched by ``[start, end)`` (``end`` exclusive)."""
    months: list[tuple[int, int]] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month) and date(year, month, 1) < end:
        months.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def _ipca_factor(ipca: Sequence[SeriesPoint], start: date, end: date) -> Decimal:
    if not ipca:
        raise ValueError(f"serie IPCA vazia; impossivel valorar de {start} a {end}.")
    by_month = {(point.date.year, point.date.month): point.value for point in ipca}
    projection = max(ipca, key=lambda point: point.date).value  # latest published, used as fallback
    factor = _ONE
    for year, month in _months_in_range(start, end):
        month_start = date(year, month, 1)
        next_month = _first_of_next_month(year, month)
        du_month = business_days_between(month_start, next_month)
        if du_month == 0:
            continue
        du_window = business_days_between(max(start, month_start), min(end, next_month))
        monthly = by_month.get((year, month), projection)
        exponent = Decimal(du_window) / Decimal(du_month)
        factor *= (_ONE + monthly) ** exponent
    return factor
