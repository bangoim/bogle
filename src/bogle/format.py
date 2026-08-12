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

**Display.** ``decimal_separator`` (see :mod:`bogle.settings`) picks which
character separates the decimals; the other one groups the thousands. Money and
quantities are grouped, percentages are not — a weight or a return never needs
it. Each frontend calls :func:`configure` once at startup; the default is the
canonical ``1,234.56``.

**Input** is deliberately narrower and does not follow the setting: one
separator, always the cents (``,`` or ``.``), and thousands with no separator at
all. See :func:`to_canonical`.

**Privacy.** :func:`hide_amounts` replaces every amount with :data:`MASK` —
money *and* quantities, since a quantity times a public price is the amount
again. Percentages, weights and returns stay: they say how the portfolio is
doing without saying how much is in it. Only the TUI turns this on (``h``, or
``hide_values`` at startup); the CLI is left alone so its output stays scriptable,
and :func:`exact_or_none` (``--json``) is never masked either way.

The machine-readable path never goes through the localized helpers:
:func:`exact_or_none` (used by ``--json``) always emits a canonical decimal.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

DASH = "-"
"""Rendered in place of a value that is not available."""

MASK = "\u2022" * 6
"""Rendered in place of an amount while amounts are hidden."""

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
_HIDDEN = False


def configure(decimal_separator: str) -> None:
    """Set the separators for this process, from the user's setting."""
    global _SEPARATORS
    _SEPARATORS = separators_for(decimal_separator)


def separators() -> Separators:
    return _SEPARATORS


def hide_amounts(hidden: bool) -> None:
    """Mask (or unmask) every amount rendered from here on."""
    global _HIDDEN
    _HIDDEN = hidden


def amounts_hidden() -> bool:
    return _HIDDEN


def _localized(canonical: str) -> str:
    """Swap Python's ``1,234.56`` rendering for the configured separators."""
    if _SEPARATORS.is_canonical:
        return canonical
    return canonical.translate(
        str.maketrans({_CANONICAL_THOUSANDS: _SEPARATORS.thousands, CANONICAL_DECIMAL: _SEPARATORS.decimal})
    )


# ---------------------------------------------------------------- exibicao


def money(value: Decimal | None) -> str:
    """``1234.5`` -> ``"1,234.50"``, or :data:`MASK` while amounts are hidden."""
    if value is None:
        return DASH
    return MASK if _HIDDEN else _localized(f"{value:,.2f}")


def signed_money(value: Decimal | None) -> str:
    """``1234.5`` -> ``"+1,234.50"``; ``-1.2`` -> ``"-1.20"``."""
    if value is None:
        return DASH
    return MASK if _HIDDEN else _localized(f"{value:+,.2f}")


def pct(value: Decimal | None) -> str:
    """A fraction as a percentage: ``0.1234`` -> ``"12.34%"``."""
    return _localized(f"{value * 100:.2f}%") if value is not None else DASH


def signed_pct(value: Decimal | None) -> str:
    """A fraction as a signed percentage: ``0.1234`` -> ``"+12.34%"``."""
    return _localized(f"{value * 100:+.2f}%") if value is not None else DASH


def points(value: Decimal | None) -> str:
    """A difference between two returns, in percentage points: ``0.074`` -> ``"+7.40 p.p."``.

    The difference between two percentages is measured in points, not in percent:
    a portfolio 7.4 p.p. ahead of the CDI is not "7.4% ahead of it", which would
    be a different (and much smaller) claim. Only the number is localized — the
    dots in "p.p." are not separators.
    """
    return f"{_localized(f'{value * 100:+.2f}')} p.p." if value is not None else DASH


def exact(value: Decimal | None) -> str:
    """Every digit that matters, no more: ``10.00000000`` -> ``"10"``, ``0E+4`` -> ``"0"``.

    Masked with the amounts: a quantity and a public price multiply back into the
    amount.
    """
    if value is None:
        return DASH
    return MASK if _HIDDEN else _localized(format(value.normalize(), ",f"))


def rate(value: Decimal | None) -> str:
    """A contracted rate: ``1.10`` (110% of CDI), ``0.065`` (IPCA + 6.5%).

    Same digits :func:`exact` gives, but never masked: privacy hides *amounts*, and
    a rate is a term of the contract, not the size of the position. Masking it
    would hide nothing and cost the only view that shows the fixed income metadata.
    """
    return _localized(format(value.normalize(), "f")) if value is not None else DASH


def exact_or_none(value: Decimal | None) -> str | None:
    """:func:`exact` for JSON payloads: canonical decimal, no grouping, ``None`` kept."""
    return format(value.normalize(), "f") if value is not None else None


def sign_color(value: Decimal) -> str:
    """``"green"`` for gains (and zero), ``"red"`` for losses."""
    return "green" if value >= 0 else "red"


def signed(value: Decimal | None, *, percent: bool) -> str:
    """Signed, colored Rich markup — percentage when ``percent``, money otherwise.

    A masked amount comes back dim and uncolored: green on a row of dots would be
    reading a value that is not shown.
    """
    if value is None:
        return DASH
    if _HIDDEN and not percent:
        return f"[dim]{MASK}[/dim]"
    body = signed_pct(value) if percent else signed_money(value)
    return f"[{sign_color(value)}]{body}[/{sign_color(value)}]"


# ------------------------------------------------------------------ entrada


def to_canonical(value: str) -> str | None:
    """Rewrite a number the user typed the way ``Decimal`` accepts it.

    Input takes **one** separator, and it always marks the cents — ``,`` or ``.``,
    whichever the user prefers, independent of what the display is configured to
    do. Thousands are written without any separator at all: ``150000,75``, not
    ``150.000,75``.

    That rule is what makes input unambiguous. Accepting a thousands separator
    would not: a lone ``1.000`` is one thousand to someone reading the grouped
    display and one to someone following the canonical examples, and the two
    readings differ by a factor of a thousand. Refusing anything with a second
    separator keeps that guess off the table — ``None`` says so, and the caller
    turns it into a friendly error.

    Anything without a separator passes straight through, so scientific notation
    and plain garbage keep reaching ``Decimal`` (and its error message).
    """
    text = value.strip()
    sign = ""
    if text[:1] in "+-":
        sign, text = text[0], text[1:]
    if text.count(CANONICAL_DECIMAL) + text.count(",") > 1:
        return None
    return f"{sign}{text.replace(',', CANONICAL_DECIMAL)}"
