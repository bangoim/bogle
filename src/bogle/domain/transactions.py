from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class TransactionType(StrEnum):
    """Kinds of portfolio events recorded in ``transactions``.

    BUY/SELL are trades (carry quantity and price); the remaining types
    are income events (carry only the gross amount received).
    """

    BUY = "BUY"
    SELL = "SELL"
    DIVIDEND = "DIVIDEND"
    JCP = "JCP"
    RENDIMENTO = "RENDIMENTO"
    INTEREST = "INTEREST"


@dataclass(frozen=True, slots=True)
class Transaction:
    """A persisted row of ``transactions``.

    ``date`` maps to the ``purchase_date`` column (the historical name
    predates SELL/income support; renaming it is deferred to the
    holdings rework in issue 3.2).

    Field semantics per type:

    - trades: ``total_investment = shares * unit_price``;
      ``total_cost`` = investment + fees for BUY, fees only for SELL.
    - income: gross amount received in ``total_investment``;
      ``shares``/``unit_price``/``fees``/``total_cost`` are zero and
      ``tax_withheld`` holds the income tax retained at source.
    """

    id: int
    ticker: str
    transaction_type: TransactionType
    date: datetime
    shares: Decimal
    unit_price: Decimal
    total_investment: Decimal
    fees: Decimal
    total_cost: Decimal
    tax_withheld: Decimal
