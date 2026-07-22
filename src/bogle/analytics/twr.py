"""Time-Weighted Return (issue #20). Pure ``Decimal`` math, no I/O.

TWR removes the effect of the size and timing of external cash flows, isolating
market performance. The period is split at every cash flow into sub-periods; each
sub-period return is chained geometrically::

    TWR = prod(1 + r_i) - 1

Model (decision D — valuation only at cash-flow dates + period bounds):

- Portfolio value = ``sum(shares_held * price)``, priced from the history's
  ``close`` (not ``adjustedClose`` — dividends are handled explicitly below, so an
  adjusted series would double-count them). The "price on the date or the nearest
  earlier one" rule covers weekends/holidays.
- Trades are external flows and are excluded from return: a BUY/SELL changes the
  invested base but not the sub-period's return. Trades are valued at the same
  ``close``, so the base moves by exactly the traded market value.
- Income (DIVIDEND/JCP/RENDIMENTO/INTEREST) is *return*, credited (gross) to the
  sub-period ending on its date — since the ex-date price already dropped by the
  payout, adding the income back recovers the total return.
- Sub-periods whose opening base is zero (no capital at work yet) are skipped, so
  an asset bought mid-period contributes only from its purchase onward. Income on
  the exact ``start`` date is treated as earned before the window and ignored.

The valuator is injectable so a daily series or a fixed-income present-value engine
can drop in later without touching this code.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal

from bogle.data.models import HistPoint
from bogle.domain.transactions import Transaction, TransactionType

_ZERO = Decimal("0")
_ONE = Decimal("1")

_INCOME_TYPES = frozenset(
    {
        TransactionType.DIVIDEND,
        TransactionType.JCP,
        TransactionType.RENDIMENTO,
        TransactionType.INTEREST,
    }
)

# Given the shares held per ticker on a date, return the portfolio's market value.
Valuator = Callable[[Mapping[str, Decimal], date], Decimal]


def _as_date(value: date) -> date:
    """Normalize a ``datetime`` (or ``date``) to a calendar ``date``."""
    return value.date() if isinstance(value, datetime) else value


def _delta_shares(txn: Transaction) -> Decimal:
    if txn.transaction_type == TransactionType.BUY:
        return txn.shares
    if txn.transaction_type == TransactionType.SELL:
        return -txn.shares
    return _ZERO


def _price_on_or_before(history: Sequence[HistPoint], on: date) -> Decimal | None:
    """Close of the latest bar dated on or before ``on`` (weekend/holiday rule)."""
    best_close: Decimal | None = None
    best_date: date | None = None
    for point in history:
        point_date = _as_date(point.date)
        if point_date <= on and (best_date is None or point_date > best_date):
            best_date, best_close = point_date, point.close
    return best_close


def price_history_valuator(price_history: Mapping[str, Sequence[HistPoint]]) -> Valuator:
    """Build a valuator that marks holdings to the ``close`` of each ticker."""

    def valuate(holdings: Mapping[str, Decimal], on: date) -> Decimal:
        total = _ZERO
        for ticker, shares in holdings.items():
            if shares == _ZERO:
                continue
            price = _price_on_or_before(price_history.get(ticker, ()), on)
            if price is None:
                raise ValueError(f"Sem preco para '{ticker}' em ou antes de {on.isoformat()}.")
            total += shares * price
        return total

    return valuate


def shares_held(transactions: Sequence[Transaction], on: date) -> dict[str, Decimal]:
    """Shares held per ticker at end of day ``on`` (BUY - SELL, trades on the day
    included). Shared with the reports epic (#67) for patrimony reconstruction."""
    return _shares_as_of(transactions, on, inclusive=True)


def _shares_as_of(txns: Sequence[Transaction], on: date, *, inclusive: bool) -> dict[str, Decimal]:
    holdings: dict[str, Decimal] = defaultdict(lambda: _ZERO)
    for txn in txns:
        txn_date = _as_date(txn.date)
        if (txn_date <= on) if inclusive else (txn_date < on):
            holdings[txn.ticker] += _delta_shares(txn)
    return holdings


def _income_on(txns: Sequence[Transaction], on: date) -> Decimal:
    total = _ZERO
    for txn in txns:
        if txn.transaction_type in _INCOME_TYPES and _as_date(txn.date) == on:
            total += txn.total_investment
    return total


def compute_twr(
    transactions: Sequence[Transaction],
    price_history: Mapping[str, Sequence[HistPoint]] | None,
    start: date,
    end: date,
    *,
    valuator: Valuator | None = None,
) -> Decimal:
    """Time-weighted return over ``[start, end]``.

    Pass either ``price_history`` (marks holdings to close) or an explicit
    ``valuator``. Transactions before ``start`` set the opening position;
    transactions after ``end`` are ignored. Returns ``0`` when no capital was ever
    at work in the window.
    """
    start, end = _as_date(start), _as_date(end)
    if end < start:
        raise ValueError("end deve ser >= start.")
    if valuator is None:
        if price_history is None:
            raise ValueError("Forneca price_history ou um valuator.")
        valuator = price_history_valuator(price_history)

    txns = sorted((t for t in transactions if _as_date(t.date) <= end), key=lambda t: _as_date(t.date))

    valuation_dates = {start, end}
    valuation_dates.update(d for t in txns if start <= (d := _as_date(t.date)) <= end)

    factor = _ONE
    opening_base: Decimal | None = None
    for index, on in enumerate(sorted(valuation_dates)):
        shares_before = _shares_as_of(txns, on, inclusive=False)
        shares_after = _shares_as_of(txns, on, inclusive=True)
        mv_before = valuator(shares_before, on)
        if index > 0 and opening_base is not None and opening_base > _ZERO:
            subperiod_return = (mv_before + _income_on(txns, on)) / opening_base - _ONE
            factor *= _ONE + subperiod_return
        opening_base = valuator(shares_after, on)
    return factor - _ONE


def compute_twr_per_ticker(
    transactions: Sequence[Transaction],
    price_history: Mapping[str, Sequence[HistPoint]],
    start: date,
    end: date,
) -> dict[str, Decimal]:
    """TWR computed independently for each ticker that appears in ``transactions``."""
    end_date = _as_date(end)
    tickers = sorted({t.ticker for t in transactions if _as_date(t.date) <= end_date})
    result: dict[str, Decimal] = {}
    for ticker in tickers:
        ticker_txns = [t for t in transactions if t.ticker == ticker]
        ticker_history = {ticker: price_history.get(ticker, ())}
        result[ticker] = compute_twr(ticker_txns, ticker_history, start, end)
    return result
