"""Tests for storing what each transaction is (issue #115), through the sync and the test database.

The rules themselves are in ``test_spending_classify``; here: the sync writes
the verdict and recomputes it every run, ``spending.expenses`` (what the
analyses read) holds spending and refunds only, and a kind set by hand survives
the next sync until it is handed back to the rules.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from psycopg.rows import DictRow

from bogle.domain.errors import ValidationError
from bogle.spending.classify import Kind
from bogle.spending.marking import parse_kind, reclassify, set_kind
from bogle.spending.sync import confirm_duplicate, run_sync
from tests.pluggy_fakes import ITEM, NOW, FakePluggy, account, bill, item, tx

Conn = psycopg.Connection[DictRow]

CARD = "acc-card"
BANK = "acc-bank"
ME = "000.000.000-00"
"""The document the fake accounts carry: the user's."""

TRANSFERS = ("05000000", "Transfers")
SAME_PERSON = ("04000000", "Same person transfer")
CARD_PAYMENT = ("05100000", "Credit card payment")
CLOTHING = ("08040000", "Clothing")


def pix(receiver: str | None, payer: str | None = ME) -> dict[str, Any]:
    def party(document: str | None) -> dict[str, Any]:
        return {"documentNumber": {"type": "CPF", "value": document} if document else None}

    return {"payer": party(payer), "receiver": party(receiver), "paymentMethod": "PIX"}


@pytest.fixture
def pluggy() -> FakePluggy:
    fake = FakePluggy()
    fake.add_item(item(), account(CARD, name="Cartão"), account(BANK, kind="BANK", number="12345-6", name="Conta"))
    fake.set_transactions(
        CARD,
        tx("buy", CARD, date(2026, 10, 14), "75.30", category=CLOTHING, operation="PAGAMENTO"),
        tx("refund", CARD, date(2026, 10, 19), "-75.30", kind="CREDIT", category=CLOTHING, operation="ESTORNO"),
        tx("shirt", CARD, date(2026, 10, 21), "60", category=CLOTHING, operation="PAGAMENTO"),
        tx("back", CARD, date(2026, 10, 22), "-20", kind="CREDIT", category=CLOTHING, operation="ESTORNO"),
        tx(
            "paid",
            CARD,
            date(2026, 11, 10),
            "-1800.80",
            kind="CREDIT",
            category=CARD_PAYMENT,
            operation="PAGAMENTO_FATURA",
            description="Pagamento recebido",
        ),
    )
    fake.set_bills(CARD, bill("bill-11", date(2026, 11, 10), "1800.80", payments=[(date(2026, 11, 10), "1800.80")]))
    fake.set_transactions(
        BANK,
        tx(
            "debit",
            BANK,
            date(2026, 11, 10),
            "-1800.80",
            category=TRANSFERS,
            operation="OUTROS",
            description="Pagamento de fatura",
            at="14:32:07.512",
        ),
        tx(
            "lookalike",
            BANK,
            date(2026, 11, 20),
            "-1800.80",
            category=TRANSFERS,
            operation="PIX",
            payment_data=pix("111.222.333-44"),
            at="14:00:00.000",
        ),
        tx(
            "own",
            BANK,
            date(2026, 11, 10),
            "-500",
            category=SAME_PERSON,
            operation="PIX",
            payment_data=pix(ME),
            at="15:00:00.000",
        ),
        tx(
            "rent",
            BANK,
            date(2026, 11, 11),
            "-1800",
            category=TRANSFERS,
            operation="PIX",
            payment_data=pix("111.222.333-44"),
            at="16:00:00.000",
        ),
        tx(
            "redeem",
            BANK,
            date(2026, 11, 12),
            "1000",
            kind="CREDIT",
            category=("05090000", "Third party transfers"),
            operation="RESGATE_APLIC_FINANCEIRA",
            description="Valor recebido de Investimentos",
            at="17:00:00.000",
        ),
    )
    return fake


def sync(conn: Conn, fake: FakePluggy, **kw: Any) -> None:
    report = run_sync(conn, fake, (ITEM,), now=kw.pop("now", NOW), **kw)
    assert report.failures == []


def kinds(conn: Conn) -> dict[str, str | None]:
    rows = conn.execute("SELECT pluggy_id, kind FROM spending.transactions ORDER BY pluggy_id").fetchall()
    return {row["pluggy_id"]: row["kind"] for row in rows}


def local_id(conn: Conn, pluggy_id: str) -> int:
    row = conn.execute("SELECT id FROM spending.transactions WHERE pluggy_id = %s", (pluggy_id,)).fetchone()
    assert row is not None
    return row["id"]


