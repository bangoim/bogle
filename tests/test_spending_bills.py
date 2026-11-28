"""Tests for the card bills, the installments and the projection (issue #118).

Through the sync and the test database, and over lines built by hand. The
card closes on the 3rd and is due on the 10th; "today" is 2026-11-28.

====== ============================================================ =========
bill   lines (bill_id)                                              Pluggy
====== ============================================================ =========
09/10  purchases 100 and 200                                        300, paid
10/10  payment of the previous bill -300; purchase 400;             515, 200
       fridge 1/3 100; IOF 15 in ``financeCharges``                 paid
11/10  fridge 2/3 100; course 1/10 50; market 100                   260, unpaid
open   bakery 80 (11-15); fridge 3/3 (11-20); course 2/10 (11-25);  closes
       sneakers 1/4 75 (11-10); none with a bill_id                 12-03 (est.)
ahead  sneakers 2/4 (12-02), 3/4 (01-02), 4/4 (02-02); course 3/10
       to 6/10 on the 25th (12-25 to 03-25); a PENDING copy of the
       course's 2/10 (12-25) and of the fridge's 3/3 (12-20)
====== ============================================================ =========

The checking account has a salary of 5000 on the 1st, from August.
"""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from psycopg.rows import DictRow

from bogle.domain.errors import ValidationError
from bogle.spending.analysis import Coverage
from bogle.spending.bills import (
    PAYMENTS_POSTED,
    BillState,
    Charge,
    Cycle,
    average_income,
    base_description,
    bill_detail,
    bill_state,
    bills_overview,
    card_bills,
    commitments,
    finance_charges,
    installment_plans,
    installments,
    parse_bill_ref,
    purchase_key,
)
from bogle.spending.models import CardLine, IncomeLine, StoredAccount, StoredBill
from bogle.spending.sync import run_sync
from tests.pluggy_fakes import ITEM, NOW, FakePluggy, account, bill, category_tree, item, tx

Conn = psycopg.Connection[DictRow]

CARD = "acc-card"
BANK = "acc-bank"
TODAY = date(2026, 11, 28)

SHOPPING = ("08000000", "Shopping")
GROCERIES = ("10000000", "Groceries")
SALARY = ("01010000", "Salary")
CARD_PAYMENT = ("05100000", "Credit card payment")

D = Decimal


def buy(tx_id: str, day: date, amount: str, description: str, *, bill_id: str | None = None, **kw: Any) -> Any:
    return tx(tx_id, CARD, day, amount, description=description, bill_id=bill_id, category=SHOPPING, **kw)


def fridge(number: int, day: date, *, bill_id: str | None = None, status: str = "POSTED", tx_id: str = "") -> Any:
    """One of three installments of 300, bought on 2026-09-20."""
    return buy(
        tx_id or f"fridge-{number}",
        day,
        "100",
        f"Geladeira {number}/3",
        bill_id=bill_id,
        installment=number,
        installments=3,
        purchase_date=date(2026, 9, 20),
        purchase_total="300",
        status=status,
    )


def course(number: int, day: date, *, bill_id: str | None = None, status: str = "POSTED", tx_id: str = "") -> Any:
    """One of ten installments of 50, bought on 2026-10-25."""
    return buy(
        tx_id or f"course-{number}",
        day,
        "50",
        f"CURSO ONLINE - Parcela {number}/10",
        bill_id=bill_id,
        installment=number,
        installments=10,
        purchase_date=date(2026, 10, 25),
        status=status,
    )


def sneakers(number: int, day: date, *, status: str = "PENDING") -> Any:
    """One of four installments of 75, bought on 2026-11-10; no purchase date, no description suffix."""
    return buy(f"sneakers-{number}", day, "75", "Tênis", installment=number, installments=4, status=status)


