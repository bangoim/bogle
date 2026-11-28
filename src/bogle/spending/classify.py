"""What each spending transaction is: the exclusion rules of issue #115.

Pure: lines, bill payments and the user's documents in, a verdict per line out.
The sync stores the verdict (:func:`bogle.spending.repository.write_classifications`)
and recomputes it every run; the user's own choice (``manual_kind``) is never
touched here, and wins over it. Precedence: manual > rule (#116) > these rules.

**Kinds.** Only ``gasto`` and ``abatimento`` count as spending
(:data:`COUNTED`); the rest stay out of every analysis.

**The rules, in order** (evidence: the Nubank data read live on 2026-11-28):

1. *Card.* A ``CREDIT`` with ``operationType=PAGAMENTO_FATURA`` (or Pluggy's
   ``Credit card payment``, ``05100000``) is a bill
   payment, always (matched or not, it is never spending). Any other ``CREDIT``
   on the card (``ESTORNO``, IOF refunded, an anticipation discount) is a
   refund: it carries the category of the purchase and abates it in the month
   it posts, by its own amount, so a partial refund, a refund on a later bill
   and a refund of an installment purchase all abate what the bank credited.
   Every other card line is spending.
2. *Bill payment, checking side.* A debit of the same amount as a card payment
   (the ``payments`` of ``/bills``, and the card's ``PAGAMENTO_FATURA`` lines),
   within :data:`BILL_WINDOW` of it, one to one, the closest first, unless the
   debit names another person (a CPF not the user's) as receiver. At the same
   distance, a debit that names no other party (or is a transfer to Pluggy)
   goes before one to a company, then the earlier. Pluggy sends
   it as ``Transfers``/``OUTROS``, with nothing marking a bill. Several
   payments of one bill match several debits. A card that is not connected has
   nothing to match, so its payment stays spending until the user marks it.
3. *Investment.* ``operationType=RESGATE_APLIC_FINANCEIRA`` (an application or a
   redemption), Nubank's ``Valor recebido de Investimentos`` whatever category
   Pluggy gave it, or a debit under ``Investments`` (``03``). A credit under
   ``Investments`` with neither mark is a dividend, interest on equity or a
   yield: income.
4. *Own transfer.* The other party's document (the receiver of a debit, the
   payer of a credit, in ``paymentData``) is one of the user's: the tax number
   of a connected account. When the line names no other party, Pluggy's
   ``Same person transfer`` (``04000000``) is taken at its word. Else, a debit
   on one connected bank account and a credit of the same amount on another,
   within :data:`TRANSFER_WINDOW`, both transfers to Pluggy and neither naming
   a third party, are the two ends of one transfer. The
   category ``Transfers`` alone excludes nothing: a Pix to someone else (rent,
   a cleaner) is spending.
5. *Refund on the checking account*: ``operationType=ESTORNO``.
6. Anything else: a debit is spending, a credit is income.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from bogle.data.pluggy import local_day

__all__ = [
    "BILL_WINDOW",
    "COUNTED",
    "TRANSFER_WINDOW",
    "BillPayment",
    "Kind",
    "Line",
    "Verdict",
    "classify",
    "digits",
    "parse_bill_payments",
    "spending_value",
]


class Kind(StrEnum):
    """What a transaction is. The values are what is stored and typed on the CLI."""

    SPENDING = "gasto"
    INCOME = "receita"
    OWN_TRANSFER = "transferencia_propria"
    BILL_PAYMENT = "pagamento_fatura"
    INVESTMENT = "investimento"
    REFUND = "abatimento"
    """A refund or reimbursement: negative spending, in the category it came with."""
    IGNORED = "ignorada"
    """Only the user marks a line ignored; no rule does."""


COUNTED = frozenset({Kind.SPENDING, Kind.REFUND})
"""The kinds a spending analysis sums (``spending.expenses``)."""

BILL_WINDOW = timedelta(days=3)
"""How far a checking-account debit may be from the bill payment it pays.

