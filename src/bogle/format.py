"""Value formatters shared by the user-facing frontends (issue #73).

Every ``cli/*.py`` module grew its own private copy of the same four or five
helpers (``_money``, ``_pct``, ``_qty``, ``_signed``, ``_fmt``); the TUI would
have been the third generation of the same code. They live here instead.

Two conventions worth knowing:

- ``None`` means "not available" (an unpriced position, an index without data)
  and renders as :data:`DASH`, never as zero.
- :func:`signed` returns Rich *markup* (green when >= 0, red when < 0), which
  both frontends render — Rich tables directly, Textual through
  :meth:`~rich.text.Text.from_markup`.

Money keeps two decimals and no thousands separator; :func:`exact` is the
escape hatch for values whose scale carries meaning (quantities, and anything
read back from a ``NUMERIC`` column, where ``100.00000000`` should read
``100``).
"""

from __future__ import annotations

from decimal import Decimal

DASH = "-"
"""Rendered in place of a value that is not available."""


def money(value: Decimal | None) -> str:
    """``1234.5`` -> ``"1234.50"``."""
    return f"{value:.2f}" if value is not None else DASH


def signed_money(value: Decimal | None) -> str:
    """``1234.5`` -> ``"+1234.50"``; ``-1.2`` -> ``"-1.20"``."""
    return f"{value:+.2f}" if value is not None else DASH


def pct(value: Decimal | None) -> str:
    """A fraction as a percentage: ``0.1234`` -> ``"12.34%"``."""
    return f"{value * 100:.2f}%" if value is not None else DASH


def signed_pct(value: Decimal | None) -> str:
    """A fraction as a signed percentage: ``0.1234`` -> ``"+12.34%"``."""
    return f"{value * 100:+.2f}%" if value is not None else DASH


def exact(value: Decimal | None) -> str:
    """Normalized and non-scientific: ``10.00000000`` -> ``"10"``, ``0E+4`` -> ``"0"``."""
    return format(value.normalize(), "f") if value is not None else DASH


def exact_or_none(value: Decimal | None) -> str | None:
    """:func:`exact` for JSON payloads, where absent must stay ``null``."""
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