def history() -> tuple[list[Any], list[Any]]:
    """The card's lines and bills of the module's table."""
    lines = [
        buy("s1", date(2026, 8, 5), "100", "Loja A", bill_id="bill-sep"),
        buy("s2", date(2026, 8, 20), "200", "Loja B", bill_id="bill-sep"),
        tx(
            "o-pay",
            CARD,
            date(2026, 9, 10),
            "-300",
            description="Pagamento recebido",
            bill_id="bill-oct",
            kind="CREDIT",
            operation="PAGAMENTO_FATURA",
            category=CARD_PAYMENT,
        ),
        buy("o-big", date(2026, 9, 15), "400", "Loja Grande", bill_id="bill-oct"),
        fridge(1, date(2026, 9, 20), bill_id="bill-oct"),
        fridge(2, date(2026, 10, 20), bill_id="bill-nov"),
        course(1, date(2026, 10, 25), bill_id="bill-nov"),
        tx("n-market", CARD, date(2026, 10, 28), "100", description="Mercado", bill_id="bill-nov", category=GROCERIES),
        buy("a-bakery", date(2026, 11, 15), "80", "Padaria"),
        fridge(3, date(2026, 11, 20)),
        course(2, date(2026, 11, 25)),
        sneakers(1, date(2026, 11, 10), status="POSTED"),
        sneakers(2, date(2026, 12, 2)),
        sneakers(3, date(2027, 1, 2)),
        sneakers(4, date(2027, 2, 2)),
        course(3, date(2026, 12, 25), status="PENDING"),
        course(4, date(2027, 1, 25), status="PENDING"),
        course(5, date(2027, 2, 25), status="PENDING"),
        course(6, date(2027, 3, 25), status="PENDING"),
        # Copias PENDING de parcelas ja lancadas: nao entram de novo na projecao.
        course(2, date(2026, 12, 25), status="PENDING", tx_id="course-2-copy"),
        fridge(3, date(2026, 12, 20), status="PENDING", tx_id="fridge-3-copy"),
    ]
    bills = [
        bill("bill-sep", date(2026, 9, 10), "300", closing=date(2026, 9, 3), payments=[(date(2026, 9, 10), "300")]),
        bill(
            "bill-oct",
            date(2026, 10, 10),
            "515",
            closing=date(2026, 10, 3),
            payments=[(date(2026, 10, 9), "200")],
            charges=[("IOF", "15")],
        ),
        bill("bill-nov", date(2026, 11, 10), "260", closing=date(2026, 11, 3)),
    ]
    return lines, bills


@pytest.fixture
def pluggy() -> FakePluggy:
    fake = FakePluggy()
    fake.categories = category_tree()
    fake.add_item(item(), account(CARD, name="Cartão"), account(BANK, kind="BANK", number="12345-6", name="Conta"))
    lines, bills = history()
    fake.set_transactions(CARD, *lines)
    fake.set_bills(CARD, *bills)
    fake.set_transactions(
        BANK,
        *(
            tx(f"salary-{month}", BANK, date(2026, month, 1), "5000", kind="CREDIT", category=SALARY, at="15:00:00.000")
            for month in (8, 9, 10, 11)
        ),
    )
    return fake


@pytest.fixture
def synced(conn: Conn, pluggy: FakePluggy) -> FakePluggy:
    report = run_sync(conn, pluggy, (ITEM,), now=NOW, full=True)
    assert report.failures == []
    conn.commit()
    return pluggy


def bill_id(conn: Conn, pluggy_id: str) -> int:
    row = conn.execute("SELECT id FROM spending.bills WHERE pluggy_id = %s", (pluggy_id,)).fetchone()
    assert row is not None
    return row["id"]


class TestReconciliation:
    """Criterion 1: each closed bill's lines are set against Pluggy's total, the difference explained."""

    def test_a_bill_that_matches(self, conn: Conn, synced: FakePluggy) -> None:
        card = bills_overview(conn, today=TODAY).cards[0]
        september = next(entry for entry in card.closed if entry.month == date(2026, 9, 1))
        check = september.reconciliation
        assert check is not None
        assert (check.total, check.lines, check.count, check.difference) == (D("300"), D("300"), 2, D("0"))
        assert check.explained == ()
        assert check.unexplained == 0

    def test_the_difference_is_explained_by_payments_and_charges(self, conn: Conn, synced: FakePluggy) -> None:
        card = bills_overview(conn, today=TODAY).cards[0]
        october = next(entry for entry in card.closed if entry.month == date(2026, 10, 1))
        check = october.reconciliation
        assert check is not None
        # 515 da Pluggy: o saldo anterior (300) quitado pelo pagamento lancado, 500 de compras e 15 de IOF.
        assert (check.total, check.lines, check.difference) == (D("515"), D("200"), D("315"))
        assert check.explained == (Charge(PAYMENTS_POSTED, D("300")), Charge("IOF", D("15")))
        assert check.unexplained == 0

    def test_what_is_left_is_shown_as_unexplained(self, conn: Conn, synced: FakePluggy) -> None:
        card = bills_overview(conn, today=TODAY).cards[0]
        november = next(entry for entry in card.closed if entry.month == date(2026, 11, 1))
        check = november.reconciliation
        assert check is not None
        assert (check.lines, check.difference, check.unexplained) == (D("250"), D("10"), D("10"))

    def test_closed_bills_newest_first_with_what_was_paid(self, conn: Conn, synced: FakePluggy) -> None:
        card = bills_overview(conn, today=TODAY).cards[0]
        assert [entry.month.month for entry in card.closed] == [11, 10, 9]
        assert [(entry.paid, entry.paid_on) for entry in card.closed] == [
            (D("0"), None),
            (D("200"), date(2026, 10, 9)),
            (D("300"), date(2026, 9, 10)),
        ]
        assert [entry.id for entry in card.closed] == [bill_id(conn, f"bill-{m}") for m in ("nov", "oct", "sep")]


