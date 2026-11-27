"""Target weights that follow the user's age: the glide path engine (issue #78).

The plan of a passive portfolio changes with time: less risk as retirement
approaches, and less still after it (the logic of a target-date fund). The
trajectory is a few anchor ages in ``glide_path_points``, each with the target of
every ticker in it, and :func:`glide_targets` turns it into the targets of any age:
the first anchor before it, the last one after it, a straight line in between.

The targets change **once a year, on 01/01**, and on their own:
:func:`apply_glide_path_if_due` writes the targets of the year's age to
``assets.target_weight`` the first time the app runs in a new year, and both
frontends announce what changed (:func:`application_notice`). Nothing else reads
the trajectory: the rest of the app keeps reading ``assets.target_weight``.

While a trajectory is active (it has points and ``birth_year`` is set), the target
of a ticker in it belongs to the trajectory. A manual edit to anything other than
the trajectory's own value is refused (:func:`check_manual_target`) — it would
last until the next 01/01 and be overwritten without a word. The sale that empties
a position still clears its target (:mod:`bogle.closeout`); when the trajectory
gives the ticker a target next year, the sale says it comes back
(:func:`returning_target`).
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from fractions import Fraction
from math import floor

import psycopg
from psycopg.rows import DictRow

from bogle.domain.errors import GlidePathError, GlidePathTargetError
from bogle.format import pct
from bogle.repositories.assets import AssetRepository
from bogle.repositories.glide_path import GlidePathRepository
from bogle.settings import BIRTH_YEAR, GLIDE_PATH_APPLIED_YEAR, get_setting, set_value

GlidePath = Mapping[int, Mapping[str, Decimal]]
"""The anchors of a trajectory: ``{age: {ticker: target_weight}}``."""

_UNITS = 10_000
"""Units of 0.0001 in a whole: the four places of ``NUMERIC(5,4)``."""

_ZERO = Decimal("0")

_LOCK_KEY = 78
"""Advisory lock of the yearly application: two processes starting on 01/01 (the
TUI and a command) must not both apply and both announce."""


# ---------------------------------------------------------------- calculo


def age_in(year: int, birth_year: int) -> int:
    """The age that sets the targets of ``year``: only the year of birth counts."""
    return year - birth_year


def glide_targets(points: GlidePath, age: int) -> dict[str, Decimal]:
    """The target of every ticker of the trajectory at ``age``.

    Before the first anchor, the first; after the last, the last; between two,
    the straight line joining them. A ticker missing from an anchor weighs 0 there.
    The values are rounded to four places by the largest remainder, so they add
    up to exactly 1 — the same as each anchor, which is checked first
    (:class:`~bogle.domain.errors.GlidePathError` otherwise). An empty trajectory
    has no targets.
    """
    if not points:
        return {}
    _check_anchors(points)
    tickers = sorted({ticker for anchor in points.values() for ticker in anchor})
    ages = sorted(points)

    def weight(anchor_age: int, ticker: str) -> Fraction:
        return Fraction(points[anchor_age].get(ticker, _ZERO))

    if age <= ages[0]:
        exact = {ticker: weight(ages[0], ticker) for ticker in tickers}
    elif age >= ages[-1]:
        exact = {ticker: weight(ages[-1], ticker) for ticker in tickers}
    else:
        upper = ages[bisect_left(ages, age)]  # primeira ancora >= age
        lower = ages[ages.index(upper) - 1]
        step = Fraction(age - lower, upper - lower)
        exact = {
            ticker: weight(lower, ticker) + (weight(upper, ticker) - weight(lower, ticker)) * step for ticker in tickers
        }
    return _largest_remainder(exact)


def _check_anchors(points: GlidePath) -> None:
    for age in sorted(points):
        total = sum(points[age].values(), start=_ZERO)
        if total != 1:
            raise GlidePathError(f"Trajetória inválida: os alvos aos {age} anos somam {pct(total)}.")


def _largest_remainder(exact: Mapping[str, Fraction]) -> dict[str, Decimal]:
    """Four places per ticker, adding up to exactly the whole.

    Each value is cut to its four places, and the units the cuts left over go to
    the largest remainders (ties by ticker, so the result never depends on dict
    order). Rounding each value on its own would not do: 9.1667 + 47.0833 + 43.75
    is 100.0000 by luck, and three thirds are 99.99.
    """
    units = {ticker: floor(value * _UNITS) for ticker, value in exact.items()}
    whole = round(sum(exact.values(), start=Fraction(0)) * _UNITS)
    missing = whole - sum(units.values())
    by_remainder = sorted(exact, key=lambda ticker: (-(exact[ticker] * _UNITS - units[ticker]), ticker))
    for ticker in by_remainder[:missing]:
        units[ticker] += 1
    return {ticker: Decimal(units[ticker]).scaleb(-4) for ticker in exact}


# ---------------------------------------------------------------- trajetoria ativa


@dataclass(frozen=True, slots=True)
class ActiveGlidePath:
    """A trajectory in force: it has points, and the year of birth is known."""

    points: GlidePath
    birth_year: int

    @property
    def tickers(self) -> frozenset[str]:
        return frozenset(ticker for anchor in self.points.values() for ticker in anchor)

    def targets_in(self, year: int) -> dict[str, Decimal]:
        return glide_targets(self.points, age_in(year, self.birth_year))


def active_glide_path(conn: psycopg.Connection[DictRow]) -> ActiveGlidePath | None:
    """The trajectory in force, or ``None`` without points or without ``birth_year``."""
    birth_year = get_setting(conn, BIRTH_YEAR)
    if birth_year is None:
        return None
    points = GlidePathRepository(conn).points()
    if not points:
        return None
    return ActiveGlidePath(points=points, birth_year=birth_year)


# ---------------------------------------------------------------- aplicacao anual


@dataclass(frozen=True, slots=True)
class TargetChange:
    ticker: str
    previous: Decimal
    target: Decimal


@dataclass(frozen=True, slots=True)
class GlideApplication:
    """The targets of ``year`` written to the assets, and what that changed."""

    year: int
    age: int
    changes: tuple[TargetChange, ...]
    """Only the tickers whose target moved, by ticker."""


def apply_glide_path_if_due(conn: psycopg.Connection[DictRow], today: date) -> GlideApplication | None:
    """Write the targets of ``today.year`` once a year, and say what changed.

    ``None`` when there is nothing to do: no trajectory in force, or the year
    already applied. Otherwise the targets of the year's age go to
    ``assets.target_weight`` — a ticker out of the trajectory goes to 0 — and
    ``glide_path_applied_year`` is recorded, all in one transaction: a failure
    leaves the targets and the year as they were, and the next run tries again.

    The invariant ``SUM(target_weight) <= 1`` is checked after every write
    (:meth:`AssetRepository.update_weight`), so the targets that go down are
    written before the ones that go up: the sum only ever falls towards the new
    total, then rises to it.
    """
    glide = active_glide_path(conn)
    if glide is None or not _due(conn, today):
        return None
    targets = glide.targets_in(today.year)
    repo = AssetRepository(conn)
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
        if not _due(conn, today):
            return None  # outro processo aplicou enquanto este esperava o lock
        changes = tuple(
            TargetChange(asset.ticker, asset.target_weight, targets.get(asset.ticker, _ZERO))
            for asset in repo.list()
            if asset.target_weight != targets.get(asset.ticker, _ZERO)
        )
        # Descidas antes das subidas (False < True): a soma nunca passa de 1 no meio.
        for change in sorted(changes, key=lambda change: change.target > change.previous):
            repo.update_weight(change.ticker, change.target)
        set_value(conn, GLIDE_PATH_APPLIED_YEAR, today.year)
    return GlideApplication(year=today.year, age=age_in(today.year, glide.birth_year), changes=changes)


def _due(conn: psycopg.Connection[DictRow], today: date) -> bool:
    applied = get_setting(conn, GLIDE_PATH_APPLIED_YEAR)
    return applied is None or applied < today.year


def application_notice(application: GlideApplication | None) -> str | None:
    """What both frontends say about an application; ``None`` when nothing moved."""
    if application is None or not application.changes:
        return None
    changes = ", ".join(
        f"{change.ticker} {pct(change.previous)} -> {pct(change.target)}" for change in application.changes
    )
    if len(application.changes) == 1:
        return f"alvo de {application.year} aplicado pela trajetória: {changes}."
    return f"alvos de {application.year} aplicados pela trajetória: {changes}."


def failure_notice(reason: str) -> str:
    """What both frontends say when the application failed (it runs again next time)."""
    return f"alvos da trajetória não aplicados: {reason}"


# ---------------------------------------------------------------- edicao manual e venda


def check_manual_target(conn: psycopg.Connection[DictRow], ticker: str, target: Decimal, today: date) -> None:
    """Refuse a manual target for a ticker of the trajectory in force.

    The trajectory's own value for the year is accepted: it is not a change to
    the plan, and it is what undoing a cleared target (:mod:`bogle.closeout`)
    writes back. Anything else would last until the next 01/01.
    """
    glide = active_glide_path(conn)
    ticker = ticker.upper()
    if glide is None or ticker not in glide.tickers:
        return
    planned = glide.targets_in(today.year)[ticker]
    if target != planned:
        raise GlidePathTargetError(ticker, today.year, planned)


def returning_target(conn: psycopg.Connection[DictRow], ticker: str, today: date) -> Decimal | None:
    """The target the trajectory gives ``ticker`` on the next 01/01, when above 0."""
    glide = active_glide_path(conn)
    ticker = ticker.upper()
    if glide is None or ticker not in glide.tickers:
        return None
    target = glide.targets_in(today.year + 1)[ticker]
    return target if target > _ZERO else None