The Nubank debits fell on the very day of the payment in 8 of 8 bills; three
days absorb a weekend or a late posting at another bank without reaching the
next month's payment.
"""

TRANSFER_WINDOW = timedelta(days=3)
"""How far apart the two ends of a transfer between own accounts may post."""

BILL_PAYMENT_OPERATION = "PAGAMENTO_FATURA"
REFUND_OPERATION = "ESTORNO"
INVESTMENT_OPERATION = "RESGATE_APLIC_FINANCEIRA"
INVESTMENT_CATEGORY_PREFIX = "03"
SAME_PERSON_CATEGORY = "04000000"
CARD_PAYMENT_CATEGORY = "05100000"
CPF_DIGITS = 11
TRANSFER_CATEGORY_PREFIXES = ("04", "05")
"""``Same person transfer`` and ``Transfers`` with its children (Pix, TED, third party...)."""
INVESTMENT_DESCRIPTIONS = ("valor recebido de investimentos",)
"""Normalized prefixes of lines that are investments whatever Pluggy calls them
(Nubank sends some redemptions as ``Third party transfers``/``OUTROS``)."""


@dataclass(frozen=True, slots=True)
class Line:
    """A stored transaction, as much of it as the rules read."""

    id: int
    account_id: int
    account_type: str
    """``BANK`` or ``CREDIT``: Pluggy's account type."""
    type: str
    """``DEBIT`` or ``CREDIT``."""
    amount: Decimal
    local_date: date
    description: str
    category_id: str | None = None
    operation_type: str | None = None
    payment_data: object = None
    """``paymentData`` as stored: an object by Pluggy's contract, but read as anything."""
    amount_in_account_currency: Decimal | None = None
    """``amountInAccountCurrency``: a purchase abroad comes in its own currency, with reais here."""


@dataclass(frozen=True, slots=True)
class BillPayment:
    """One entry of a card bill's ``payments`` (``/bills``)."""

    account_id: int
    """The card's stored account."""
    due_date: date
    amount: Decimal
    paid_on: date


@dataclass(frozen=True, slots=True)
class Verdict:
    kind: Kind
    reason: str
    paired_id: int | None = None


def parse_bill_payments(account_id: int, due_date: date, payments: Any) -> list[BillPayment]:
    """The ``payments`` of a stored bill, as Pluggy sent them; an entry without
    a readable amount or date is skipped (it cannot be matched anyway)."""
    if not isinstance(payments, list):
        return []
    parsed: list[BillPayment] = []
    for entry in payments:
        if not isinstance(entry, Mapping):
            continue
        try:
            amount = abs(Decimal(str(entry["amount"])))
            paid_on = local_day(datetime.fromisoformat(str(entry["paymentDate"])))
        except (KeyError, ValueError, InvalidOperation, OverflowError):
            continue
        if not amount.is_finite():
            continue
        parsed.append(BillPayment(account_id, due_date, amount, paid_on))
    return parsed


def spending_value(account_type: str, amount: Decimal, amount_in_account_currency: Decimal | None = None) -> Decimal:
    """Positive for money spent, negative for money back, on any account, in reais.

    Pluggy signs a card's lines from the bank's side (a purchase is positive)
    and a checking account's from the holder's (a debit is negative). A purchase
    abroad has ``amount`` in its own currency and the account's in
    ``amount_in_account_currency``, which counts when there is one.
    """
    value = amount if amount_in_account_currency is None else amount_in_account_currency
    return value if account_type == "CREDIT" else -value


_NOT_DIGIT = re.compile(r"\D")


def digits(value: str | None) -> str:
    """``111.222.333-44`` -> ``11122233344``; the form documents are compared in."""
    return _NOT_DIGIT.sub("", value or "")