class TestStates:
    """Criterion 4: a closed bill not paid is ``fechada a vencer`` until its due date."""

    def test_closed_and_not_paid_is_due(self, conn: Conn, synced: FakePluggy) -> None:
        card = bills_overview(conn, today=date(2026, 11, 5)).cards[0]
        assert [entry.state for entry in card.closed] == [BillState.DUE, BillState.PARTIAL, BillState.PAID]
        # No dia do vencimento ainda esta a vencer; depois, vencida.
        assert bills_overview(conn, today=date(2026, 11, 10)).cards[0].closed[0].state == BillState.DUE
        assert bills_overview(conn, today=TODAY).cards[0].closed[0].state == BillState.OVERDUE

    @pytest.mark.parametrize(
        ("total", "paid", "closing", "due", "state"),
        [
            ("100", "0", date(2026, 12, 3), date(2026, 12, 10), BillState.OPEN),
            ("100", "0", date(2026, 11, 28), date(2026, 12, 5), BillState.DUE),
            ("100", "100", date(2026, 11, 3), date(2026, 11, 10), BillState.PAID),
            ("100", "120", date(2026, 11, 3), date(2026, 11, 10), BillState.PAID),
            ("100", "40", date(2026, 11, 3), date(2026, 12, 10), BillState.PARTIAL),
            ("100", "40", date(2026, 10, 3), date(2026, 10, 10), BillState.PARTIAL),
            ("100", "0", date(2026, 10, 3), date(2026, 10, 10), BillState.OVERDUE),
            ("0", "0", date(2026, 10, 3), date(2026, 10, 10), BillState.PAID),
            ("-5", "0", date(2026, 10, 3), date(2026, 10, 10), BillState.PAID),
            ("100", "0", None, date(2026, 12, 10), BillState.DUE),
        ],
    )
    def test_state(self, total: str, paid: str, closing: date | None, due: date, state: BillState) -> None:
        assert bill_state(D(total), D(paid), closing, due, TODAY) == state


class TestOpenBill:
    def test_the_lines_after_the_last_closing_up_to_today(self, conn: Conn, synced: FakePluggy) -> None:
        opened = bills_overview(conn, today=TODAY).cards[0].open
        assert opened is not None
        assert opened.state == BillState.OPEN
        assert (opened.closing, opened.due, opened.estimated, opened.id) == (
            date(2026, 12, 3),
            date(2026, 12, 10),
            True,
            None,
        )
        # Padaria 80, geladeira 3/3 100, curso 2/10 50, tenis 1/4 75.
        assert (opened.total, opened.purchases) == (D("305"), 4)
        assert opened.month == date(2026, 12, 1)

    def test_the_card_account_dates_win_over_the_estimate(self) -> None:
        lines = [line(1, date(2026, 11, 20), "80"), line(2, date(2026, 11, 1), "10")]
        cards = card_bills(ACCOUNT, lines, [], today=TODAY, cycle_dates=(date(2026, 12, 1), date(2026, 12, 8)))
        assert cards.open is not None
        assert (cards.open.closing, cards.open.due, cards.open.estimated) == (
            date(2026, 12, 1),
            date(2026, 12, 8),
            False,
        )
        # Sem fatura gravada, o ciclo comeca um mes antes do fechamento.
        assert [entry.id for entry in cards.open.lines] == [1]

    def test_without_any_closing_date_there_is_no_open_bill(self) -> None:
        cards = card_bills(ACCOUNT, [line(1, date(2026, 11, 20), "80")], [], today=TODAY)
        assert cards.open is None
        assert cards.ahead == ()

    def test_a_stored_bill_still_open(self) -> None:
        """``/bills`` may list the running bill: it is the open one, with its lines and the unbilled ones."""
        stored = [
            stored_bill(1, "b-nov", date(2026, 11, 3), date(2026, 11, 10), "10"),
            stored_bill(2, "b-dec", date(2026, 12, 3), date(2026, 12, 10), "999"),
        ]
        lines = [
            line(1, date(2026, 11, 2), "10", bill_id="b-nov"),
            line(2, date(2026, 11, 5), "20", bill_id="b-dec"),
            line(3, date(2026, 11, 6), "30"),
        ]
        cards = card_bills(ACCOUNT, lines, stored, today=TODAY)
        assert [entry.id for entry in cards.closed] == [1]
        assert cards.open is not None
        assert (cards.open.id, cards.open.state, cards.open.total, cards.open.estimated) == (
            2,
            BillState.OPEN,
            D("50"),
            False,
        )

    def test_a_lagging_bills_list_does_not_merge_two_cycles(self) -> None:
        """The last bill on record closed in September: the open cycle still starts at the estimated 11-03."""
        stored = [stored_bill(1, "b-sep", date(2026, 9, 3), date(2026, 9, 10), "10")]
        lines = [line(1, date(2026, 10, 20), "10", bill_id="b-oct"), line(2, date(2026, 11, 20), "20")]
        cards = card_bills(ACCOUNT, lines, stored, today=TODAY)
        assert cards.open is not None
        assert [entry.id for entry in cards.open.lines] == [2]

    def test_a_drifting_closing_day_keeps_the_real_last_closing(self) -> None:
        stored = [stored_bill(1, "b-nov", date(2026, 11, 1), date(2026, 11, 10), "10")]
        cards = card_bills(
            ACCOUNT,
            [line(1, date(2026, 11, 2), "10")],
            stored,
            today=date(2026, 11, 5),
            cycle_dates=(date(2026, 12, 3), date(2026, 12, 10)),
        )
        assert cards.open is not None
        assert [entry.id for entry in cards.open.lines] == [1]


