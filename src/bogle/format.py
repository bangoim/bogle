"""Number format shared by the user-facing frontends (issues #73, #74).

Every ``cli/*.py`` module grew its own private copy of the same four or five
helpers (``_money``, ``_pct``, ``_qty``, ``_signed``, ``_fmt``); the TUI would
have been the third generation of the same code. They live here instead, and so
does the reverse direction — reading a number the user typed — so display and
input can never disagree about what a separator means.

Two conventions worth knowing:

- ``None`` means "not available" (an unpriced position, an index without data)
  and renders as :data:`DASH`, never as zero.
- :func:`signed` returns Rich *markup* (green when >= 0, red when < 0), which
  both frontends render — Rich tables directly, Textual through
  :meth:`~rich.text.Text.from_markup`.

**Separators.** ``decimal_separator`` (see :mod:`bogle.settings`) picks which
character separates the decimals; the other one groups the thousands. Money and
quantities are grouped, percentages are not — a weight or a return never needs
it. Each frontend calls :func:`configure` once at startup; the default is the
canonical ``1,234.56``.

The machine-readable path never goes through the localized helpers:
:func:`exact_or_none` (used by ``--json``) always emits a canonical decimal.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

DASH = "-"
"""Rendered in place of a value that is not available."""

CANONICAL_DECIMAL = "."
"""What ``Decimal`` itself accepts, and what ``--json`` always emits."""

_CANONICAL_THOUSANDS = ","
"""What Python's ``,`` format spec produces, before localization."""


@dataclass(frozen=True, slots=True)
class Separators:
    decimal: str
    thousands: str

    @property
    def is_canonical(self) -> bool:
        return self.decimal == CANONICAL_DECIMAL


def separators_for(decimal_separator: str) -> Separators:
    """The pair implied by a decimal separator: the other character groups."""
    if decimal_separator == CANONICAL_DECIMAL:
        return Separators(decimal=CANONICAL_DECIMAL, thousands=_CANONICAL_THOUSANDS)
    return Separators(decimal=decimal_separator, thousands=CANONICAL_DECIMAL)


_SEPARATORS = separators_for(CANONICAL_DECIMAL)


def configure(decimal_separator: str) -> None:
    """Set the separators for this process, from the user's setting."""
    global _SEPARATORS
    _SEPARATORS = separators_for(decimal_separator)


def separators() -> Separators:
    return _SEPARATORS


def sample() -> str:
    """``1,234.56`` in the configured format — for error messages."""
    return _localized("1,234.56")


def _localized(canonical: str) -> str:
    """Swap Python's ``1,234.56`` rendering for the configured separators."""
    if _SEPARATORS.is_canonical:
        return canonical
    return canonical.translate(
        str.maketrans({_CANONICAL_THOUSANDS: _SEPARATORS.thousands, CANONICAL_DECIMAL: _SEPARATORS.decimal})
    )


# ---------------------------------------------------------------- exibicao


def money(value: Decimal | None) -> str:
    """``1234.5`` -> ``"1,234.50"``."""
    return _localized(f"{value:,.2f}") if value is not None else DASH


def signed_money(value: Decimal | None) -> str:
    """``1234.5`` -> ``"+1,234.50"``; ``-1.2`` -> ``"-1.20"``."""
    return _localized(f"{value:+,.2f}") if value is not None else DASH


def pct(value: Decimal | None) -> str:
    """A fraction as a percentage: ``0.1234`` -> ``"12.34%"``."""
    return _localized(f"{value * 100:.2f}%") if value is not None else DASH


def signed_pct(value: Decimal | None) -> str:
    """A fraction as a signed percentage: ``0.1234`` -> ``"+12.34%"``."""
    return _localized(f"{value * 100:+.2f}%") if value is not None else DASH


def exact(value: Decimal | None) -> str:
    """Every digit that matters, no more: ``10.00000000`` -> ``"10"``, ``0E+4`` -> ``"0"``."""
    return _localized(format(value.normalize(), ",f")) if value is not None else DASH


