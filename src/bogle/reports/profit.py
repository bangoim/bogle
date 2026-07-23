"""Total profit decomposed: capital gain + income (issue #29).

Capital gain splits into realized (per-sale gains from the sequential
cost-basis replay, #68) and unrealized (market value minus the current average
cost of the units still held). Note ``position.pnl`` is NOT the unrealized gain
— the holdings view nets sale proceeds out of ``total_invested``, so pnl mixes
realized and unrealized (see #29 review).

Income is broken down per type with JCP net of the tax withheld at source.
Capital gains are always since inception (windowed capital needs the patrimony
at the window start); ``--period`` narrows the income window only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from bogle.domain.cost_basis import replay_cost_basis
from bogle.domain.errors import ValidationError
from bogle.domain.transactions import Transaction, TransactionType
from bogle.position import PortfolioSummary
from bogle.reports.dividends import INCOME_TYPES, received_amount

_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class ProfitReport:
    since: date
    realized: Decimal
    unrealized: Decimal
    income_by_type: dict[TransactionType, Decimal]
    income_start: date | None
    """Lower bound of the income window (``None`` = since inception)."""
    unpriced: list[str]
    """Tickers left out of the unrealized gain (no current price)."""

    @property
    def capital_total(self) -> Decimal:
        return self.realized + self.unrealized

    @property
    def income_total(self) -> Decimal:
        return sum(self.income_by_type.values(), _ZERO)

    @property
    def total(self) -> Decimal:
        return self.capital_total + self.income_total


def _as_date(value: date) -> date:
    return value.date() if isinstance(value, datetime) else value


def compute_profit(
    portfolio: PortfolioSummary,
    transactions: list[Transaction],
    *,
    income_start: date | None,
    income_end: date,
) -> ProfitReport:
    if not transactions:
        raise ValidationError("Nenhuma transacao registrada para calcular o lucro.")

    states, sales = replay_cost_basis(transactions)
    realized = sum((sale.gain for sale in sales), _ZERO)

    unrealized = _ZERO
    unpriced: list[str] = []
    for position in portfolio.positions:
        if position.market_value is None:
            unpriced.append(position.ticker)
            continue
        state = states.get(position.ticker)
        if state is None:  # posicao ativa sempre tem BUY (view 004); defensivo
            continue
        unrealized += position.market_value - state.average_cost * position.quantity

    income_by_type: dict[TransactionType, Decimal] = dict.fromkeys(INCOME_TYPES, _ZERO)
    for txn in transactions:
        on = _as_date(txn.date)
        if txn.transaction_type not in INCOME_TYPES or on > income_end:
            continue
        if income_start is not None and on < income_start:
            continue
        income_by_type[txn.transaction_type] += received_amount(txn)

    return ProfitReport(
        since=min(_as_date(t.date) for t in transactions),
        realized=realized,
        unrealized=unrealized,
        income_by_type=income_by_type,
        income_start=income_start,
        unpriced=sorted(unpriced),
    )