class TestProjection:
    """Criteria 2 and 5: each installment ahead goes to the bill its closing date says, and only once."""

    def test_each_installment_goes_to_the_bill_by_its_closing(self, conn: Conn, synced: FakePluggy) -> None:
        card = bills_overview(conn, today=TODAY).cards[0]
        ahead = {entry.month: sorted(line.description for line in entry.upcoming) for entry in card.ahead}
        assert ahead == {
            date(2026, 12, 1): ["Tênis"],  # 02/12 fecha em 03/12
            date(2027, 1, 1): ["CURSO ONLINE - Parcela 3/10", "Tênis"],  # 25/12 e 02/01 fecham em 03/01
            date(2027, 2, 1): ["CURSO ONLINE - Parcela 4/10", "Tênis"],
            date(2027, 3, 1): ["CURSO ONLINE - Parcela 5/10"],
            date(2027, 4, 1): ["CURSO ONLINE - Parcela 6/10"],
        }
        assert [(entry.closing, entry.due, entry.state) for entry in card.forecast][:2] == [
            (date(2027, 1, 3), date(2027, 1, 10), BillState.FORECAST),
            (date(2027, 2, 3), date(2027, 2, 10), BillState.FORECAST),
        ]

    def test_an_installment_already_on_the_open_bill_is_not_projected_again(
        self, conn: Conn, synced: FakePluggy
    ) -> None:
        card = bills_overview(conn, today=TODAY).cards[0]
        assert card.open is not None
        posted = {(line.description, line.installment_number) for line in card.open.lines}
        assert ("CURSO ONLINE - Parcela 2/10", 2) in posted
        assert ("Geladeira 3/3", 3) in posted
        projected = [line for entry in card.ahead for line in entry.upcoming]
        # As copias PENDING (curso 2/10 em 25/12, geladeira 3/3 em 20/12) ficaram de fora.
        assert all(line.description != "Geladeira 3/3" for line in projected)
        assert [line.installment_number for line in projected if line.description.startswith("CURSO")] == [3, 4, 5, 6]
        keys = [(purchase_key(line), line.installment_number) for line in projected]
        assert len(keys) == len(set(keys))

    def test_on_the_closing_day_it_is_still_that_bill(self) -> None:
        stored = [stored_bill(1, "b-nov", date(2026, 11, 3), date(2026, 11, 10), "0")]
        lines = [
            line(1, date(2027, 1, 3), "40", installment=(2, 3)),
            line(2, date(2027, 1, 4), "40", installment=(3, 3)),
        ]
        cards = card_bills(ACCOUNT, lines, stored, today=TODAY)
        assert [(entry.month, [x.id for x in entry.upcoming]) for entry in cards.forecast] == [
            (date(2027, 1, 1), [1]),
            (date(2027, 2, 1), [2]),
        ]

    def test_the_committed_months_against_the_average_income(self, conn: Conn, synced: FakePluggy) -> None:
        overview = bills_overview(conn, today=TODAY)
        # Agosto e o mes de entrada da conta: a media vem de setembro e outubro.
        assert overview.income == D("5000")
        rows = [(entry.month, entry.total, overview.share(entry)) for entry in overview.committed]
        assert rows == [
            (date(2026, 12, 1), D("75"), D("0.015")),
            (date(2027, 1, 1), D("125"), D("0.025")),
            (date(2027, 2, 1), D("125"), D("0.025")),
            (date(2027, 3, 1), D("50"), D("0.01")),
            (date(2027, 4, 1), D("50"), D("0.01")),
        ]

    def test_without_income_there_is_no_share(self, conn: Conn, synced: FakePluggy) -> None:
        overview = bills_overview(conn, today=date(2026, 8, 20))
        assert overview.income is None
        assert all(overview.share(entry) is None for entry in overview.committed)

    def test_a_month_without_installments_between_two_with_them_is_zero(self) -> None:
        stored = [stored_bill(1, "b-nov", date(2026, 11, 3), date(2026, 11, 10), "0")]
        lines = [
            line(1, date(2026, 12, 20), "40", installment=(2, 4)),
            line(2, date(2027, 2, 20), "40", installment=(4, 4)),
        ]
        cards = card_bills(ACCOUNT, lines, stored, today=TODAY)
        rows = [(entry.month, entry.total) for entry in commitments([cards])]
        assert rows == [(date(2027, 1, 1), D("40")), (date(2027, 2, 1), D("0")), (date(2027, 3, 1), D("40"))]