def exact_or_none(value: Decimal | None) -> str | None:
    """:func:`exact` for JSON payloads: canonical decimal, no grouping, ``None`` kept."""
    return format(value.normalize(), "f") if value is not None else None


def sign_color(value: Decimal) -> str:
    """``"green"`` for gains (and zero), ``"red"`` for losses."""
    return "green" if value >= 0 else "red"


def signed(value: Decimal | None, *, percent: bool) -> str:
    """Signed, colored Rich markup — percentage when ``percent``, money otherwise."""
    if value is None:
        return DASH
    body = signed_pct(value) if percent else signed_money(value)
    color = sign_color(value)
    return f"[{color}]{body}[/{color}]"


# ------------------------------------------------------------------ entrada

MISPLACED = "misplaced"
"""A thousands mark outside a group of three."""
AMBIGUOUS = "ambiguous"
"""A dot that could be either the decimal point or a thousands mark."""


@dataclass(frozen=True, slots=True)
class Reading:
    """A typed number in canonical form, or why it could not be read."""

    canonical: str | None
    reason: str = ""


def read_number(value: str) -> Reading:
    """Rewrite a number the user typed the way ``Decimal`` accepts it.

    Accepts the configured separators, including thousands in groups of three,
    and always the canonical dot decimal — every example in the README and in
    ``--help`` uses it, and it is what the tests and scripts type.

    Refuses instead of guessing when a string has more than one reading, because
    the two readings differ by a factor of a thousand:

    - ``126,25`` under a dot decimal is :data:`MISPLACED` — there the comma can
      only group thousands, and 25 is not a group of three;
    - ``1.000`` under a comma decimal is :data:`AMBIGUOUS` — one thousand or one,
      and no rule can tell. ``1.000,00`` and ``1000`` are both unambiguous.

    Anything without a separator passes straight through, so scientific notation
    and plain garbage keep reaching ``Decimal`` (and its error message).
    """
    text = value.strip()
    sign = ""
    if text[:1] in "+-":
        sign, text = text[0], text[1:]
    decimal, thousands = _SEPARATORS.decimal, _SEPARATORS.thousands

    if decimal in text:
        if text.count(decimal) > 1:
            return Reading(None, MISPLACED)
        integer, _, fraction = text.rpartition(decimal)
        if not _grouped(integer, thousands):
            return Reading(None, MISPLACED)
        return Reading(f"{sign}{integer.replace(thousands, '')}{CANONICAL_DECIMAL}{fraction}")

    if thousands in text:
        if thousands == CANONICAL_DECIMAL and text.count(thousands) == 1:
            _, _, tail = text.partition(thousands)
            # `1.000` com decimal virgula: milhar ou decimal canonico? As duas
            # leituras diferem por mil, entao ninguem chuta. Ja `0.750` nao e
            # ambiguo: nenhum agrupamento comeca com zero, logo e decimal.
            if _grouped(text, thousands) and len(tail) == 3:
                return Reading(None, AMBIGUOUS)
            return Reading(f"{sign}{text}")
        if not _grouped(text, thousands):
            return Reading(None, MISPLACED)
        return Reading(f"{sign}{text.replace(thousands, '')}")

    return Reading(f"{sign}{text}")


def _grouped(text: str, thousands: str) -> bool:
    """True when ``text`` carries no thousands mark, or carries them in threes.

    A grouped number never opens with a zero (``0,750`` is not seven hundred and
    fifty in any convention), so rejecting that head keeps ``0.750`` readable as
    a decimal and stops ``0,750`` from silently becoming ``750``.
    """
    if thousands not in text:
        return True
    head, *groups = text.split(thousands)
    if not head.isdigit() or len(head) > 3 or head.startswith("0"):
        return False
    return all(len(g) == 3 and g.isdigit() for g in groups)
