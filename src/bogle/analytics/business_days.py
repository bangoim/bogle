"""Brazilian business-day calendar (ANBIMA / national bank holidays).

Pure and dependency-free: the moving holidays (Carnaval Monday/Tuesday, Good
Friday, Corpus Christi) are derived from Easter via the Gregorian computus; the
fixed national holidays are hardcoded, including Consciencia Negra (national from
2024, Lei 14.759/2023). Used for the 252 business-day convention in the
fixed-income present-value math.

Only national bank holidays are modeled (state/municipal ones do not move the
financial-market calendar), matching ANBIMA's list.
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import cache

# National fixed holidays as (month, day).
_FIXED_HOLIDAYS = ((1, 1), (4, 21), (5, 1), (9, 7), (10, 12), (11, 2), (11, 15), (12, 25))
_CONSCIENCIA_NEGRA_FROM = 2024  # national from 2024


def _easter_sunday(year: int) -> date:
    """Easter Sunday for ``year`` (Anonymous Gregorian algorithm / computus)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    weeks = (32 + 2 * e + 2 * i - h - k) % 7
    p = (a + 11 * h + 22 * weeks) // 451
    month = (h + weeks - 7 * p + 114) // 31
    day = 1 + (h + weeks - 7 * p + 114) % 31
    return date(year, month, day)


@cache
def holidays(year: int) -> frozenset[date]:
    """The national bank holidays observed in ``year``."""
    days = {date(year, month, day) for month, day in _FIXED_HOLIDAYS}
    if year >= _CONSCIENCIA_NEGRA_FROM:
        days.add(date(year, 11, 20))
    easter = _easter_sunday(year)
    days.add(easter - timedelta(days=2))  # Sexta-feira Santa (Good Friday)
    days.add(easter - timedelta(days=48))  # Carnaval (segunda)
    days.add(easter - timedelta(days=47))  # Carnaval (terca)
    days.add(easter + timedelta(days=60))  # Corpus Christi
    return frozenset(days)


def is_business_day(day: date) -> bool:
    """True when ``day`` is a weekday and not a national holiday."""
    return day.weekday() < 5 and day not in holidays(day.year)


def previous_business_day(day: date) -> date:
    """The latest business day strictly before ``day``.

    Prices only exist for business days, so a "previous close" reference date
    has to land on one: asked about a Monday it answers Friday, and it walks
    back over holidays too.
    """
    previous = day - timedelta(days=1)
    while not is_business_day(previous):
        previous -= timedelta(days=1)
    return previous


def business_days_between(start: date, end: date) -> int:
    """Number of business days in the half-open interval ``[start, end)``.

    Zero when ``end <= start``. Counting ``start`` (inclusive) and excluding
    ``end`` makes same-day valuation contribute zero elapsed business days.
    """
    count = 0
    day = start
    while day < end:
        if is_business_day(day):
            count += 1
        day += timedelta(days=1)
    return count
