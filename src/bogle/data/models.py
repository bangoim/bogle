"""Provider-agnostic market-data types.

Every client (brapi, yfinance, ...) parses its own JSON into these dataclasses so
the rest of the app never sees a provider's wire format. Monetary values are
``Decimal`` (never float); the clients build them from strings/JSON parsed with
``parse_float=Decimal``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
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


@dataclass(frozen=True, slots=True)
class SeriesPoint:
    """One observation of a macro time series (CDI, IPCA, SELIC, ...).

    ``date`` is a calendar day at the series' own periodicity (daily rates are
    dated per business day; monthly ones fall on the first of the month).
    ``value`` is a fraction (0.0053 for 0.53%), unless the caller asked the client
    for raw values.
    """

    date: date
    value: Decimal


@dataclass(frozen=True, slots=True)
class TesouroQuote:
    """A Tesouro Direto title priced from the Tesouro Transparente open data.

    ``title`` is the canonical name (``"Tesouro IPCA+ 2035"``). Prices are the
    "manhã" (morning) unit prices for ``base_date`` — typically D-1, not intraday.
    All three unit prices are kept so callers pick their convention: mark-to-market
    (what a redemption pays today) uses ``pu_venda``; ``pu_compra`` is the
    investment price and ``pu_base`` the reference. Rates are annual, as fractions.
    """

    title: str
    bond_type: str
    maturity: date
    base_date: date
    pu_compra: Decimal | None = None
    pu_venda: Decimal | None = None
    pu_base: Decimal | None = None
    rate_compra: Decimal | None = None
    rate_venda: Decimal | None = None