class TestInstallments:
    """Criterion 3: a purchase whose last installment posted leaves the list."""

    def test_the_running_purchases(self, conn: Conn, synced: FakePluggy) -> None:
        plans = installments(conn, today=TODAY)
        assert [(plan.description, plan.label, plan.value, plan.remaining, plan.last_month) for plan in plans] == [
            # 8 parcelas a 50: 4 ja vieram como PENDING (3 a 6), as outras 4 pelo valor da ultima lancada.
            ("CURSO ONLINE", "2/10", D("50"), D("400"), date(2027, 7, 1)),
            ("Tênis", "1/4", D("75"), D("225"), date(2027, 2, 1)),
        ]
        assert plans[0].purchase_date == date(2026, 10, 25)
        assert plans[1].purchase_date is None

    def test_a_paid_off_purchase_leaves_the_list(self, conn: Conn, synced: FakePluggy) -> None:
        before = installments(conn, today=date(2026, 11, 19))
        assert "Geladeira" in [plan.description for plan in before]
        assert next(plan for plan in before if plan.description == "Geladeira").label == "2/3"
        after = installments(conn, today=TODAY)
        assert "Geladeira" not in [plan.description for plan in after]

    def test_a_purchase_whose_first_installment_is_ahead(self) -> None:
        plans = installment_plans([line(1, date(2026, 12, 5), "30", installment=(1, 2))], today=TODAY)
        assert [(plan.label, plan.value, plan.remaining, plan.last_month) for plan in plans] == [
            ("0/2", D("30"), D("60"), date(2027, 1, 1))
        ]

    def test_refunds_and_single_purchases_are_not_installments(self) -> None:
        lines = [
            line(1, date(2026, 11, 5), "30"),
            line(2, date(2026, 11, 5), "-30", installment=(1, 2)),
        ]
        assert installment_plans(lines, today=TODAY) == ()

    def test_only_cards(self, conn: Conn, synced: FakePluggy) -> None:
        with pytest.raises(ValidationError, match="'corrente' não é um cartão"):
            installments(conn, today=TODAY, account="corrente")
        with pytest.raises(ValidationError, match="'Conta' não é um cartão"):
            bills_overview(conn, today=TODAY, account="Conta")


class TestDetail:
    def test_a_closed_bill_by_group_with_its_installments(self, conn: Conn, synced: FakePluggy) -> None:
        detail = bill_detail(conn, date(2026, 10, 1), today=TODAY)
        assert detail.bill.state == BillState.PARTIAL
        # O pagamento da fatura anterior nao e gasto: fica fora dos grupos, dentro da conciliacao.
        assert [(entry.group.name, entry.value) for entry in detail.groups] == [("Compras", D("500"))]
        assert [line.description for line in detail.installments] == ["Geladeira 1/3"]
        assert detail.bill.reconciliation is not None
        assert bill_detail(conn, bill_id(conn, "bill-oct"), today=TODAY).bill == detail.bill

    def test_groups_and_categories(self, conn: Conn, synced: FakePluggy) -> None:
        detail = bill_detail(conn, date(2026, 11, 1), today=TODAY)
        assert [(entry.group.name, entry.value) for entry in detail.groups] == [
            ("Compras", D("150")),
            ("Alimentação", D("100")),
        ]
        assert [(category.name, category.value) for category in detail.groups[1].categories] == [
            ("Supermercado", D("100"))
        ]

    def test_the_open_bill_and_a_forecast(self, conn: Conn, synced: FakePluggy) -> None:
        opened = bill_detail(conn, date(2026, 12, 1), today=TODAY)
        assert opened.bill.state == BillState.OPEN
        assert sum((entry.value for entry in opened.groups), D(0)) == D("305")
        assert [line.description for line in opened.installments] == [
            "Tênis",
            "Geladeira 3/3",
            "CURSO ONLINE - Parcela 2/10",
            "Tênis",
        ]
        forecast = bill_detail(conn, date(2027, 1, 1), today=TODAY)
        assert forecast.bill.state == BillState.FORECAST
        assert [(entry.group.name, entry.value) for entry in forecast.groups] == [("Compras", D("125"))]
        assert forecast.bill.reconciliation is None

    def test_unknown_or_ambiguous(self, conn: Conn, pluggy: FakePluggy) -> None:
        pluggy.add_item(
            item(),
            *pluggy.accounts[ITEM],
            account("acc-black", name="Black", number="1111"),
        )
        pluggy.set_transactions("acc-black", tx("k1", "acc-black", date(2026, 11, 2), "10", bill_id="black-nov"))
        pluggy.set_bills("acc-black", bill("black-nov", date(2026, 11, 15), "10", closing=date(2026, 11, 5)))
        assert run_sync(conn, pluggy, (ITEM,), now=NOW, full=True).failures == []
        with pytest.raises(ValidationError, match=r"Mais de um cartão tem fatura em 2026-11 \(Cartão, Black\)"):
            bill_detail(conn, date(2026, 11, 1), today=TODAY)
        detail = bill_detail(conn, date(2026, 11, 1), today=TODAY, account="Black")
        assert detail.bill.total == D("10")
        with pytest.raises(ValidationError, match="Nenhuma fatura vence em 2025-01"):
            bill_detail(conn, date(2025, 1, 1), today=TODAY)
        with pytest.raises(ValidationError, match="Fatura 999999 não encontrada"):
            bill_detail(conn, 999999, today=TODAY)
        # O id de uma fatura de outro cartao nao vale com --account.
        with pytest.raises(ValidationError, match="não encontrada"):
            bill_detail(conn, bill_id(conn, "bill-nov"), today=TODAY, account="Black")


