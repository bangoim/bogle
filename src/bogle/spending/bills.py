"""Card bills, installments and what they commit ahead (issue #118).

Everything here is per card account (``CREDIT``). Pluggy's ``/bills`` brings
the bills (``spending.bills``); the bank posts each line to one (``bill_id``),
and the future installments come as lines dated ahead, without one.

The rules:

- **Today** is the day in Sao Paulo; a line dated after it has not posted yet.
- **Closed bill**: a stored bill whose closing date is today or before (one
  without a closing date is taken as closed: ``/bills`` lists the closed ones).
  Its lines are those with its ``bill_id``.
- **States**: ``aberta`` (closing after today); ``paga`` (the ``payments`` of
  ``/bills`` reach the total; a bill of zero or a credit is paid too);
  ``paga parcial`` (something paid, less than the total, before or after the
  due date); ``fechada a vencer`` (nothing paid, due today or later);
  ``vencida`` (nothing paid, due before today). Only ``/bills`` says what was
  paid: the checking-account debit matched by #115 is not attributed to a bill
  (a card payment line posts on the next bill, and which bill a debit paid is
  not in the data).
- **Reconciliation** of a closed bill: Pluggy's total against the sum of its
  lines (the card's sign: a purchase is positive; a refund, a credit or a bill
  payment negative). The difference is explained, as far as the data goes, by
  the bill payments posted on it (they offset the previous balance the total
  carries, or are absent from it), and by each ``financeCharges`` entry (IOF,
  interest, fees). What is left is shown as unexplained, never hidden.
- **Open bill**: the cycle after the last closed bill. Its closing and due
  dates are a stored bill's whose closing is after today, else the card
  account's ``balanceCloseDate``/``balanceDueDate`` when the closing is after
  today, else estimated: the day of the month of the latest known closing (and
  of its due date, the same number of months after it), in the first month
  whose closing falls after today. Its lines: the ones with that stored bill's
  ``bill_id``, and the ones with no known bill (no ``bill_id``, or one ``/bills``
  does not list) dated after the last closing and up to today; one with a
  ``bill_id`` ``/bills`` does not list counts from the closing day itself (the
  bank already posted it to the next bill). When the last
  closing on record is more than :data:`STALE_DAYS` before the estimated
  previous one (``/bills`` lags), the estimate starts the cycle. A card with no
  closing date at all (no bill with one, no ``balanceCloseDate``) has no open
  bill and no projection.
- **Projection**: each installment dated after today, of no closed bill, goes
  to the bill whose closing is the first on or after its date (a stored one, or
  estimated month by month as above), up to :data:`HORIZON_MONTHS` ahead. An
  installment already posted (the same purchase and number, dated up to
  today) is never projected again, and each one is projected once.
- **A purchase in installments** is told apart by the card, the purchase date
  (a line without one takes it from the lines of the same card, number of
  installments and description, when exactly one of their purchase dates
  fits it: the line's month less its number falls in that purchase's month or
  up to :data:`ADOPT_SHIFT_MONTHS` after it, for a first installment on the
  next bill and a pending copy dated a month on; else the month of its first
  installment: the month of a line less its number), the number of installments (up to :data:`MAX_INSTALLMENTS`, with the
  number within it), and the description without its ``3/10`` suffix, compared
  as names are. The total of the purchase is not in it: Pluggy may send it on
  the posted installments and not on the pending ones. Its current installment
  is the highest number dated up to today; what is left is every installment
  after it (the known lines, and the value of the last one for those still
  missing). One whose last installment has posted is paid off and leaves the
  list; so does one with no installment ahead whose last month has passed
  (paid ahead or refunded).
- **Out of range** values from Pluggy are left out, not shown: a bill or cycle
  date with a year outside ``MIN_YEAR`` to ``MAX_YEAR`` (1900 to 2999), an
  installment line dated outside them (it is not read as one), and a
  ``financeCharges`` amount from 10^16 up.
- **Committed**: per month of the due dates, the installments still to post on
  each card, against the average monthly income: the ``receita`` lines of
  every account (whatever ``--account`` chose) in the :data:`INCOME_MONTHS`
  whole months before the running one, each account averaged over the months
  it is compared in (#117's rule: from the month after its first transaction's),
  summed. No income, no share.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

import psycopg
from psycopg.rows import DictRow

from bogle.domain.errors import ValidationError
from bogle.spending import repository
from bogle.spending.analysis import (
    MAX_YEAR,
    MIN_YEAR,
    ZERO,
    CategorySpending,
    Coverage,
    GroupSpending,
    add_months,
    month_end,
    month_label,
    month_of,
    parse_month,
    printable,
    select_accounts,
)
from bogle.spending.classify import Kind, normalized, parse_bill_payments
from bogle.spending.models import CardLine, CategoryGroup, ExpenseLine, IncomeLine, StoredAccount, StoredBill

Connection = psycopg.Connection[DictRow]

INCOME_MONTHS = 3
"""The whole months before the running one the average income takes."""

HORIZON_MONTHS = 120
"""How far ahead an installment is projected: ten years (anything further is left out)."""

STALE_DAYS = 10
"""How far the last closing on record may fall before the estimated previous one (the closing day drifts)."""

NO_CATEGORY = "Sem categoria"

MAX_INSTALLMENTS = 999
"""The most installments a purchase is read with; a line beyond it is not taken as one."""

_MAX_CHARGE = Decimal(10) ** 16
"""A charge from this size up is not read: more than ``NUMERIC(18,2)`` holds."""

_BIG_ID = 2**63 - 1
_MONTH = re.compile(r"^\d{4}-\d{2}$")
# O sufixo da parcela: "Loja 3/10", "Loja - Parcela 3/10" (com hifen, meia-risca ou travessao). O
# padrao procura so o numero no fim do texto; os separadores antes dele saem a parte, sem backtracking.
_INSTALLMENT_SUFFIX = re.compile(r"(?:parcela\s*)?\d{1,3}\s*/\s*\d{1,3}$", re.IGNORECASE)
_SUFFIX_TAIL = 40
"""How far from the end the suffix is looked for: ``Parcela 3/10`` and its spaces fit."""
_SEPARATORS = "-\u2013\u2014"

CHARGE_NAMES = {
    "IOF": "IOF",
    "LATE_PAYMENT_INTEREST": "juros de atraso",
    "LATE_PAYMENT_REMUNERATIVE_INTEREST": "juros remuneratórios",
    "LATE_PAYMENT_FEE": "multa por atraso",
    "OTHER": "outros encargos",
}
"""Pluggy's ``financeCharges`` types, as shown."""