def expenses(conn: Conn) -> dict[str, Decimal]:
    rows = conn.execute(
        "SELECT t.pluggy_id, e.value FROM spending.expenses e "
        "JOIN spending.transactions t ON t.id = e.transaction_id ORDER BY t.pluggy_id"
    ).fetchall()
    return {row["pluggy_id"]: row["value"] for row in rows}


def category_totals(conn: Conn) -> dict[str, Decimal]:
    rows = conn.execute("SELECT category, SUM(value) AS total FROM spending.expenses GROUP BY category").fetchall()
    return {row["category"]: row["total"] for row in rows}


def resend_purchase(fake: FakePluggy) -> None:
    """Pluggy sends ``buy`` again under a new id and another instant: a probable duplicate (#114)."""
    again = tx("buy-again", CARD, date(2026, 10, 14), "75.30", category=CLOTHING, at="12:00:00.001")
    fake.set_transactions(CARD, *[again if line.id == "buy" else line for line in fake.transactions[CARD]])


class TestSyncStoresKinds:
    def test_every_line_is_classified_by_the_sync(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        assert kinds(conn) == {
            "back": "abatimento",
            "buy": "gasto",
            "debit": "pagamento_fatura",
            "lookalike": "gasto",
            "own": "transferencia_propria",
            "paid": "pagamento_fatura",
            "redeem": "investimento",
            "refund": "abatimento",
            "rent": "gasto",
            "shirt": "gasto",
        }

    def test_the_bill_payment_is_paired_on_both_sides(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        rows = conn.execute(
            "SELECT pluggy_id, paired_transaction_id, auto_reason FROM spending.transactions "
            "WHERE pluggy_id IN ('debit', 'paid') ORDER BY pluggy_id"
        ).fetchall()
        debit, paid = rows
        assert debit["paired_transaction_id"] == local_id(conn, "paid")
        assert paid["paired_transaction_id"] == local_id(conn, "debit")
        assert debit["auto_reason"] == "pagamento da fatura do cartão com vencimento em 10/11/2026"

    def test_the_analyses_see_spending_and_refunds_only(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        assert expenses(conn) == {
            "back": Decimal("-20.00"),
            "buy": Decimal("75.30"),
            "lookalike": Decimal("1800.80"),
            "refund": Decimal("-75.30"),
            "rent": Decimal("1800.00"),
            "shirt": Decimal("60.00"),
        }

    def test_a_refund_reduces_the_category_and_a_full_one_cancels(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        assert category_totals(conn)["Clothing"] == Decimal("40.00")
        pair = conn.execute(
            "SELECT SUM(e.value) AS total FROM spending.expenses e JOIN spending.transactions t "
            "ON t.id = e.transaction_id WHERE t.pluggy_id IN ('buy', 'refund')"
        ).fetchone()
        assert pair is not None and pair["total"] == Decimal("0.00")

    def test_a_purchase_abroad_counts_in_reais(self, conn: Conn, pluggy: FakePluggy) -> None:
        """``amount`` is in the purchase's currency; the analyses sum ``amountInAccountCurrency``."""
        abroad = [
            tx("usd", CARD, date(2026, 10, 23), "20", currency="USD", in_account_currency="112.40"),
            tx(
                "usd-back",
                CARD,
                date(2026, 10, 24),
                "-5",
                kind="CREDIT",
                operation="ESTORNO",
                currency="USD",
                in_account_currency="-28.10",
            ),
        ]
        pluggy.set_transactions(CARD, *pluggy.transactions[CARD], *abroad)
        sync(conn, pluggy)
        assert {key: value for key, value in expenses(conn).items() if key.startswith("usd")} == {
            "usd": Decimal("112.40"),
            "usd-back": Decimal("-28.10"),
        }

    def test_a_pending_duplicate_stays_out_of_the_analyses(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        resend_purchase(pluggy)
        sync(conn, pluggy, now=NOW + timedelta(days=1), full=True)
        assert kinds(conn)["buy-again"] == "gasto"
        assert "buy-again" not in expenses(conn)

    def test_the_match_is_redone_when_the_other_side_arrives(self, conn: Conn, pluggy: FakePluggy) -> None:
        bank_lines = pluggy.transactions[BANK]
        pluggy.set_transactions(BANK, *[line for line in bank_lines if line.id != "debit"])
        sync(conn, pluggy)
        pluggy.set_transactions(BANK, *bank_lines)
        sync(conn, pluggy, now=NOW + timedelta(days=1))
        assert kinds(conn)["debit"] == "pagamento_fatura"
        row = conn.execute("SELECT paired_transaction_id FROM spending.transactions WHERE pluggy_id = 'paid'")
        assert row.fetchone() == {"paired_transaction_id": local_id(conn, "debit")}

    def test_a_second_pass_changes_nothing(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        with conn.transaction():
            assert reclassify(conn) == 0

    def test_a_merged_duplicate_is_classified_again(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        resend_purchase(pluggy)
        sync(conn, pluggy, now=NOW + timedelta(days=1), full=True)
        suggestion = conn.execute("SELECT id FROM spending.duplicate_suggestions").fetchone()
        assert suggestion is not None
        confirm_duplicate(conn, suggestion["id"])
        assert kinds(conn)["buy-again"] == "gasto"
        assert "buy" not in kinds(conn)


class TestManualKind:
    def test_a_kind_set_by_hand_survives_the_sync(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        rent = local_id(conn, "rent")
        after = set_kind(conn, rent, Kind.IGNORED, now=NOW)
        assert (after.kind, after.manual_kind, after.auto_kind) == ("ignorada", "ignorada", "gasto")
        sync(conn, pluggy, now=NOW + timedelta(days=1), full=True)
        assert kinds(conn)["rent"] == "ignorada"
        assert "rent" not in expenses(conn)

    def test_changing_the_kind_moves_a_line_into_the_analyses(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        set_kind(conn, local_id(conn, "own"), Kind.SPENDING, now=NOW)
        sync(conn, pluggy, now=NOW + timedelta(days=1))
        assert expenses(conn)["own"] == Decimal("500.00")

    def test_auto_hands_it_back_to_the_rules(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        rent = local_id(conn, "rent")
        set_kind(conn, rent, Kind.IGNORED, now=NOW)
        after = set_kind(conn, rent, None, now=NOW)
        assert (after.kind, after.manual_kind) == ("gasto", None)
        row = conn.execute("SELECT manual_kind_at FROM spending.transactions WHERE id = %s", (rent,)).fetchone()
        assert row == {"manual_kind_at": None}

    def test_a_kind_set_by_hand_survives_a_new_pluggy_id(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        set_kind(conn, local_id(conn, "rent"), Kind.IGNORED, now=NOW)
        renamed = [
            tx(
                "rent-2",
                BANK,
                date(2026, 11, 11),
                "-1800",
                category=TRANSFERS,
                operation="PIX",
                payment_data=pix("111.222.333-44"),
                at="16:00:00.000",
            )
            if line.id == "rent"
            else line
            for line in pluggy.transactions[BANK]
        ]
        pluggy.set_transactions(BANK, *renamed)
        sync(conn, pluggy, now=NOW + timedelta(days=1), full=True)
        assert kinds(conn)["rent-2"] == "ignorada"

    def test_a_kind_set_on_the_newer_side_survives_the_merge(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        resend_purchase(pluggy)
        sync(conn, pluggy, now=NOW + timedelta(days=1), full=True)
        set_kind(conn, local_id(conn, "buy-again"), Kind.IGNORED, now=NOW)
        suggestion = conn.execute("SELECT id FROM spending.duplicate_suggestions").fetchone()
        assert suggestion is not None
        confirm_duplicate(conn, suggestion["id"])
        row = conn.execute(
            "SELECT kind, manual_kind_at FROM spending.transactions WHERE pluggy_id = 'buy-again'"
        ).fetchone()
        assert row == {"kind": "ignorada", "manual_kind_at": NOW}
        assert "buy-again" not in expenses(conn)

    def test_the_older_sides_kind_wins_the_merge(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        set_kind(conn, local_id(conn, "buy"), Kind.SPENDING, now=NOW)
        resend_purchase(pluggy)
        sync(conn, pluggy, now=NOW + timedelta(days=1), full=True)
        set_kind(conn, local_id(conn, "buy-again"), Kind.IGNORED, now=NOW + timedelta(days=1))
        suggestion = conn.execute("SELECT id FROM spending.duplicate_suggestions").fetchone()
        assert suggestion is not None
        confirm_duplicate(conn, suggestion["id"])
        row = conn.execute(
            "SELECT kind, manual_kind_at FROM spending.transactions WHERE pluggy_id = 'buy-again'"
        ).fetchone()
        assert row == {"kind": "gasto", "manual_kind_at": NOW}

    def test_an_unknown_transaction_is_refused(self, conn: Conn) -> None:
        with pytest.raises(ValidationError, match="Transação 999 não encontrada"):
            set_kind(conn, 999, Kind.IGNORED, now=NOW)

    def test_the_database_refuses_an_unknown_kind(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE spending.transactions SET manual_kind = 'outra' WHERE pluggy_id = 'rent'")


class TestParseKind:
    @pytest.mark.parametrize("text", ["ignorada", " IGNORADA ", "Ignorada"])
    def test_reads_a_kind(self, text: str) -> None:
        assert parse_kind(text) == Kind.IGNORED

    def test_refuses_anything_else_listing_the_kinds(self) -> None:
        with pytest.raises(ValidationError, match="gasto, receita, transferencia_propria"):
            parse_kind("transferencia")
