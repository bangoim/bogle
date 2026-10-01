"""Weighted-average acquisition cost from a transaction history (issues 4.5/4.6).

Pure functions over ``Transaction`` rows. The ``holdings`` view deliberately
does not compute average cost or realized PnL, so this is the single source of
truth reused by income-tax (4.3), current position (6.1) and reports (8.5).

Brazilian "preco medio ponderado" method (accepted by the RFB for individuals),
now as a **sequential replay** (issue #68) — the aggregated formula diverges
from the RFB whenever a buy happens after a sale:

- On each BUY the average is recomputed over the *remaining* quantity:
  ``avg = (qty * avg + total_cost) / (qty + shares)``. Purchase fees are already
  folded into ``total_cost``, so they compose the cost.
- On each SELL the quantity drops but the average of the units still held
  **never** changes; the sale's cost basis is ``avg * shares`` at that moment
  and its realized gain is ``proceeds - sale fees - cost basis`` (same formula
  as ``income_tax_on_sale``).
- Fixed income without daily liquidity uses the BUY ``shares=1`` /
  ``unit_price=amount applied`` convention; no special case needed.

Everything is ``Decimal``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from bogle.domain.errors import ValidationError
from bogle.domain.transactions import Transaction, TransactionType

_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class RealizedSale:
    """One SELL with the cost basis it carried at the moment it happened."""

    transaction_id: int
    ticker: str
    date: datetime
    shares: Decimal
    proceeds: Decimal
    fees: Decimal
    cost_basis: Decimal

    @property
    def gain(self) -> Decimal:
        return self.proceeds - self.fees - self.cost_basis


@dataclass(frozen=True, slots=True)
class TickerCostBasis:
    ticker: str
    remaining_shares: Decimal
    average_cost: Decimal
    """Average cost of the units still held (unchanged by sales)."""

    @property
    def invested(self) -> Decimal:
        """What the units still held cost: the "valor investido" (issue #85).

        Average cost times quantity, so a sale takes out exactly the cost of the
        units it sold and the gain it realized never touches what is left. Zero
        once the position is closed.
        """
        return self.average_cost * self.remaining_shares


def _sort_key(txn: Transaction) -> tuple:
    """Day by day, and inside a day the purchases before the sales.

    The same reading of a day the ledger is held to (see
    :mod:`bogle.domain.ledger`): a sale is covered by what was held at the close
    of its day, so a purchase and a sale of the same date are netted whatever
    order they were registered in. Ordering by registration instead would refuse
    a day the app accepted.
    """
    day = txn.date.date() if isinstance(txn.date, datetime) else txn.date
    return (day, txn.transaction_type is not TransactionType.BUY, txn.id)


def replay_cost_basis(
    transactions: list[Transaction],
) -> tuple[dict[str, TickerCostBasis], list[RealizedSale]]:
    """Chronological replay of BUY/SELL per ticker (income events are ignored).

    Returns the current cost-basis state per ticker (only tickers that had at
    least one BUY) and every sale with its realized gain. Raises
    ``ValidationError`` on a sale without enough quantity held — that is broken
    data, not a 0 result.
    """
    quantities: dict[str, Decimal] = {}
    averages: dict[str, Decimal] = {}
    sales: list[RealizedSale] = []
    for txn in sorted(transactions, key=_sort_key):
        if txn.transaction_type is TransactionType.BUY:
            quantity = quantities.get(txn.ticker, _ZERO)
            average = averages.get(txn.ticker, _ZERO)
            averages[txn.ticker] = (quantity * average + txn.total_cost) / (quantity + txn.shares)
            quantities[txn.ticker] = quantity + txn.shares
        elif txn.transaction_type is TransactionType.SELL:
            quantity = quantities.get(txn.ticker, _ZERO)
            if txn.ticker not in averages or txn.shares > quantity:
                raise ValidationError(
                    f"Venda de {txn.shares} '{txn.ticker}' sem quantidade suficiente em carteira "
                    f"(restam {quantity}). Historico de transacoes inconsistente."
                )
            sales.append(
                RealizedSale(
                    transaction_id=txn.id,
                    ticker=txn.ticker,
                    date=txn.date,
                    shares=txn.shares,
                    proceeds=txn.total_investment,
                    fees=txn.fees,
                    cost_basis=averages[txn.ticker] * txn.shares,
                )
            )
            quantities[txn.ticker] = quantity - txn.shares
    states = {
        ticker: TickerCostBasis(ticker=ticker, remaining_shares=quantities[ticker], average_cost=average)
        for ticker, average in averages.items()
    }
    return states, sales


def replay_by_ticker(transactions: list[Transaction]) -> tuple[dict[str, TickerCostBasis], list[str]]:
    """The same replay, one ticker at a time: a broken history only costs its own.

    :func:`replay_cost_basis` refuses the whole ledger over one sale larger than
    the position at the time, which is right for ``bogle profit`` (a realized
    gain built on it would be wrong) and wrong for a screen that values the rest
    of the portfolio. Here that ticker is set aside and named in the second
    element, sorted, for the caller to report; every other ticker keeps its state.
    """
    by_ticker: dict[str, list[Transaction]] = {}
    for txn in transactions:
        by_ticker.setdefault(txn.ticker, []).append(txn)
    states: dict[str, TickerCostBasis] = {}
    refused: list[str] = []
    for ticker, history in by_ticker.items():
        try:
            ticker_states, _ = replay_cost_basis(history)
        except ValidationError:
            refused.append(ticker)
            continue
        states.update(ticker_states)
    return states, sorted(refused)


def average_cost_per_share(transactions: list[Transaction]) -> Decimal:
    """Weighted-average cost of one share of a single ticker's history.

    Delegates to the sequential replay (#68), so a buy after a sale composes
    with the *remaining* quantity — the RFB rule. Raises ``ValidationError``
    if there are no purchases (the average is undefined, not 0).
    """
    if not any(t.transaction_type is TransactionType.BUY for t in transactions):
        raise ValidationError("Sem compras registradas para calcular o custo medio.")
    states, _ = replay_cost_basis(transactions)
    if len(states) != 1:
        raise ValidationError("Historico com mais de um ticker; calcule o custo medio por ticker.")
    return next(iter(states.values())).average_cost


def acquisition_cost(transactions: list[Transaction], shares_sold: Decimal) -> Decimal:
    """Acquisition cost of ``shares_sold`` units = average cost * quantity.

    The cost basis to subtract from sale proceeds when computing capital gains.
    Uses the weighted-average price, so a partial sale carries a proportional
    slice of the total cost and leaves the remaining units' average unchanged.
    """
    return average_cost_per_share(transactions) * shares_sold