PAYMENTS_POSTED = "pagamentos lançados (saldo anterior)"


class BillState(StrEnum):
    OPEN = "aberta"
    DUE = "fechada a vencer"
    PAID = "paga"
    PARTIAL = "paga parcial"
    OVERDUE = "vencida"
    FORECAST = "prevista"


# ------------------------------------------------------------- datas


def _on_day(month: date, day: int) -> date:
    """``day`` of ``month``, held to the month's end."""
    return month.replace(day=min(day, month_end(month).day))


def _months_between(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + end.month - start.month


def _in_range(day: date | None) -> bool:
    """Whether a date from Pluggy is usable: none, or a year from ``MIN_YEAR`` to ``MAX_YEAR``."""
    return day is None or MIN_YEAR <= day.year <= MAX_YEAR


@dataclass(frozen=True, slots=True)
class Cycle:
    """A bill's closing and due dates; ``estimated`` when neither Pluggy nor the bank gave them."""

    closing: date
    due: date
    estimated: bool = False

    def following(self, months: int = 1) -> Cycle:
        """The cycle ``months`` later, on the same days of the month."""
        offset = _months_between(self.closing, self.due)
        closing_month = add_months(month_of(self.closing), months)
        return Cycle(
            _on_day(closing_month, self.closing.day),
            _on_day(add_months(closing_month, offset), self.due.day),
            estimated=True,
        )


# ------------------------------------------------------------ faturas


@dataclass(frozen=True, slots=True)
class Charge:
    """Part of a bill's difference: what explains it, and how much."""

    name: str
    amount: Decimal


@dataclass(frozen=True, slots=True)
class Reconciliation:
    total: Decimal
    """Pluggy's ``totalAmount``."""
    lines: Decimal
    """The sum of the bill's lines, the card's sign."""
    count: int
    explained: tuple[Charge, ...] = ()

    @property
    def difference(self) -> Decimal:
        return self.total - self.lines

    @property
    def unexplained(self) -> Decimal:
        return self.difference - sum((charge.amount for charge in self.explained), ZERO)


@dataclass(frozen=True, slots=True)
class Bill:
    account_id: int
    card: str
    """How the card is named on screen."""
    id: int | None
    """The stored bill's local id; ``None`` for one ``/bills`` has not listed."""
    cycle: Cycle | None
    """``None`` only for a stored bill without a closing date."""
    due: date
    state: BillState
    total: Decimal
    """Pluggy's total for a closed bill; the purchases so far for the open one; the installments for a forecast."""
    lines: tuple[CardLine, ...] = ()
    """What posted on it, up to today."""
    upcoming: tuple[CardLine, ...] = ()
    """The installments still to post on it."""
    paid: Decimal | None = None
    paid_on: date | None = None
    """The last payment's day."""
    reconciliation: Reconciliation | None = None

    @property
    def month(self) -> date:
        """The month it is due: how a bill is named."""
        return month_of(self.due)

    @property
    def closing(self) -> date | None:
        return self.cycle.closing if self.cycle is not None else None

    @property
    def estimated(self) -> bool:
        return self.cycle is not None and self.cycle.estimated

    @property
    def purchases(self) -> int:
        return sum(1 for line in self.lines if line.amount > 0 and line.kind != Kind.BILL_PAYMENT)

    @property
    def to_post(self) -> Decimal:
        """The installments still to post."""
        return sum((line.amount for line in self.upcoming), ZERO)


def bill_state(total: Decimal, paid: Decimal, closing: date | None, due: date, today: date) -> BillState:
    """See the module's rules."""
    if closing is not None and closing > today:
        return BillState.OPEN
    if paid >= total:
        return BillState.PAID
    if paid > 0:
        return BillState.PARTIAL
    return BillState.DUE if due >= today else BillState.OVERDUE


def finance_charges(raw: Any) -> list[Charge]:
    """The ``financeCharges`` of a stored bill; an entry without a readable amount is skipped."""
    if not isinstance(raw, list):
        return []
    charges: list[Charge] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        try:
            amount = Decimal(str(entry["amount"]))
        except (KeyError, InvalidOperation, ValueError):
            continue
        if not amount.is_finite() or amount == 0 or amount.copy_abs() >= _MAX_CHARGE:
            continue
        kind = str(entry.get("type") or "OTHER")
        name = CHARGE_NAMES.get(kind.upper(), printable(kind).lower() or CHARGE_NAMES["OTHER"])
        charges.append(Charge(name, amount))
    return charges


def reconcile(bill: StoredBill, lines: Sequence[CardLine]) -> Reconciliation:
    """Pluggy's total against the bill's lines, with what explains the difference (see the module's rules)."""
    explained: list[Charge] = []
    posted_payments = -sum((line.amount for line in lines if line.kind == Kind.BILL_PAYMENT), ZERO)
    if posted_payments:
        explained.append(Charge(PAYMENTS_POSTED, posted_payments))
    explained += finance_charges(bill.finance_charges)
    return Reconciliation(
        total=bill.total_amount,
        lines=sum((line.amount for line in lines), ZERO),
        count=len(lines),
        explained=tuple(explained),
    )


# ----------------------------------------------------------- parcelas


def base_description(text: str) -> str:
    """The description without its installment suffix: ``Loja 3/10`` -> ``Loja``."""
    trimmed = text.rstrip()
    found = _INSTALLMENT_SUFFIX.search(trimmed, max(len(trimmed) - _SUFFIX_TAIL, 0))
    if found is None:
        return text.strip()
    end = found.start()
    while end and (trimmed[end - 1].isspace() or trimmed[end - 1] in _SEPARATORS):
        end -= 1
    return trimmed[:end].strip() or text.strip()


type PurchaseKey = tuple[int, date, int, str]
type _Purchase = tuple[int, int, str]
"""A purchase without its date: the card, the number of installments, the description."""

ADOPT_SHIFT_MONTHS = 2
"""How many months after the purchase's an undated installment may fall and still take its date (see the rules)."""


def _purchase(line: CardLine) -> tuple[_Purchase, int] | None:
    """The purchase and the installment's number; ``None`` for a line not in installments, or dated out of range."""
    number, total = line.installment_number, line.total_installments
    if number is None or total is None or not 1 <= number <= total <= MAX_INSTALLMENTS:
        return None
    if not _in_range(line.local_date):
        return None  # a conta de mes com essa data estouraria a faixa de date
    return (line.account_id, total, normalized(base_description(line.description))), number


def purchase_dates(lines: Iterable[CardLine]) -> dict[_Purchase, frozenset[date]]:
    """The purchase dates the lines of each purchase carry: where an undated line looks for its own."""
    found: dict[_Purchase, set[date]] = defaultdict(set)
    for line in lines:
        purchase = _purchase(line)
        if purchase is not None and line.purchase_date is not None:
            found[purchase[0]].add(line.purchase_date)
    return {purchase: frozenset(dates) for purchase, dates in found.items()}


def _adopted(line: CardLine, number: int, dated: Iterable[date]) -> date | None:
    """The only purchase date whose month, plus ``number - 1``, falls up to the shift before the line's month."""
    fits = [day for day in dated if 0 <= _months_between(day, line.local_date) - (number - 1) <= ADOPT_SHIFT_MONTHS]
    return fits[0] if len(fits) == 1 else None


def purchase_key(line: CardLine, dates: Mapping[_Purchase, frozenset[date]] | None = None) -> PurchaseKey | None:
    """What tells a purchase in installments apart (see the module's rules); ``None`` for a line not in installments.

    ``dates`` (from :func:`purchase_dates`) gives a line without a purchase date the one of its purchase.
    """
    found = _purchase(line)
    if found is None:
        return None
    purchase, number = found
    anchor = (
        line.purchase_date
        or _adopted(line, number, (dates or {}).get(purchase, ()))
        or add_months(month_of(line.local_date), -(number - 1))
    )
    account_id, total, description = purchase
    return (account_id, anchor, total, description)


def _installment_id(line: CardLine, dates: Mapping[_Purchase, frozenset[date]]) -> tuple[PurchaseKey, int] | None:
    key = purchase_key(line, dates)
    return None if key is None or line.installment_number is None else (key, line.installment_number)


@dataclass(frozen=True, slots=True)
class InstallmentPlan:
    """A purchase in installments still running."""

    account_id: int
    card: str
    description: str
    purchase_date: date | None
    current: int
    """The highest installment posted up to today; 0 before the first."""
    total: int
    value: Decimal
    """The last installment posted (else the next one)."""
    remaining: Decimal
    last_month: date
    """The month the last installment posts."""

    @property
    def label(self) -> str:
        return f"{self.current}/{self.total}"


def installment_plans(
    lines: Iterable[CardLine], *, today: date, cards: Mapping[int, str] | None = None
) -> tuple[InstallmentPlan, ...]:
    """The purchases in installments still running, the largest remainder first."""
    purchases: dict[PurchaseKey, dict[int, CardLine]] = defaultdict(dict)
    ordered = sorted(lines, key=lambda line: (line.local_date, line.id))
    dates = purchase_dates(ordered)
    for line in ordered:
        found = _installment_id(line, dates)
        if found is None or line.amount <= 0:
            continue
        key, number = found
        if number > key[2]:
            continue
        known = purchases[key]
        # A mesma parcela duas vezes: vale a ja lancada (a mais antiga), a outra e copia.
        if number not in known:
            known[number] = line
    plans: list[InstallmentPlan] = []
    for key, known in purchases.items():
        total = key[2]
        posted = [number for number, line in known.items() if line.local_date <= today]
        current = max(posted, default=0)
        if current >= total:
            continue  # quitada: a ultima parcela ja foi lancada
        ahead = {number: line for number, line in known.items() if number > current}
        reference = known[current] if current else ahead[min(ahead)]
        missing = (total - current) - len(ahead)
        remaining = sum((line.amount for line in ahead.values()), ZERO) + reference.amount * missing
        if total in known:
            last_month = month_of(known[total].local_date)
        else:
            number = reference.installment_number or current
            last_month = add_months(month_of(reference.local_date), total - number)
        if not ahead and last_month < month_of(today):
            continue  # parou de receber parcelas (antecipada ou estornada) e o fim ja passou
        latest = max(known.values(), key=lambda line: (line.local_date <= today, line.local_date, line.id))
        plans.append(
            InstallmentPlan(
                account_id=key[0],
                card=(cards or {}).get(key[0], ""),
                description=base_description(latest.description),
                purchase_date=reference.purchase_date,
                current=current,
                total=total,
                value=reference.amount,
                remaining=remaining,
                last_month=last_month,
            )
        )
    plans.sort(key=lambda plan: (-plan.remaining, normalized(plan.description), plan.account_id))
    return tuple(plans)


# ---------------------------------------------------------- um cartao


@dataclass(frozen=True, slots=True)
class CardBills:
    account_id: int
    card: str
    closed: tuple[Bill, ...]
    """Newest first."""
    open: Bill | None
    """``None`` when no closing date is known."""
    forecast: tuple[Bill, ...] = ()
    """The bills after the open one, up to the last installment."""

    @property
    def ahead(self) -> tuple[Bill, ...]:
        """The open bill and the ones after it: where installments still post."""
        return ((self.open,) if self.open is not None else ()) + self.forecast


def _paid(bill: StoredBill) -> tuple[Decimal, date | None]:
    payments = parse_bill_payments(bill.account_id, bill.due_date, bill.payments)
    return sum((payment.amount for payment in payments), ZERO), max(
        (payment.paid_on for payment in payments), default=None
    )


def card_bills(
    account: StoredAccount,
    lines: Sequence[CardLine],
    bills: Sequence[StoredBill],
    *,
    today: date,
    cycle_dates: tuple[date | None, date | None] = (None, None),
) -> CardBills:
    """The bills of one card: closed, open and forecast (see the module's rules)."""
    lines = sorted(
        (line for line in lines if line.account_id == account.id), key=lambda line: (line.local_date, line.id)
    )
    # Data fora de 1900-2999 vinda da Pluggy nao vira ciclo: as contas de mes estourariam a faixa de date.
    own = [
        bill
        for bill in bills
        if bill.account_id == account.id and _in_range(bill.due_date) and _in_range(bill.closing_date)
    ]
    by_pluggy = {bill.pluggy_id: bill for bill in own}

    def closed(bill: StoredBill) -> bool:
        return bill.closing_date is None or bill.closing_date <= today

    shut = sorted(
        (bill for bill in own if closed(bill)), key=lambda bill: (bill.closing_date or bill.due_date, bill.id)
    )
    later = sorted((bill for bill in own if not closed(bill)), key=lambda bill: (bill.closing_date, bill.id))
    closed_bills: list[Bill] = []
    for bill in reversed(shut):
        mine = tuple(line for line in lines if line.bill_id == bill.pluggy_id)
        paid, paid_on = _paid(bill)
        closed_bills.append(
            Bill(
                account_id=account.id,
                card=account.label,
                id=bill.id,
                cycle=Cycle(bill.closing_date, bill.due_date) if bill.closing_date is not None else None,
                due=bill.due_date,
                state=bill_state(bill.total_amount, paid, bill.closing_date, bill.due_date, today),
                total=bill.total_amount,
                lines=mine,
                paid=paid,
                paid_on=paid_on,
                reconciliation=reconcile(bill, mine),
            )
        )

    known = [Cycle(bill.closing_date, bill.due_date) for bill in own if bill.closing_date is not None]
    close_date, due_date = cycle_dates
    if not (_in_range(close_date) and _in_range(due_date)):
        close_date = due_date = None
    if close_date is not None and due_date is not None:
        known.append(Cycle(close_date, due_date))
    if not known:
        return CardBills(account.id, account.label, tuple(closed_bills), None)
    reference = max(known, key=lambda cycle: cycle.closing)

    # O ciclo aberto: uma fatura gravada que ainda nao fechou, senao a data da conta, senao a estimativa.
    open_bill = later[0] if later else None
    if open_bill is not None and open_bill.closing_date is not None:
        current = Cycle(open_bill.closing_date, open_bill.due_date)
    elif close_date is not None and due_date is not None and close_date > today:
        current = Cycle(close_date, due_date)
    else:
        step = max(_months_between(reference.closing, today), 0)
        current = reference.following(step)
        while current.closing <= today:
            step += 1
            current = reference.following(step)
    previous = reference.following(_months_between(reference.closing, current.closing) - 1)
    last_closed = max((bill.closing_date for bill in shut if bill.closing_date is not None), default=None)
    start = previous.closing
    if last_closed is not None and last_closed >= previous.closing - timedelta(days=STALE_DAYS):
        start = last_closed

    def unbilled(line: CardLine) -> bool:
        return line.bill_id is None or line.bill_id not in by_pluggy

    def stored_ahead(line: CardLine) -> StoredBill | None:
        bill = by_pluggy.get(line.bill_id) if line.bill_id is not None else None
        return bill if bill is not None and not closed(bill) else None

    def after_start(line: CardLine) -> bool:
        # No dia do fechamento, a linha ja posta numa fatura que /bills nao lista e da seguinte.
        return line.local_date > start or (line.local_date == start and line.bill_id is not None)

    open_pluggy = open_bill.pluggy_id if open_bill is not None else None
    now_lines = tuple(
        line
        for line in lines
        if line.local_date <= today
        and ((open_pluggy is not None and line.bill_id == open_pluggy) or (unbilled(line) and after_start(line)))
    )

    # As parcelas a lancar: nenhuma ja lancada (mesma compra e numero ate hoje) e cada uma uma vez so.
    dates = purchase_dates(lines)
    seen = {found for line in lines if line.local_date <= today and (found := _installment_id(line, dates)) is not None}
    horizon = add_months(month_of(today), HORIZON_MONTHS)
    cycles: list[Cycle] = [current]
    stored_cycles: dict[date, StoredBill] = {current.closing: open_bill} if open_bill is not None else {}
    for bill in later[1:]:
        assert bill.closing_date is not None  # so as fechadas podem nao ter fechamento
        if bill.closing_date > cycles[-1].closing:
            cycles.append(Cycle(bill.closing_date, bill.due_date))
            stored_cycles[bill.closing_date] = bill
    upcoming: dict[date, list[CardLine]] = defaultdict(list)
    for line in lines:
        found = _installment_id(line, dates)
        if line.local_date <= today or found is None or found in seen or line.local_date >= horizon:
            continue
        target = stored_ahead(line)
        if target is None and not unbilled(line):
            continue  # de uma fatura ja fechada
        seen.add(found)
        if target is not None and target.closing_date is not None:
            upcoming[target.closing_date].append(line)
            continue
        while cycles[-1].closing < line.local_date:
            months = _months_between(reference.closing, cycles[-1].closing) + 1
            cycles.append(reference.following(months))
        closing = next(cycle.closing for cycle in cycles if cycle.closing >= line.local_date)
        upcoming[closing].append(line)

    def stored_id(cycle: Cycle) -> int | None:
        bill = stored_cycles.get(cycle.closing)
        return bill.id if bill is not None else None

    opened = Bill(
        account_id=account.id,
        card=account.label,
        id=stored_id(current),
        cycle=current,
        due=current.due,
        state=BillState.OPEN,
        total=sum((line.amount for line in now_lines if line.kind != Kind.BILL_PAYMENT), ZERO),
        lines=now_lines,
        upcoming=tuple(upcoming.get(current.closing, ())),
    )
    forecast = tuple(
        Bill(
            account_id=account.id,
            card=account.label,
            id=stored_id(cycle),
            cycle=cycle,
            due=cycle.due,
            state=BillState.FORECAST,
            total=sum((line.amount for line in upcoming.get(cycle.closing, ())), ZERO),
            upcoming=tuple(upcoming.get(cycle.closing, ())),
        )
        for cycle in cycles[1:]
    )
    return CardBills(account.id, account.label, tuple(closed_bills), opened, forecast)


# ------------------------------------------------------- comprometido


@dataclass(frozen=True, slots=True)
class Commitment:
    """A month of due dates and the installments still to post on each card's bill."""

    month: date
    amounts: tuple[Decimal, ...]
    """One per card, in the order of :attr:`BillsOverview.cards`."""

    @property
    def total(self) -> Decimal:
        return sum(self.amounts, ZERO)


def commitments(cards: Sequence[CardBills]) -> tuple[Commitment, ...]:
    """From the first month with an installment to post to the last, every month in between."""
    by_month: dict[date, list[Decimal]] = defaultdict(lambda: [ZERO] * len(cards))
    for index, card in enumerate(cards):
        for bill in card.ahead:
            if bill.upcoming:
                by_month[bill.month][index] += bill.to_post
    if not by_month:
        return ()
    first, last = min(by_month), max(by_month)
    months = [add_months(first, step) for step in range(_months_between(first, last) + 1)]
    return tuple(Commitment(month, tuple(by_month.get(month, [ZERO] * len(cards)))) for month in months)


def average_income(
    incomes: Iterable[IncomeLine], coverage: Coverage, *, today: date, months: int = INCOME_MONTHS
) -> Decimal | None:
    """The average monthly income of the ``months`` whole months before today's (see the module's rules)."""
    window = [add_months(month_of(today), -back) for back in range(months, 0, -1)]
    chosen = list(incomes)
    total = ZERO
    based = False
    for account_id in coverage.starts:
        own = {month for month in window if coverage.compares(account_id, month)}
        if not own:
            continue
        based = True
        value = sum(
            (line.value for line in chosen if line.account_id == account_id and month_of(line.local_date) in own),
            ZERO,
        )
        total += value / len(own)
    return total if based and total > 0 else None


@dataclass(frozen=True, slots=True)
class BillsOverview:
    cards: tuple[CardBills, ...]
    committed: tuple[Commitment, ...]
    income: Decimal | None
    """The average monthly income; ``None`` when there is none to compare with."""
    today: date

    def share(self, entry: Commitment) -> Decimal | None:
        return entry.total / self.income if self.income else None


# ------------------------------------------------------------ detalhe


@dataclass(frozen=True, slots=True)
class BillDetail:
    bill: Bill
    groups: tuple[GroupSpending, ...]
    """The spending by group and category: of what posted (of the installments, for a forecast)."""
    today: date
    installments: tuple[CardLine, ...] = ()
    """The installments on it, posted and to post."""


def group_totals(lines: Iterable[ExpenseLine]) -> tuple[GroupSpending, ...]:
    """Spending by group and category, the largest first (no comparison: a bill is not a month)."""
    groups: dict[int, CategoryGroup] = {}
    by_group: dict[int, Decimal] = defaultdict(Decimal)
    by_category: dict[tuple[int, str | None], Decimal] = defaultdict(Decimal)
    names: dict[tuple[int, str | None], str] = {}
    for line in lines:
        groups[line.group.id] = line.group
        by_group[line.group.id] += line.value
        key = (line.group.id, line.category_id)
        by_category[key] += line.value
        names[key] = line.category or NO_CATEGORY
    result = [
        GroupSpending(
            group=group,
            value=by_group[group_id],
            average=None,
            categories=tuple(
                sorted(
                    (
                        CategorySpending(category_id, names[(owner, category_id)], value, None)
                        for (owner, category_id), value in by_category.items()
                        if owner == group_id
                    ),
                    key=lambda entry: (-entry.value, normalized(entry.name)),
                )
            ),
        )
        for group_id, group in groups.items()
    ]
    result.sort(key=lambda entry: (-entry.value, normalized(entry.group.name)))
    return tuple(result)


def parse_bill_ref(text: str) -> date | int:
    """``2026-12`` (the month a bill is due) or a stored bill's id."""
    wanted = text.strip()
    if wanted.isdigit() and wanted.isascii():
        number = int(wanted)
        if number > _BIG_ID:
            raise ValidationError(f"Fatura {wanted} não encontrada.")
        return number
    if _MONTH.match(wanted):
        return parse_month(wanted)
    raise ValidationError(f"Fatura '{text}' inválida. Use o mês de vencimento (AAAA-MM, como 2026-12) ou o id.")


def find_bill(overview: BillsOverview, ref: date | int) -> Bill:
    """The bill ``ref`` names: by id, or by the month it is due (closed, open or forecast)."""
    every = [bill for card in overview.cards for bill in (*card.closed, *card.ahead)]
    if isinstance(ref, int):
        found = [bill for bill in every if bill.id == ref]
        if not found:
            raise ValidationError(f"Fatura {ref} não encontrada.")
        return found[0]
    found = [bill for bill in every if bill.month == ref]
    if not found:
        raise ValidationError(f"Nenhuma fatura vence em {month_label(ref)}.")
    cards = list(dict.fromkeys(bill.account_id for bill in found))
    if len(cards) > 1:
        names = ", ".join(dict.fromkeys(printable(bill.card) for bill in found))
        raise ValidationError(
            f"Mais de um cartão tem fatura em {month_label(ref)} ({names}). Escolha um com --account."
        )
    if len(found) > 1:
        ids = ", ".join(str(bill.id) for bill in found if bill.id is not None)
        raise ValidationError(f"Mais de uma fatura vence em {month_label(ref)}. Use o id: {ids}.")
    return found[0]


# ------------------------------------------------------------- do banco


def _cards(conn: Connection, account: str | None) -> list[StoredAccount]:
    chosen = select_accounts(repository.accounts(conn), account)
    cards = [entry for entry in chosen if entry.type == "CREDIT"]
    if not cards:
        if account is not None:
            raise ValidationError(f"'{account}' não é um cartão: faturas e parcelas só existem no cartão.")
        raise ValidationError("Nenhum cartão de crédito entre as contas de gastos.")
    return cards


def bills_overview(conn: Connection, *, today: date, account: str | None = None) -> BillsOverview:
    """``bogle spending bills``: every chosen card's bills, and what installments commit ahead."""
    cards = _cards(conn, account)
    ids = [card.id for card in cards]
    lines = repository.card_lines(conn, ids)
    stored = repository.stored_bills(conn, ids)
    dates = repository.card_cycle_dates(conn, ids)
    built = tuple(
        card_bills(card, lines, stored, today=today, cycle_dates=dates.get(card.id, (None, None))) for card in cards
    )
    every = repository.accounts(conn)
    since = add_months(month_of(today), -INCOME_MONTHS)
    incomes = repository.income_lines(conn, [entry.id for entry in every], since, month_of(today) - timedelta(days=1))
    return BillsOverview(
        cards=built,
        committed=commitments(built),
        income=average_income(incomes, Coverage.of(every), today=today),
        today=today,
    )


def bill_detail(conn: Connection, ref: date | int, *, today: date, account: str | None = None) -> BillDetail:
    """``bogle spending bills <fatura>``: one bill by group and category, its installments and reconciliation."""
    overview = bills_overview(conn, today=today, account=account)
    bill = find_bill(overview, ref)
    counted = bill.lines if bill.state != BillState.FORECAST else bill.upcoming
    groups = group_totals(repository.expense_lines_of(conn, [line.id for line in counted])) if counted else ()
    installments = tuple(line for line in (*bill.lines, *bill.upcoming) if purchase_key(line) is not None)
    return BillDetail(bill=bill, groups=groups, installments=installments, today=today)


def installments(conn: Connection, *, today: date, account: str | None = None) -> tuple[InstallmentPlan, ...]:
    """``bogle spending installments``: the purchases in installments still running."""
    cards = _cards(conn, account)
    lines = repository.card_lines(conn, [card.id for card in cards])
    return installment_plans(lines, today=today, cards={card.id: card.label for card in cards})
