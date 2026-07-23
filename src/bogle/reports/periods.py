"""Shared period vocabulary for the reports epic (issue #67).

Every report command takes ``--period`` with a subset of the same tokens; a
period resolves to a lower-bound date (``None`` = unbounded, since inception).
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date

from bogle.domain.errors import ValidationError

ALL_PERIODS = ("1m", "12m", "2y", "5y", "10y", "ytd", "all", "total")

_MONTHS_BACK = {"1m": 1, "12m": 12, "2y": 24, "5y": 60, "10y": 120}
_UNBOUNDED = frozenset({"all", "total"})


def add_months(day: date, months: int) -> date:
    """``day`` shifted by ``months`` (negative = back), day clamped to the
    target month's length (Jan 31 - 1m -> Dec 31; Mar 31 - 1m -> Feb 28/29)."""
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, min(day.day, monthrange(year, month)[1]))


def parse_period(value: str, *, allowed: tuple[str, ...] = ALL_PERIODS) -> str:
    period = value.strip().lower()
    if period not in allowed:
        raise ValidationError(f"--period invalido: {value!r}. Valores aceitos: {', '.join(allowed)}.")
    return period


def period_start(period: str, *, today: date) -> date | None:
    """Lower bound of the window ending ``today``; ``None`` = since inception."""
    if period in _UNBOUNDED:
        return None
    if period == "ytd":
        return date(today.year, 1, 1)
    return add_months(today, -_MONTHS_BACK[period])