def _normalized(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return " ".join("".join(char for char in decomposed if not unicodedata.combining(char)).split())


def _day(value: date) -> str:
    return f"{value:%d/%m/%Y}"


def _counterpart_document(line: Line) -> str:
    """The other party's document: the receiver of a debit, the payer of a credit."""
    data = line.payment_data
    if not isinstance(data, Mapping):
        return ""
    party = data.get("receiver" if line.type == "DEBIT" else "payer")
    if not isinstance(party, Mapping):
        return ""
    document = party.get("documentNumber")
    if not isinstance(document, Mapping):
        return ""
    value = document.get("value")
    return digits(value) if isinstance(value, str) else ""


# ------------------------------------------------------------ casamentos


@dataclass(frozen=True, slots=True)
class _Payment:
    """A bill payment as the matcher sees it: from ``/bills``, the card's line, or both."""

    account_id: int
    amount: Decimal
    paid_on: date
    due_date: date | None
    card_line: Line | None


def _closest_pairs[L, R](
    lefts: Sequence[L], rights: Sequence[R], gap: Callable[[L, R], tuple[int, ...] | None]
) -> list[tuple[L, R]]:
    """One-to-one pairs, the smallest gap first; ``gap`` returns ``None`` for no pair.

    The gap is a sort key: the distance in days, then any tie-break. Full ties
    go to the earlier index on each side, so the result never depends on the
    order of a set.
    """
    options: list[tuple[tuple[int, ...], int, int]] = []
    for i, left in enumerate(lefts):
        for j, right in enumerate(rights):
            distance = gap(left, right)
            if distance is not None:
                options.append((distance, i, j))
    options.sort()
    used_left: set[int] = set()
    used_right: set[int] = set()
    pairs: list[tuple[L, R]] = []
    for _distance, i, j in options:
        if i in used_left or j in used_right:
            continue
        used_left.add(i)
        used_right.add(j)
        pairs.append((lefts[i], rights[j]))
    return pairs


def _payments(card_lines: Sequence[Line], bill_payments: Sequence[BillPayment]) -> list[_Payment]:
    """Every card payment once: ``/bills`` joined to the card's own line where both exist.

    A bill keeps only its recent history in ``/bills``, and a card line may be
    missing from a short sync: either side alone is still a payment.
    """
    ordered = sorted(bill_payments, key=lambda p: (p.paid_on, p.amount, p.account_id))

    def gap(payment: BillPayment, line: Line) -> tuple[int] | None:
        if payment.account_id != line.account_id or abs(line.amount) != payment.amount:
            return None
        days = abs((line.local_date - payment.paid_on).days)
        return (days,) if days <= BILL_WINDOW.days else None

    pairs = _closest_pairs(ordered, card_lines, gap)
    joined = {id(payment): line for payment, line in pairs}
    used = {line.id for _, line in pairs}
    payments = [_Payment(p.account_id, p.amount, p.paid_on, p.due_date, joined.get(id(p))) for p in ordered]
    payments += [
        _Payment(line.account_id, abs(line.amount), line.local_date, None, line)
        for line in card_lines
        if line.id not in used
    ]
    return payments


def _match_bill_payments(
    debits: Sequence[Line], payments: Sequence[_Payment], own: Collection[str]
) -> list[tuple[Line, _Payment]]:
    def names_a_payee(debit: Line) -> int:
        """1 for a debit to a named party (a Pix to a company), 0 for one that looks like a bill payment."""
        document = _counterpart_document(debit)
        if not document or document in own or (debit.category_id or "").startswith(TRANSFER_CATEGORY_PREFIXES):
            return 0
        return 1

    def gap(debit: Line, payment: _Payment) -> tuple[int, int] | None:
        if abs(debit.amount) != payment.amount:
            return None
        days = abs((debit.local_date - payment.paid_on).days)
        return (days, names_a_payee(debit)) if days <= BILL_WINDOW.days else None

    return _closest_pairs(debits, payments, gap)


def _match_transfer_ends(debits: Sequence[Line], credits: Sequence[Line]) -> list[tuple[Line, Line]]:
    """Pairs of the same amount only, so the ends are grouped by amount first:
    each group is matched on its own, which gives the same pairs as matching
    all of them at once, without comparing every debit with every credit."""

    def gap(debit: Line, credit: Line) -> tuple[int] | None:
        if debit.account_id == credit.account_id:
            return None
        days = abs((debit.local_date - credit.local_date).days)
        return (days,) if days <= TRANSFER_WINDOW.days else None

    credits_by_amount: dict[Decimal, list[Line]] = defaultdict(list)
    for credit in credits:
        credits_by_amount[abs(credit.amount)].append(credit)
    debits_by_amount: dict[Decimal, list[Line]] = defaultdict(list)
    for debit in debits:
        debits_by_amount[abs(debit.amount)].append(debit)
    pairs: list[tuple[Line, Line]] = []
    for amount, group in debits_by_amount.items():
        if amount in credits_by_amount:
            pairs += _closest_pairs(group, credits_by_amount[amount], gap)
    return pairs


# ---------------------------------------------------------------- regras


def _names_another_person(line: Line, own: Collection[str]) -> bool:
    document = _counterpart_document(line)
    return len(document) == CPF_DIGITS and document not in own


def _is_investment(line: Line) -> bool:
    if line.operation_type == INVESTMENT_OPERATION or _normalized(line.description).startswith(INVESTMENT_DESCRIPTIONS):
        return True
    # Um credito em Investments sem marca de resgate e provento (dividendo,
    # JCP, rendimento): receita, nao resgate.
    return line.type == "DEBIT" and (line.category_id or "").startswith(INVESTMENT_CATEGORY_PREFIX)


def _is_card_payment(line: Line) -> bool:
    return line.type == "CREDIT" and (
        line.operation_type == BILL_PAYMENT_OPERATION or line.category_id == CARD_PAYMENT_CATEGORY
    )


def _card_verdict(line: Line) -> Verdict:
    if line.type == "CREDIT":
        if _is_card_payment(line):
            return Verdict(Kind.BILL_PAYMENT, "pagamento de fatura no cartão, sem débito correspondente nas contas")
        if line.operation_type == REFUND_OPERATION:
            return Verdict(Kind.REFUND, "estorno no cartão")
        return Verdict(Kind.REFUND, "crédito no cartão")
    return Verdict(Kind.SPENDING, "compra no cartão")


def classify(
    lines: Iterable[Line],
    bill_payments: Iterable[BillPayment] = (),
    own_documents: Collection[str] = (),
) -> dict[int, Verdict]:
    """A verdict for every line, keyed by its id.

    ``own_documents`` are the user's tax numbers, digits only
    (:func:`digits`): the documents of the connected accounts.
    """
    every = sorted(lines, key=lambda line: (line.local_date, line.id))
    own = {doc for doc in own_documents if doc}
    verdicts: dict[int, Verdict] = {}

    card = [line for line in every if line.account_type == "CREDIT"]
    bank = [line for line in every if line.account_type != "CREDIT"]
    for line in card:
        verdicts[line.id] = _card_verdict(line)

    # Fatura: o debito da conta corrente casa com um pagamento do cartao.
    card_payments = [line for line in card if _is_card_payment(line)]
    payments = _payments(card_payments, list(bill_payments))
    # Um debito que nomeia outra pessoa (CPF de terceiro) nunca paga fatura:
    # quem recebe o pagamento de um cartao e o emissor (CNPJ) ou ninguem.
    debits = [line for line in bank if line.type == "DEBIT" and not _names_another_person(line, own)]
    for debit, payment in _match_bill_payments(debits, payments, own):
        due = f" com vencimento em {_day(payment.due_date)}" if payment.due_date is not None else ""
        paired = payment.card_line.id if payment.card_line is not None else None
        verdicts[debit.id] = Verdict(Kind.BILL_PAYMENT, f"pagamento da fatura do cartão{due}", paired)
        if payment.card_line is not None:
            verdicts[payment.card_line.id] = Verdict(
                Kind.BILL_PAYMENT, f"pagamento de fatura pago pela conta em {_day(debit.local_date)}", debit.id
            )

    pending = [line for line in bank if line.id not in verdicts]
    for line in pending:
        if _is_investment(line):
            verdict = Verdict(Kind.INVESTMENT, "aplicação" if line.type == "DEBIT" else "resgate de aplicação")
            verdicts[line.id] = verdict
            continue
        document = _counterpart_document(line)
        party = "favorecido" if line.type == "DEBIT" else "pagador"
        if document and document in own:
            verdicts[line.id] = Verdict(Kind.OWN_TRANSFER, f"o {party} é o próprio titular")
        elif not document and line.category_id == SAME_PERSON_CATEGORY:
            verdicts[line.id] = Verdict(Kind.OWN_TRANSFER, "transferência entre contas do mesmo titular, pela Pluggy")

    # As duas pontas de uma transferencia entre contas conectadas: a Pluggy
    # chama as duas de transferencia, e nenhuma nomeia um terceiro.
    def open_end(line: Line, direction: str) -> bool:
        if line.type != direction or not (line.category_id or "").startswith(TRANSFER_CATEGORY_PREFIXES):
            return False
        verdict = verdicts.get(line.id)
        if verdict is not None and verdict.kind != Kind.OWN_TRANSFER:
            return False
        document = _counterpart_document(line)
        return not document or document in own

    ends_out = [line for line in bank if open_end(line, "DEBIT")]
    ends_in = [line for line in bank if open_end(line, "CREDIT")]
    for debit, credit in _match_transfer_ends(ends_out, ends_in):
        verdicts[debit.id] = Verdict(
            Kind.OWN_TRANSFER, f"transferência para outra conta própria em {_day(credit.local_date)}", credit.id
        )
        verdicts[credit.id] = Verdict(
            Kind.OWN_TRANSFER, f"transferência de outra conta própria em {_day(debit.local_date)}", debit.id
        )

    for line in bank:
        if line.id in verdicts:
            continue
        if line.type == "CREDIT":
            refund = line.operation_type == REFUND_OPERATION
            verdicts[line.id] = (
                Verdict(Kind.REFUND, "estorno na conta") if refund else Verdict(Kind.INCOME, "crédito na conta")
            )
        else:
            verdicts[line.id] = Verdict(Kind.SPENDING, "débito na conta")
    return verdicts
