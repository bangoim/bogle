"""Tests for the exclusion rules of issue #115 (:mod:`bogle.spending.classify`), on plain values.

Covers the criteria of the issue and of its benchmark comment: the bill payments
are matched and excluded on both sides, a same-amount debit outside the window
is not, a bill paid in two parts matches both debits, a Pix to someone else is
spending, a transfer to an own account and an investment are neither spending
nor income, and refunds (full, partial, on a later bill, of an installment
purchase) abate what the bank credited. The amounts and dates of the eight bills
are made up; their shape (same day, same amount, ``Transfers``/``OUTROS`` on the
checking side) is the one read live from Nubank on 2026-11-28.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from itertools import count
from typing import Any

import pytest

from bogle.spending.classify import (
    BILL_WINDOW,
    COUNTED,
    BillPayment,
    Kind,
    Line,
    Verdict,
    classify,
    digits,
    parse_bill_payments,
    spending_value,
)

CARD = 1
BANK = 2
OTHER_BANK = 3
ME = "000.000.000-00"
SOMEONE = "111.222.333-44"
SHOP = "00.000.000/0001-00"
OWN = {digits(ME)}

TRANSFERS = "05000000"
SAME_PERSON = "04000000"
CARD_PAYMENT = "05100000"
SHOPPING = "08000000"
INVESTMENTS = "03000000"

_ids = count(1)


def party(document: str | None, kind: str = "CPF") -> dict[str, Any]:
    return {"documentNumber": {"type": kind, "value": document} if document else None, "name": None}


def pix(receiver: str | None = None, payer: str | None = ME) -> dict[str, Any]:
    return {"payer": party(payer), "receiver": party(receiver), "paymentMethod": "PIX"}


def line(
    account: int,
    day: date,
    amount: str,
    *,
    kind: str | None = None,
    category: str | None = SHOPPING,
    operation: str | None = None,
    description: str = "Compra",
    payment_data: object = None,
    in_account: str | None = None,
) -> Line:
    """A line with Pluggy's signs: on the card a purchase is positive, on the bank a debit is negative.

    ``in_account`` is ``amountInAccountCurrency``: a purchase abroad comes in its
    own currency, with the value in reais there.
    """
    value = Decimal(amount)
    if kind is None:
        kind = ("DEBIT" if value > 0 else "CREDIT") if account == CARD else ("DEBIT" if value < 0 else "CREDIT")
    return Line(
        id=next(_ids),
        account_id=account,
        account_type="CREDIT" if account == CARD else "BANK",
        type=kind,
        amount=value,
        local_date=day,
        description=description,
        category_id=category,
        operation_type=operation,
        payment_data=payment_data,
        amount_in_account_currency=Decimal(in_account) if in_account is not None else None,
    )


def card_payment(day: date, amount: str) -> Line:
    """The card side of a bill payment: a CREDIT, negative, ``PAGAMENTO_FATURA``."""
    return line(CARD, day, f"-{amount}", category=CARD_PAYMENT, operation="PAGAMENTO_FATURA", description="Pagamento")


def bank_bill_debit(day: date, amount: str) -> Line:
    """The checking side: a ``Transfers``/``OUTROS`` debit, nothing marking a bill."""
    return line(BANK, day, f"-{amount}", category=TRANSFERS, operation="OUTROS", description="Pagamento de fatura")


def paid(day: date, amount: str, due: date | None = None) -> BillPayment:
    return BillPayment(CARD, due or day + timedelta(days=1), Decimal(amount), day)


def kinds(verdicts: dict[int, Verdict], *lines: Line) -> list[Kind]:
    return [verdicts[entry.id].kind for entry in lines]


def totals(lines: list[Line], verdicts: dict[int, Verdict]) -> dict[str | None, Decimal]:
    """What an analysis sums per category: the counted kinds, signed as spending."""
    result: dict[str | None, Decimal] = defaultdict(Decimal)
    for entry in lines:
        if verdicts[entry.id].kind in COUNTED:
            result[entry.category_id] += spending_value(
                entry.account_type, entry.amount, entry.amount_in_account_currency
            )
    return dict(result)


# ------------------------------------------------------------------ fatura


NUBANK_BILLS = [
    (date(2026, 4, 6), "1100.10"),
    (date(2026, 5, 5), "1200.20"),
    (date(2026, 6, 9), "1300.30"),
    (date(2026, 7, 6), "1400.40"),
    (date(2026, 8, 11), "500.50"),
    (date(2026, 9, 9), "1600.60"),
    (date(2026, 10, 6), "1700.70"),
    (date(2026, 11, 10), "1800.80"),
]


class TestBillPayment:
    def test_the_eight_payments_are_matched_and_excluded_on_both_sides(self) -> None:
        card_side = [card_payment(day, amount) for day, amount in NUBANK_BILLS]
        bank_side = [bank_bill_debit(day, amount) for day, amount in NUBANK_BILLS]
        payments = [paid(day, amount) for day, amount in NUBANK_BILLS]
        verdicts = classify([*card_side, *bank_side], payments, OWN)
        for card, bank in zip(card_side, bank_side, strict=True):
            assert verdicts[card.id].kind == Kind.BILL_PAYMENT
            assert verdicts[bank.id].kind == Kind.BILL_PAYMENT
            assert verdicts[bank.id].paired_id == card.id
            assert verdicts[card.id].paired_id == bank.id
        assert totals([*card_side, *bank_side], verdicts) == {}

    def test_the_reason_names_the_due_date(self) -> None:
        bank = bank_bill_debit(date(2026, 11, 10), "1800.80")
        verdicts = classify([bank], [paid(date(2026, 11, 10), "1800.80", date(2026, 11, 10))], OWN)
        assert verdicts[bank.id].reason == "pagamento da fatura do cartão com vencimento em 10/11/2026"

    def test_a_same_amount_debit_outside_the_window_is_not_matched(self) -> None:
        day = date(2026, 11, 10)
        far = bank_bill_debit(day + BILL_WINDOW + timedelta(days=1), "1800.80")
        before = bank_bill_debit(day - BILL_WINDOW - timedelta(days=1), "1800.80")
        verdicts = classify([far, before, card_payment(day, "1800.80")], [paid(day, "1800.80")], OWN)
        assert kinds(verdicts, far, before) == [Kind.SPENDING, Kind.SPENDING]

    def test_inside_the_window_it_is(self) -> None:
        day = date(2026, 11, 10)
        late = bank_bill_debit(day + BILL_WINDOW, "1800.80")
        assert classify([late], [paid(day, "1800.80")], OWN)[late.id].kind == Kind.BILL_PAYMENT

    def test_a_different_amount_is_not_matched(self) -> None:
        day = date(2026, 11, 10)
        other = bank_bill_debit(day, "1800.81")
        assert classify([other], [paid(day, "1800.80")], OWN)[other.id].kind == Kind.SPENDING

    def test_one_payment_matches_one_debit_the_closest(self) -> None:
        day = date(2026, 11, 10)
        next_day = bank_bill_debit(day + timedelta(days=1), "300")
        same_day = bank_bill_debit(day, "300")
        verdicts = classify([next_day, same_day], [paid(day, "300")], OWN)
        assert kinds(verdicts, same_day, next_day) == [Kind.BILL_PAYMENT, Kind.SPENDING]

    def test_a_bill_paid_in_two_parts_matches_both_debits(self) -> None:
        due = date(2026, 11, 10)
        first, second = bank_bill_debit(date(2026, 11, 3), "1000"), bank_bill_debit(due, "800.80")
        payments = [paid(date(2026, 11, 3), "1000", due), paid(due, "800.80", due)]
        verdicts = classify(
            [first, second, card_payment(date(2026, 11, 3), "1000"), card_payment(due, "800.80")], payments, OWN
        )
        assert kinds(verdicts, first, second) == [Kind.BILL_PAYMENT, Kind.BILL_PAYMENT]

    def test_a_payment_before_closing_is_matched_like_any_other(self) -> None:
        early = bank_bill_debit(date(2026, 10, 20), "500")
        payments = [paid(date(2026, 10, 20), "500", date(2026, 11, 10))]
        assert classify([early], payments, OWN)[early.id].kind == Kind.BILL_PAYMENT

    def test_the_card_line_alone_matches_when_bills_lack_the_payment(self) -> None:
        card, bank = card_payment(date(2026, 3, 9), "800"), bank_bill_debit(date(2026, 3, 9), "800")
        verdicts = classify([card, bank], [], OWN)
        assert verdicts[bank.id] == Verdict(Kind.BILL_PAYMENT, "pagamento da fatura do cartão", card.id)

    def test_bills_alone_match_when_the_card_line_is_missing(self) -> None:
        bank = bank_bill_debit(date(2026, 3, 9), "800")
        verdicts = classify([bank], [paid(date(2026, 3, 9), "800")], OWN)
        assert verdicts[bank.id].kind == Kind.BILL_PAYMENT
        assert verdicts[bank.id].paired_id is None

    def test_the_card_side_is_never_spending_even_unmatched(self) -> None:
        card = card_payment(date(2026, 3, 9), "800")
        assert classify([card], [], OWN)[card.id].kind == Kind.BILL_PAYMENT

    def test_a_payment_and_its_card_line_count_once(self) -> None:
        day = date(2026, 11, 10)
        one, two = bank_bill_debit(day, "100"), bank_bill_debit(day, "100")
        verdicts = classify([one, two, card_payment(day, "100")], [paid(day, "100")], OWN)
        assert sorted(kinds(verdicts, one, two)) == sorted([Kind.BILL_PAYMENT, Kind.SPENDING])

    def test_a_pix_to_another_person_is_never_a_bill_payment(self) -> None:
        day = date(2026, 11, 10)
        friend = line(BANK, day, "-1800.80", category=TRANSFERS, operation="PIX", payment_data=pix(SOMEONE))
        assert classify([friend], [paid(day, "1800.80")], OWN)[friend.id].kind == Kind.SPENDING

    def test_a_payment_to_the_issuer_is_matched(self) -> None:
        day = date(2026, 11, 10)
        issuer = line(BANK, day, "-1800.80", category=CARD_PAYMENT, operation="PIX", payment_data=pix(SHOP))
        assert classify([issuer], [paid(day, "1800.80")], OWN)[issuer.id].kind == Kind.BILL_PAYMENT

    def test_a_tie_goes_to_the_debit_that_names_no_one(self) -> None:
        """A Pix of the same amount to a company, the same day and stored first, does not take the payment."""
        day = date(2026, 11, 10)
        shop = line(BANK, day, "-500", category=SHOPPING, operation="PIX", payment_data=pix(SHOP))
        bill_debit = bank_bill_debit(day, "500")
        verdicts = classify([shop, bill_debit], [paid(day, "500")], OWN)
        assert kinds(verdicts, bill_debit, shop) == [Kind.BILL_PAYMENT, Kind.SPENDING]
        assert totals([shop, bill_debit], verdicts) == {SHOPPING: Decimal("500")}

    def test_a_closer_debit_still_wins_over_a_plain_one(self) -> None:
        day = date(2026, 11, 10)
        issuer = line(BANK, day, "-500", category=SHOPPING, operation="PIX", payment_data=pix(SHOP))
        later = bank_bill_debit(day + timedelta(days=1), "500")
        verdicts = classify([issuer, later], [paid(day, "500")], OWN)
        assert kinds(verdicts, issuer, later) == [Kind.BILL_PAYMENT, Kind.SPENDING]

    def test_the_payment_of_a_card_not_connected_stays_spending(self) -> None:
        other_card = line(
            BANK, date(2026, 11, 10), "-350", category=CARD_PAYMENT, operation="PIX", payment_data=pix(SHOP)
        )
        assert classify([other_card], [], OWN)[other_card.id].kind == Kind.SPENDING

    def test_a_bank_credit_of_the_same_amount_is_not_a_payment(self) -> None:
        refund = line(BANK, date(2026, 11, 10), "1800.80", category=TRANSFERS, payment_data=pix(ME, SOMEONE))
        assert classify([refund], [paid(date(2026, 11, 10), "1800.80")], OWN)[refund.id].kind == Kind.INCOME


# ---------------------------------------------------------- transferencias


class TestTransfers:
    def test_a_pix_to_someone_else_is_spending(self) -> None:
        rent = line(BANK, date(2026, 11, 5), "-1800", category=TRANSFERS, operation="PIX", payment_data=pix(SOMEONE))
        assert classify([rent], [], OWN)[rent.id] == Verdict(Kind.SPENDING, "débito na conta")

    def test_a_transfer_to_an_own_account_is_excluded(self) -> None:
        out = line(BANK, date(2026, 11, 5), "-500", category=SAME_PERSON, operation="PIX", payment_data=pix(ME))
        verdict = classify([out], [], OWN)[out.id]
        assert verdict == Verdict(Kind.OWN_TRANSFER, "o favorecido é o próprio titular")

    def test_the_receiver_counts_whatever_pluggy_called_it(self) -> None:
        out = line(BANK, date(2026, 11, 5), "-500", category=TRANSFERS, operation="PIX", payment_data=pix(ME))
        assert classify([out], [], OWN)[out.id].kind == Kind.OWN_TRANSFER

    def test_a_credit_from_an_own_account_is_not_income(self) -> None:
        incoming = line(BANK, date(2026, 11, 5), "500", category=TRANSFERS, payment_data=pix(ME, payer=ME))
        verdict = classify([incoming], [], OWN)[incoming.id]
        assert verdict == Verdict(Kind.OWN_TRANSFER, "o pagador é o próprio titular")

    def test_documents_are_compared_by_their_digits(self) -> None:
        out = line(BANK, date(2026, 11, 5), "-500", category=TRANSFERS, payment_data=pix("00000000000"))
        assert classify([out], [], OWN)[out.id].kind == Kind.OWN_TRANSFER

    def test_without_the_users_documents_nothing_is_own(self) -> None:
        out = line(BANK, date(2026, 11, 5), "-500", category=TRANSFERS, payment_data=pix(ME))
        assert classify([out], [], set())[out.id].kind == Kind.SPENDING

    def test_same_person_without_a_document_takes_pluggy_at_its_word(self) -> None:
        out = line(BANK, date(2026, 11, 5), "-500", category=SAME_PERSON, payment_data=pix(None))
        assert classify([out], [], OWN)[out.id].kind == Kind.OWN_TRANSFER

    def test_same_person_naming_someone_else_is_spending(self) -> None:
        out = line(BANK, date(2026, 11, 5), "-500", category=SAME_PERSON, payment_data=pix(SOMEONE))
        assert classify([out], [], OWN)[out.id].kind == Kind.SPENDING

    def test_the_two_ends_between_connected_accounts_are_paired(self) -> None:
        out = line(BANK, date(2026, 11, 5), "-700", category=TRANSFERS, payment_data=pix(None))
        incoming = line(OTHER_BANK, date(2026, 11, 6), "700", category=TRANSFERS, payment_data=pix(None, payer=None))
        verdicts = classify([out, incoming], [], OWN)
        assert kinds(verdicts, out, incoming) == [Kind.OWN_TRANSFER, Kind.OWN_TRANSFER]
        assert verdicts[out.id].paired_id == incoming.id
        assert verdicts[incoming.id].paired_id == out.id

    def test_ends_in_the_same_account_are_not_a_transfer(self) -> None:
        out = line(BANK, date(2026, 11, 5), "-700", category=TRANSFERS, payment_data=pix(None))
        back = line(BANK, date(2026, 11, 6), "700", category=TRANSFERS, payment_data=pix(None, payer=None))
        assert kinds(classify([out, back], [], OWN), out, back) == [Kind.SPENDING, Kind.INCOME]

    def test_an_end_that_names_someone_else_is_not_paired(self) -> None:
        out = line(BANK, date(2026, 11, 5), "-700", category=TRANSFERS, payment_data=pix(SOMEONE))
        incoming = line(OTHER_BANK, date(2026, 11, 5), "700", category=TRANSFERS, payment_data=pix(None, payer=None))
        assert kinds(classify([out, incoming], [], OWN), out, incoming) == [Kind.SPENDING, Kind.INCOME]

    def test_ends_that_are_not_transfers_are_not_paired(self) -> None:
        purchase = line(BANK, date(2026, 11, 5), "-700", category=SHOPPING, operation="CARTAO")
        incoming = line(OTHER_BANK, date(2026, 11, 5), "700", category=TRANSFERS, payment_data=pix(None, payer=None))
        assert kinds(classify([purchase, incoming], [], OWN), purchase, incoming) == [Kind.SPENDING, Kind.INCOME]

    def test_ends_too_far_apart_are_not_paired(self) -> None:
        out = line(BANK, date(2026, 11, 1), "-700", category=TRANSFERS, payment_data=pix(None))
        incoming = line(OTHER_BANK, date(2026, 11, 10), "700", category=TRANSFERS, payment_data=pix(None, payer=None))
        assert kinds(classify([out, incoming], [], OWN), out, incoming) == [Kind.SPENDING, Kind.INCOME]

    def test_own_transfers_never_reach_an_analysis(self) -> None:
        lines = [
            line(BANK, date(2026, 11, 5), "-500", category=SAME_PERSON, payment_data=pix(ME)),
            line(BANK, date(2026, 11, 6), "500", category=SAME_PERSON, payment_data=pix(ME, payer=ME)),
            line(BANK, date(2026, 11, 7), "-40", category=SHOPPING, payment_data=pix(SHOP)),
        ]
        assert totals(lines, classify(lines, [], OWN)) == {SHOPPING: Decimal("40")}


# ------------------------------------------------------------ investimento


class TestInvestment:
    @pytest.mark.parametrize("amount", ["-1000", "1000"])
    def test_an_application_or_a_redemption_is_an_investment(self, amount: str) -> None:
        entry = line(BANK, date(2026, 11, 5), amount, category=SHOPPING, operation="RESGATE_APLIC_FINANCEIRA")
        assert classify([entry], [], OWN)[entry.id].kind == Kind.INVESTMENT

    def test_a_redemption_is_not_income(self) -> None:
        entry = line(
            BANK,
            date(2026, 11, 5),
            "1000",
            category="05090000",
            operation="OUTROS",
            description="Valor recebido de Investimentos",
        )
        assert classify([entry], [], OWN)[entry.id] == Verdict(Kind.INVESTMENT, "resgate de aplicação")

    def test_the_investments_category_is_an_investment(self) -> None:
        entry = line(BANK, date(2026, 11, 5), "-300", category="03010000", operation="PIX", payment_data=pix(SHOP))
        assert classify([entry], [], OWN)[entry.id] == Verdict(Kind.INVESTMENT, "aplicação")

    @pytest.mark.parametrize("category", [INVESTMENTS, "03060000"])
    def test_a_dividend_under_investments_is_income(self, category: str) -> None:
        """A dividend, interest on equity or yield credited to the account is not a redemption."""
        entry = line(BANK, date(2026, 11, 5), "42.10", category=category, operation="OUTROS", description="Dividendos")
        assert classify([entry], [], OWN)[entry.id] == Verdict(Kind.INCOME, "crédito na conta")

    def test_a_redemption_under_investments_is_still_an_investment(self) -> None:
        entry = line(BANK, date(2026, 11, 5), "1000", category=INVESTMENTS, operation="RESGATE_APLIC_FINANCEIRA")
        assert classify([entry], [], OWN)[entry.id] == Verdict(Kind.INVESTMENT, "resgate de aplicação")


# ---------------------------------------------------------------- estornos


class TestRefund:
    def test_a_refund_abates_its_category(self) -> None:
        purchase = line(CARD, date(2026, 10, 14), "250.00")
        other = line(CARD, date(2026, 10, 16), "40")
        refund = line(CARD, date(2026, 10, 20), "-100", operation="ESTORNO", description="Estorno de compra")
        verdicts = classify([purchase, other, refund], [], OWN)
        assert verdicts[refund.id] == Verdict(Kind.REFUND, "estorno no cartão")
        assert totals([purchase, other, refund], verdicts) == {SHOPPING: Decimal("190.00")}

    def test_a_purchase_and_its_full_refund_sum_to_zero(self) -> None:
        purchase = line(CARD, date(2026, 7, 12), "75.30", category="08040000")
        refund = line(CARD, date(2026, 7, 19), "-75.30", category="08040000", operation="ESTORNO")
        assert totals([purchase, refund], classify([purchase, refund], [], OWN)) == {"08040000": Decimal("0")}

    def test_a_partial_refund_abates_what_was_credited(self) -> None:
        purchase = line(CARD, date(2026, 7, 12), "300")
        refund = line(CARD, date(2026, 7, 19), "-45.50", operation="ESTORNO")
        assert totals([purchase, refund], classify([purchase, refund], [], OWN)) == {SHOPPING: Decimal("254.50")}

    def test_a_refund_on_a_later_bill_abates_the_month_it_posts(self) -> None:
        purchase = line(CARD, date(2026, 6, 4), "45.50")
        refund = line(CARD, date(2026, 8, 3), "-45.50", operation="ESTORNO")
        verdicts = classify([purchase, refund], [], OWN)
        by_month: dict[tuple[int, int], Decimal] = defaultdict(Decimal)
        for entry in (purchase, refund):
            if verdicts[entry.id].kind in COUNTED:
                by_month[(entry.local_date.year, entry.local_date.month)] += spending_value("CREDIT", entry.amount)
        assert by_month == {(2026, 6): Decimal("45.50"), (2026, 8): Decimal("-45.50")}

    def test_the_refund_of_an_installment_purchase_cancels_it(self) -> None:
        installments = [line(CARD, date(2026, month, 15), "90.00") for month in (10, 11, 12)]
        refund = line(CARD, date(2026, 10, 17), "-270.00", operation="ESTORNO")
        lines = [*installments, refund]
        assert totals(lines, classify(lines, [], OWN)) == {SHOPPING: Decimal("0")}

    def test_any_other_credit_on_the_card_is_a_refund(self) -> None:
        iof = line(
            CARD, date(2026, 10, 23), "-12.34", category="15030000", operation="OPERACOES_CREDITO_CONTRATADAS_CARTAO"
        )
        assert classify([iof], [], OWN)[iof.id] == Verdict(Kind.REFUND, "crédito no cartão")

    def test_a_refund_on_the_checking_account(self) -> None:
        refund = line(BANK, date(2026, 10, 24), "35", operation="ESTORNO")
        assert classify([refund], [], OWN)[refund.id] == Verdict(Kind.REFUND, "estorno na conta")


# ------------------------------------------------------------------ resto


class TestDefaults:
    def test_a_card_purchase_is_spending(self) -> None:
        purchase = line(CARD, date(2026, 10, 15), "50")
        assert classify([purchase], [], OWN)[purchase.id] == Verdict(Kind.SPENDING, "compra no cartão")

    def test_a_plain_credit_is_income(self) -> None:
        salary = line(BANK, date(2026, 10, 5), "5000", category=TRANSFERS, payment_data=pix(ME, payer=SHOP))
        assert classify([salary], [], OWN)[salary.id] == Verdict(Kind.INCOME, "crédito na conta")

    def test_every_line_gets_a_verdict_and_none_is_ignored(self) -> None:
        lines = [
            line(CARD, date(2026, 10, 15), "50"),
            card_payment(date(2026, 10, 9), "50"),
            bank_bill_debit(date(2026, 10, 9), "50"),
            line(BANK, date(2026, 10, 5), "5000"),
            line(BANK, date(2026, 10, 6), "-30"),
        ]
        verdicts = classify(lines, [], OWN)
        assert set(verdicts) == {entry.id for entry in lines}
        assert Kind.IGNORED not in {verdict.kind for verdict in verdicts.values()}

    def test_spending_value_signs(self) -> None:
        assert spending_value("CREDIT", Decimal("50")) == Decimal("50")
        assert spending_value("BANK", Decimal("-50")) == Decimal("50")
        assert spending_value("BANK", Decimal("20")) == Decimal("-20")

    def test_spending_value_is_in_the_account_currency(self) -> None:
        """A purchase abroad: ``amount`` in dollars, ``amountInAccountCurrency`` in reais."""
        assert spending_value("CREDIT", Decimal("20"), Decimal("112.40")) == Decimal("112.40")
        assert spending_value("BANK", Decimal("-20"), Decimal("-112.40")) == Decimal("112.40")
        purchase = line(CARD, date(2026, 10, 14), "20", in_account="112.40")
        refund = line(CARD, date(2026, 10, 20), "-5", operation="ESTORNO", in_account="-28.10")
        lines = [purchase, refund]
        assert totals(lines, classify(lines, [], OWN)) == {SHOPPING: Decimal("84.30")}

    @pytest.mark.parametrize("payment_data", [[], ["x"], "PIX", 7, 1.5, True])
    def test_payment_data_that_is_not_an_object_names_no_one(self, payment_data: object) -> None:
        """Pluggy breaking its own contract must not stop every ``bogle spending`` command."""
        debit = line(BANK, date(2026, 11, 10), "-30", category=TRANSFERS, payment_data=payment_data)
        credit = line(BANK, date(2026, 11, 10), "30", category=TRANSFERS, payment_data=payment_data)
        verdicts = classify([debit, credit], [], OWN)
        assert kinds(verdicts, debit, credit) == [Kind.SPENDING, Kind.INCOME]


class TestParseBillPayments:
    def test_reads_nubanks_shape(self) -> None:
        payments = [
            {"paymentDate": "2026-11-10T00:00:00.000Z", "amount": "1800.80", "paymentMode": "DEBIT_ACCOUNT"},
            {"paymentDate": "2026-10-06T00:00:00.000Z", "amount": 700.5},
        ]
        assert parse_bill_payments(CARD, date(2026, 11, 10), payments) == [
            BillPayment(CARD, date(2026, 11, 10), Decimal("1800.80"), date(2026, 11, 10)),
            BillPayment(CARD, date(2026, 11, 10), Decimal("700.5"), date(2026, 10, 6)),
        ]

    @pytest.mark.parametrize(
        "payments",
        [
            None,
            {},
            ["x"],
            [{"amount": "10"}],
            [{"paymentDate": "2026-11-10T00:00:00.000Z"}],
            [{"paymentDate": "ontem", "amount": "10"}],
            [{"paymentDate": "2026-11-10T00:00:00.000Z", "amount": "dez"}],
            [{"paymentDate": "2026-11-10T00:00:00.000Z", "amount": "NaN"}],
            [{"paymentDate": "0001-01-01T01:00:00", "amount": "10"}],
            [{"paymentDate": "9999-12-31T23:59:59-12:00", "amount": "10"}],
        ],
    )
    def test_skips_what_it_cannot_read(self, payments: Any) -> None:
        assert parse_bill_payments(CARD, date(2026, 11, 10), payments) == []