# ---- funcoes puras

ACCOUNT = StoredAccount(
    id=1,
    pluggy_id="acc-1",
    replaced_pluggy_ids=(),
    item_id=ITEM,
    type="CREDIT",
    subtype="CREDIT_CARD",
    number=None,
    name="Cartão",
    marketing_name=None,
    tax_number=None,
    transfer_number=None,
    credit_brand=None,
    last_synced_at=None,
    first_transaction_date=date(2026, 1, 1),
    last_transaction_date=None,
)


def line(
    line_id: int,
    day: date,
    amount: str,
    *,
    bill_id: str | None = None,
    installment: tuple[int, int] | None = None,
    description: str = "Loja",
) -> CardLine:
    return CardLine(
        id=line_id,
        account_id=1,
        local_date=day,
        amount=D(amount),
        description=description,
        bill_id=bill_id,
        kind="gasto" if D(amount) > 0 else "abatimento",
        installment_number=installment[0] if installment else None,
        total_installments=installment[1] if installment else None,
    )


def stored_bill(local_id: int, pluggy_id: str, closing: date | None, due: date, total: str) -> StoredBill:
    return StoredBill(
        id=local_id, pluggy_id=pluggy_id, account_id=1, due_date=due, closing_date=closing, total_amount=D(total)
    )


class TestPure:
    def test_the_next_cycles_keep_the_days(self) -> None:
        cycle = Cycle(date(2026, 1, 31), date(2026, 2, 7))
        assert cycle.following(1) == Cycle(date(2026, 2, 28), date(2026, 3, 7), estimated=True)
        assert cycle.following(2) == Cycle(date(2026, 3, 31), date(2026, 4, 7), estimated=True)
        assert Cycle(date(2026, 11, 28), date(2026, 12, 5)).following(1).due == date(2027, 1, 5)
        assert Cycle(date(2026, 11, 3), date(2026, 11, 10)).following(-1).closing == date(2026, 10, 3)

    @pytest.mark.parametrize(
        ("text", "base"),
        [
            ("Loja Exemplo 3/10", "Loja Exemplo"),
            ("CURSO - Parcela 2/10", "CURSO"),
            ("Loja \u2013 1 / 12", "Loja"),  # meia-risca
            ("Tênis", "Tênis"),
            ("3/10", "3/10"),
        ],
    )
    def test_base_description(self, text: str, base: str) -> None:
        assert base_description(text) == base

    def test_finance_charges_are_read_as_far_as_they_go(self) -> None:
        raw = [
            {"type": "IOF", "amount": 1.5},
            {"type": "LATE_PAYMENT_FEE", "amount": "2"},
            {"type": "NOVO\x1b[2J", "amount": 3},
            {"type": "OTHER", "amount": None},
            {"amount": "x"},
            {"type": "IOF", "amount": 0},
            "lixo",
        ]
        assert finance_charges(raw) == [
            Charge("IOF", D("1.5")),
            Charge("multa por atraso", D("2")),
            Charge("novo[2j", D("3")),
        ]
        assert finance_charges({"type": "IOF"}) == []

    @pytest.mark.parametrize(
        ("text", "ref"),
        [("2026-12", date(2026, 12, 1)), (" 42 ", 42), ("9223372036854775807", 2**63 - 1)],
    )
    def test_parse_bill_ref(self, text: str, ref: date | int) -> None:
        assert parse_bill_ref(text) == ref

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("abc", "Use o mês de vencimento"),
            ("2026-13", "inválido"),
            ("9999-01", "ano vai de 1900 a 2999"),
            ("99999999999999999999999", "não encontrada"),
            ("-1", "Use o mês de vencimento"),
            ("٣", "Use o mês de vencimento"),
        ],
    )
    def test_parse_bill_ref_refuses(self, text: str, message: str) -> None:
        with pytest.raises(ValidationError, match=message):
            parse_bill_ref(text)

    def test_average_income_per_account_over_its_compared_months(self) -> None:
        incomes = [IncomeLine(1, date(2026, month, 5), D("3000")) for month in (8, 9, 10)]
        incomes += [IncomeLine(2, date(2026, 10, 5), D("1000")), IncomeLine(2, date(2026, 11, 5), D("9999"))]
        coverage = Coverage({1: date(2026, 7, 1), 2: date(2026, 9, 1), 3: None})
        # A conta 2 entra em setembro e so e comparada em outubro; novembro e o mes corrente.
        assert average_income(incomes, coverage, today=TODAY) == D("4000")
        assert average_income([], coverage, today=TODAY) is None
        assert average_income(incomes, Coverage({1: date(2026, 11, 1)}), today=TODAY) is None


