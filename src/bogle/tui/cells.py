"""Table cells for the TUI (issue #73).

``DataTable`` has no per-column alignment, so numeric cells carry their own
``justify="right"``. The rendering itself is not reinvented here: every helper
delegates to :mod:`bogle.format`, which keeps the interface showing exactly what
the CLI shows.
"""

from __future__ import annotations

from decimal import Decimal

from rich.text import Text

from bogle import format as fmt


def text(value: str) -> Text:
    return Text(value)


def ticker(value: str) -> Text:
    """A ticker, in the cyan the CLI tables use."""
    return Text(value, style="bold cyan")


def right(value: str) -> Text:
    return Text(value, justify="right")


def money(value: Decimal | None) -> Text:
    return right(fmt.money(value))


def pct(value: Decimal | None) -> Text:
    return right(fmt.pct(value))


def exact(value: Decimal | None) -> Text:
    return right(fmt.exact(value))


def signed(value: Decimal | None, *, percent: bool) -> Text:
    """Signed and colored (green >= 0, red < 0), from the shared markup."""
    return Text.from_markup(fmt.signed(value, percent=percent), justify="right")


def points(value: Decimal | None) -> Text:
    """A difference between two returns in percentage points, colored by sign."""
    if value is None:
        return right(fmt.DASH)
    return Text(fmt.points(value), style=fmt.sign_color(value), justify="right")


def total(value: str) -> Text:
    """A summary cell, set apart from the rows it adds up."""
    return Text(value, style="bold", justify="right")
