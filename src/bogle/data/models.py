"""Provider-agnostic market-data types.

Every client (brapi, yfinance, ...) parses its own JSON into these dataclasses so
the rest of the app never sees a provider's wire format. Monetary values are
``Decimal`` (never float); the clients build them from strings/JSON parsed with
``parse_float=Decimal``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Quote:
    """A current price snapshot for a ticker or index.

    ``symbol`` is the provider's resolved symbol; ``requested_symbol`` is what the
    caller asked for and differs only when the provider reports a rename.
    """

    symbol: str
    price: Decimal
    currency: str
    time: datetime
    requested_symbol: str
    previous_close: Decimal | None = None
    change: Decimal | None = None
    change_percent: Decimal | None = None

    @property
    def renamed(self) -> bool:
        """True when the provider resolved the request to a different symbol."""
        return self.symbol != self.requested_symbol


@dataclass(frozen=True, slots=True)
class HistPoint:
    """One OHLCV bar of a historical series.

    ``date`` is timezone-aware (UTC). ``adjusted_close`` is the close adjusted for
    splits/dividends when the provider supplies it. Consumed by the TWR engine.
    """

    date: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    adjusted_close: Decimal | None = None