class TestReviewFindings:
    """The first review's findings, each by its trigger."""

    def test_a_total_sent_on_some_lines_only_keeps_one_purchase(self) -> None:
        """N1: the posted installments carry the purchase's total, the pending ones do not."""

        def part(line_id: int, day: date, number: int, *, total: str | None, status: str = "PENDING") -> CardLine:
            return replace(
                line(line_id, day, "100", installment=(number, 6)),
                purchase_date=date(2026, 9, 10),
                installments_total_amount=D(total) if total else None,
                status=status,
            )

        lines = [
            part(1, date(2026, 9, 10), 1, total="600", status="POSTED"),
            part(2, date(2026, 10, 10), 2, total="600", status="POSTED"),
            part(3, date(2026, 11, 10), 3, total="600", status="POSTED"),
            part(4, date(2026, 12, 10), 4, total=None),
            part(5, date(2027, 1, 10), 5, total=None),
            part(6, date(2027, 2, 10), 6, total=None),
            part(7, date(2026, 12, 10), 3, total=None),  # copia PENDING da 3/6 ja lancada
        ]
        plans = installment_plans(lines, today=TODAY)
        assert [(plan.label, plan.remaining) for plan in plans] == [("3/6", D("300"))]
        stored = [stored_bill(1, "b-nov", date(2026, 11, 3), date(2026, 11, 10), "100")]
        cards = card_bills(ACCOUNT, lines, stored, today=TODAY)
        assert sorted(entry.id for bill in cards.ahead for entry in bill.upcoming) == [4, 5, 6]

    def test_a_purchase_that_stopped_getting_installments_leaves_the_list(self) -> None:
        """N2: 10x with 1 to 5 posted from January to May and nothing after (paid ahead or refunded)."""
        lines = [
            replace(
                line(number, date(2026, number, 5), "100", installment=(number, 10)), purchase_date=date(2026, 1, 5)
            )
            for number in range(1, 6)
        ]
        assert installment_plans(lines, today=TODAY) == ()
        # Ainda no mes da ultima parcela, ela pode chegar: segue na lista.
        assert [plan.label for plan in installment_plans(lines, today=date(2026, 10, 20))] == ["5/10"]

    def test_a_line_on_the_closing_day_with_an_unlisted_bill_is_on_the_open_bill(self) -> None:
        """N3: the bank already posted it to the next bill, which ``/bills`` does not list yet."""
        stored = [stored_bill(1, "b-nov", date(2026, 11, 3), date(2026, 11, 10), "10")]
        lines = [
            line(1, date(2026, 11, 3), "40", bill_id="b-dec"),
            line(2, date(2026, 11, 10), "30"),
            line(3, date(2026, 11, 3), "5"),  # sem bill_id no dia do fechamento: segue fora
            line(4, date(2026, 11, 2), "10", bill_id="b-nov"),
        ]
        cards = card_bills(ACCOUNT, lines, stored, today=TODAY)
        assert cards.open is not None
        assert (cards.open.total, cards.open.purchases) == (D("70"), 2)
        assert [entry.id for entry in cards.closed[0].lines] == [4]

    def test_values_out_of_range_from_pluggy_do_not_break(self) -> None:
        """Z1: an absurd installment number, a bill due in 9999 and a huge charge are left out."""
        huge = line(1, date(2026, 11, 5), "10", installment=(32767, 32767))
        assert purchase_key(huge) is None
        assert purchase_key(line(2, date(2026, 11, 5), "10", installment=(5, 3))) is None
        assert purchase_key(line(3, date(2026, 11, 5), "10", installment=(1, 1000))) is None
        assert purchase_key(line(4, date(2026, 11, 5), "10", installment=(999, 999))) is not None
        assert installment_plans([huge], today=TODAY) == ()
        stored = [
            stored_bill(1, "b-nov", date(2026, 11, 3), date(2026, 11, 10), "10"),
            stored_bill(2, "b-far", date(2026, 10, 3), date(9999, 12, 10), "10"),
            replace(
                stored_bill(3, "b-oct", date(2026, 10, 1), date(2026, 10, 8), "10"),
                finance_charges=[
                    {"type": "IOF", "amount": "1e1000000"},
                    {"type": "IOF", "amount": "2"},
                ],
            ),
        ]
        cards = card_bills(ACCOUNT, [huge], stored, today=TODAY, cycle_dates=(date(2026, 12, 3), date(9999, 1, 10)))
        assert [bill.id for bill in cards.closed] == [1, 3]
        reconciliation = cards.closed[1].reconciliation
        assert reconciliation is not None
        assert reconciliation.explained == (Charge("IOF", D("2")),)
        assert reconciliation.unexplained == D("8")
        assert cards.open is not None
        assert (cards.open.closing, cards.open.due) == (date(2026, 12, 3), date(2026, 12, 10))
        assert finance_charges([{"amount": "1e16"}, {"amount": "-1e16"}, {"amount": "9999999999999999.99"}]) == [
            Charge("outros encargos", D("9999999999999999.99"))
        ]

    @pytest.mark.parametrize("filler", [" ", "-", "\u2013"])  # meia-risca
    def test_a_long_run_of_separators_is_read_fast(self, filler: str) -> None:
        """Z2: the suffix pattern was quadratic over a run of spaces or dashes."""
        started = time.perf_counter()
        assert base_description("Loja" + filler * 5_000 + "x") == "Loja" + filler * 5_000 + "x"
        assert base_description("Loja" + filler * 5_000 + "3/10") == "Loja"
        assert time.perf_counter() - started < 0.5  # antes: ~1,3 s por chamada

    @pytest.mark.parametrize("dated", ["posted", "pending"])
    def test_a_purchase_date_sent_on_some_lines_only_keeps_one_purchase(self, dated: str) -> None:
        """N4: only the posted installments (or only the pending ones) carry the purchase date."""

        def part(line_id: int, day: date, number: int, *, status: str = "PENDING") -> CardLine:
            has_date = (status == "POSTED") == (dated == "posted")
            return replace(
                line(line_id, day, "100", installment=(number, 6)),
                purchase_date=date(2026, 9, 10) if has_date else None,
                status=status,
            )

        lines = [
            part(1, date(2026, 9, 10), 1, status="POSTED"),
            part(2, date(2026, 10, 10), 2, status="POSTED"),
            part(3, date(2026, 11, 10), 3, status="POSTED"),
            part(4, date(2026, 12, 10), 4),
            part(5, date(2027, 1, 10), 5),
            part(6, date(2027, 2, 10), 6),
            part(7, date(2026, 12, 10), 3),  # copia PENDING da 3/6 ja lancada
        ]
        plans = installment_plans(lines, today=TODAY)
        assert [(plan.label, plan.remaining) for plan in plans] == [("3/6", D("300"))]
        stored = [stored_bill(1, "b-nov", date(2026, 11, 3), date(2026, 11, 10), "100")]
        cards = card_bills(ACCOUNT, lines, stored, today=TODAY)
        assert sorted(entry.id for bill in cards.ahead for entry in bill.upcoming) == [4, 5, 6]

    def test_two_dated_purchases_of_the_same_month_leave_an_undated_line_apart(self) -> None:
        """N4: with two candidates the undated line keeps today's rule (the month of its first installment)."""

        def part(line_id: int, day: date, number: int, bought: date | None) -> CardLine:
            return replace(line(line_id, day, "100", installment=(number, 3)), purchase_date=bought)

        lines = [
            part(1, date(2026, 10, 5), 1, date(2026, 10, 5)),
            part(2, date(2026, 10, 20), 1, date(2026, 10, 20)),
            part(3, date(2026, 12, 5), 3, None),
        ]
        assert sorted(plan.label for plan in installment_plans(lines, today=TODAY)) == ["0/3", "1/3", "1/3"]

    @pytest.mark.parametrize(
        ("day", "installment"),
        [
            (date(1, 3, 1), (2, 3)),
            (date(50, 1, 1), (999, 999)),
            (date(1899, 12, 31), (2, 3)),
            (date(3000, 1, 1), (1, 3)),
            (date(9950, 1, 1), (1, 999)),
            (date(9999, 12, 1), (1, 3)),
        ],
    )
    def test_a_card_line_dated_out_of_range_is_not_an_installment(
        self, day: date, installment: tuple[int, int]
    ) -> None:
        """Z3: a line's own date out of 1900 to 2999 made the month arithmetic raise."""
        odd = replace(line(1, day, "10", installment=installment), status="PENDING")
        dated = replace(line(2, day, "10", installment=installment), purchase_date=date(2026, 9, 10))
        assert purchase_key(odd) is None
        assert purchase_key(dated) is None
        lines = [odd, dated, line(3, date(2026, 11, 10), "30")]
        assert installment_plans(lines, today=TODAY) == ()
        stored = [stored_bill(1, "b-nov", date(2026, 11, 3), date(2026, 11, 10), "10")]
        cards = card_bills(ACCOUNT, lines, stored, today=TODAY)
        assert cards.open is not None
        assert [entry.id for entry in cards.open.lines] == [3]
