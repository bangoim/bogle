"""Weighted-average acquisition cost from a transaction history (issue 4.5).

Pure functions over ``Transaction`` rows. The ``holdings`` view deliberately
does not compute average cost or realized PnL, so this is the single source of
truth reused by income-tax (4.3), current position (6.1) and reports (8.5).

Brazilian "preco medio ponderado" method (accepted by the RFB for individuals):

- Average cost per share = SUM(BUY total_cost) / SUM(BUY shares). Purchase fees
  are already folded into ``total_cost`` (= total_investment + fees), so they
  compose the cost.
- Sales reduce the remaining quantity but **never** change the average cost of
  the units still held.
- Fixed income without daily liquidity uses the BUY ``shares=1`` /
  ``unit_price=amount applied`` convention; the same formula yields
  ``total_cost`` of the single unit, so it needs no special case.

Everything is ``Decimal``.
"""

from __future__ import annotations

from decimal import Decimal

from bogle.domain.errors import ValidationError
from bogle.domain.transactions import Transaction, TransactionType

_ZERO = Decimal("0")


def average_cost_per_share(transactions: list[Transaction]) -> Decimal:
    """Weighted-average cost of one share, from the BUYs in ``transactions``.

    Only ``BUY`` rows count; sales, dividends and other events are ignored.
    Purchase fees are included (they are part of each BUY's ``total_cost``).

    Raises ``ValidationError`` if there are no purchases (the average is
    undefined and dividing by zero shares would be a bug, not a 0 result).
    """
    total_cost = _ZERO
    total_shares = _ZERO
    for tx in transactions:
        if tx.transaction_type is TransactionType.BUY:
            total_cost += tx.total_cost
            total_shares += tx.shares
    if total_shares <= _ZERO:
        raise ValidationError("Sem compras registradas para calcular o custo medio.")
    return total_cost / total_shares


def acquisition_cost(transactions: list[Transaction], shares_sold: Decimal) -> Decimal:
    """Acquisition cost of ``shares_sold`` units = average cost * quantity.

    The cost basis to subtract from sale proceeds when computing capital gains.
    Uses the weighted-average price, so a partial sale carries a proportional
    slice of the total cost and leaves the remaining units' average unchanged.
    """
    return average_cost_per_share(transactions) * shares_sold
