"""Tests for the spending sync (issue #114), against the test database and a fake Pluggy.

Covers the criteria of the issue and of its benchmark comment: two syncs in a
row duplicate nothing, a future installment that vanishes is pruned, a recent
sync does not call the API, a renewed consent duplicates nothing, a short
``dateTo`` prunes nothing outside it, and every account exposes its last sync,
last transaction and first one.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from psycopg.rows import DictRow

from bogle.domain.errors import (
    NetworkError,
    PluggyError,
    PluggyNotConfiguredError,
    SyncInProgressError,
    ValidationError,
)
from bogle.settings import SPENDING_LAST_SYNC_AT, get_setting, set_value
from bogle.spending import repository
from bogle.spending.models import SyncReport, SyncWindow
from bogle.spending.sync import confirm_duplicate, reject_duplicate, run_sync, sync_if_due, sync_now
from tests.conftest import TEST_DATABASE_URL
from tests.pluggy_fakes import ENV, ITEM, ITEM_NEW, NOW, FakePluggy, account, bill, item, no_client, tx

Conn = psycopg.Connection[DictRow]

CARD = "acc-card"
BANK = "acc-bank"
D = date(2026, 11, 20)


@pytest.fixture
def pluggy() -> FakePluggy:
    fake = FakePluggy()
    fake.add_item(item(), account(CARD, name="Cartão"), account(BANK, kind="BANK", number="12345-6", name="Conta"))
    fake.set_transactions(
        CARD,
        tx("c1", CARD, date(2026, 10, 5), "50", provider_id="pc1", bill_id="bill-1"),
        tx("c2", CARD, date(2026, 11, 20), "30", provider_id="pc2", at="18:00:00"),
        tx("p1", CARD, date(2026, 12, 20), "100", status="PENDING", description="Loja 2/3"),
        tx("p2", CARD, date(2027, 1, 20), "100", status="PENDING", description="Loja 3/3"),
    )
    fake.set_bills(CARD, bill("bill-1", date(2026, 11, 10), "50"))
    fake.set_transactions(BANK, tx("b1", BANK, date(2026, 9, 1), "-20", provider_id="pb1"))
    return fake


def sync(
    conn: Conn, fake: FakePluggy, *, now: datetime = NOW, items: tuple[str, ...] = (ITEM,), **kw: Any
) -> SyncReport:
    return run_sync(conn, fake, items, now=now, **kw)


def lines(conn: Conn) -> list[tuple[Any, ...]]:
    rows = conn.execute(
        "SELECT t.pluggy_id, a.pluggy_id AS account, t.status, t.local_date, t.amount "
        "FROM spending.transactions t JOIN spending.accounts a ON a.id = t.account_id ORDER BY t.pluggy_id"
    ).fetchall()
    return [tuple(row.values()) for row in rows]


def ids(conn: Conn) -> list[str]:
    return [line[0] for line in lines(conn)]


def account_row(conn: Conn, pluggy_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM spending.accounts WHERE pluggy_id = %s OR %s = ANY(replaced_pluggy_ids)", (pluggy_id, pluggy_id)
    ).fetchone()
    assert row is not None
    return dict(row)


def counts(conn: Conn) -> dict[str, int]:
    tables = ("items", "categories", "accounts", "transactions", "bills", "duplicate_suggestions")
    return {
        table: conn.execute(f"SELECT count(*) AS n FROM spending.{table}").fetchone()["n"]  # type: ignore[index]
        for table in tables
    }


def by_label(report: SyncReport) -> dict[str, Any]:
    return {entry.label: entry for entry in report.accounts}


# ------------------------------------------------------------ idempotencia


class TestIdempotent:
    def test_a_first_sync_writes_everything(self, conn: Conn, pluggy: FakePluggy) -> None:
        report = sync(conn, pluggy)
        assert report.failures == []
        assert counts(conn) == {
            "items": 1,
            "categories": 1,
            "accounts": 2,
            "transactions": 5,
            "bills": 1,
            "duplicate_suggestions": 0,
        }
        assert by_label(report)["Cartão"].inserted == 4
        assert get_setting(conn, SPENDING_LAST_SYNC_AT) == NOW

    def test_twice_in_a_row_duplicates_nothing(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        before, before_lines = counts(conn), lines(conn)
        report = sync(conn, pluggy, now=NOW + timedelta(hours=1))
        assert counts(conn) == before
        assert lines(conn) == before_lines
        assert all(entry.inserted == 0 for entry in report.accounts)
        # Incremental: o c1 (05/10) ficou antes da janela e nem foi pedido.
        assert by_label(report)["Cartão"].updated == 3

    def test_the_fields_are_stored(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        row = conn.execute("SELECT * FROM spending.transactions WHERE pluggy_id = 'c1'").fetchone()
        assert row is not None
        assert row["amount"] == Decimal("50")
        assert row["bill_id"] == "bill-1"
        assert row["provider_id"] == "pc1"
        assert row["category_id"] == "07000000"
        assert row["raw"]["id"] == "c1"
        card = account_row(conn, CARD)
        assert card["credit_brand"] == "MASTERCARD"
        assert card["item_id"] == ITEM

    def test_the_client_is_asked_for_cards_bills_only(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        assert pluggy.requests("list_bills") == [("list_bills", CARD)]


# ----------------------------------------------------------------- PENDING


class TestPending:
    def test_a_vanished_installment_is_pruned(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        pluggy.transactions[CARD] = [row for row in pluggy.transactions[CARD] if row.id != "p2"]
        report = sync(conn, pluggy, now=NOW + timedelta(hours=25))
        assert "p2" not in ids(conn)
        assert by_label(report)["Cartão"].removed_pending == 1

    def test_pending_to_posted_with_the_same_id_updates(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        pluggy.transactions[CARD] = [
            tx("p1", CARD, date(2026, 12, 20), "100", status="POSTED") if row.id == "p1" else row
            for row in pluggy.transactions[CARD]
        ]
        report = sync(conn, pluggy, now=NOW + timedelta(hours=25))
        assert ("p1", CARD, "POSTED", date(2026, 12, 20), Decimal("100.00")) in lines(conn)
        assert len(lines(conn)) == 5
        assert by_label(report)["Cartão"].inserted == 0

    def test_pending_to_posted_with_a_new_id_replaces(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        pluggy.transactions[CARD] = [
            tx("p1-posted", CARD, date(2026, 12, 20), "100", description="Loja 2/3") if row.id == "p1" else row
            for row in pluggy.transactions[CARD]
        ]
        sync(conn, pluggy, now=NOW + timedelta(hours=25))
        assert "p1" not in ids(conn)
        assert ("p1-posted", CARD, "POSTED", date(2026, 12, 20), Decimal("100.00")) in lines(conn)
        assert counts(conn)["duplicate_suggestions"] == 0  # a PENDING saiu: nada a sugerir

    def test_an_empty_answer_prunes_nothing(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        pluggy.transactions[CARD] = []
        report = sync(conn, pluggy, now=NOW + timedelta(hours=25))
        assert {"p1", "p2"} <= set(ids(conn))
        assert by_label(report)["Cartão"].removed_pending == 0

    def test_a_short_date_to_prunes_nothing_outside_it(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        window = SyncWindow(date(2026, 10, 1), date(2026, 12, 31))
        pluggy.calls.clear()
        sync(conn, pluggy, now=NOW + timedelta(hours=25), window=window)
        # A parcela de janeiro nao veio (fora do dateTo + 2 dias) e continua la.
        assert "p2" in ids(conn)
        requests = {call[1]: call[2:] for call in pluggy.requests("list_transactions")}
        assert requests[CARD] == (date(2026, 9, 29), date(2027, 1, 2))

    def test_a_pending_line_outside_the_logical_window_stays(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        # Dentro da margem de 2 dias do pedido, mas fora da janela logica.
        pluggy.transactions[CARD] = [tx("c2", CARD, date(2026, 11, 20), "30", provider_id="pc2", at="18:00:00")]
        sync(conn, pluggy, now=NOW + timedelta(hours=25), window=SyncWindow(date(2026, 11, 1), date(2026, 12, 19)))
        assert {"p1", "p2"} <= set(ids(conn))


# ------------------------------------------------------------- incremental


class TestIncremental:
    def test_first_asks_for_everything_then_35_days_back(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        assert {call[2:] for call in pluggy.requests("list_transactions")} == {(None, None)}
        pluggy.calls.clear()
        # O ultimo sync foi em 27/11 (08:00 em Sao Paulo): 27/11 - 35 d - 2 d.
        sync(conn, pluggy, now=NOW + timedelta(hours=25))
        requests = {call[1]: call[2:] for call in pluggy.requests("list_transactions")}
        assert requests[BANK] == (date(2026, 10, 21), None)

    def test_an_old_pending_line_pulls_date_from_back(self, conn: Conn, pluggy: FakePluggy) -> None:
        pluggy.transactions[BANK].append(tx("bp", BANK, date(2026, 8, 1), "-5", status="PENDING"))
        sync(conn, pluggy)
        pluggy.calls.clear()
        sync(conn, pluggy, now=NOW + timedelta(hours=25))
        requests = {call[1]: call[2:] for call in pluggy.requests("list_transactions")}
        assert requests[BANK] == (date(2026, 7, 30), None)

    def test_full_asks_for_everything_again(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        pluggy.calls.clear()
        sync(conn, pluggy, now=NOW + timedelta(hours=25), full=True)
        assert {call[2:] for call in pluggy.requests("list_transactions")} == {(None, None)}


# ------------------------------------------------------ frescor e cobertura


class TestFreshness:
    def test_each_account_has_last_sync_last_and_first_transaction(self, conn: Conn, pluggy: FakePluggy) -> None:
        report = sync(conn, pluggy)
        card = by_label(report)["Cartão"]
        assert card.last_synced_at == NOW
        # As parcelas futuras nao sao a "ultima transacao".
        assert card.last_transaction_date == date(2026, 11, 20)
        assert card.first_transaction_date == date(2026, 10, 5)
        assert account_row(conn, BANK)["first_transaction_date"] == date(2026, 9, 1)

    def test_coverage_never_moves_forward(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        pluggy.transactions[CARD] = [row for row in pluggy.transactions[CARD] if row.id != "c1"]
        sync(conn, pluggy, now=NOW + timedelta(hours=25))
        assert account_row(conn, CARD)["first_transaction_date"] == date(2026, 10, 5)

    def test_an_account_that_failed_keeps_its_last_sync(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        later = NOW + timedelta(hours=25)
        pluggy.fail[f"list_transactions:{CARD}"] = NetworkError("pluggy", "sem rede")
        report = sync(conn, pluggy, now=later)
        assert account_row(conn, CARD)["last_synced_at"] == NOW
        assert account_row(conn, BANK)["last_synced_at"] == later
        assert [failure.subject for failure in report.failures] == ["conta Cartão"]
        assert get_setting(conn, SPENDING_LAST_SYNC_AT) == later

    def test_status_lists_each_account(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        statuses = {entry.label: entry for entry in repository.account_statuses(conn)}
        assert statuses["Conta"].last_transaction_date == date(2026, 9, 1)
        assert statuses["Cartão"].first_transaction_date == date(2026, 10, 5)
        assert statuses["Cartão"].last_synced_at == NOW


# ------------------------------------------------------------------ guardas


class TestGuards:
    def test_an_expired_consent_is_skipped_without_touching_anything(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        before = lines(conn)
        later = NOW + timedelta(hours=25)
        pluggy.items[ITEM] = item(consent_expires_at=NOW)
        pluggy.transactions[CARD] = []
        pluggy.calls.clear()
        report = sync(conn, pluggy, now=later)
        assert lines(conn) == before
        assert pluggy.requests("list_transactions") == []
        assert report.notices == ["conexão Cartão, Conta ignorada: consentimento vencido"]
        assert get_setting(conn, SPENDING_LAST_SYNC_AT) == NOW  # nada aplicou
        state = repository.item_states(conn)[0]
        assert state.checked_at == later  # o estado foi gravado mesmo assim

    def test_login_error_is_skipped(self, conn: Conn, pluggy: FakePluggy) -> None:
        pluggy.items[ITEM] = item(status="LOGIN_ERROR")
        report = sync(conn, pluggy)
        assert counts(conn)["transactions"] == 0
        assert report.notices == ["conexão MeuPluggy ignorada: erro de login"]

    def test_a_network_failure_in_one_account_spares_the_other(self, conn: Conn, pluggy: FakePluggy) -> None:
        pluggy.fail[f"list_bills:{CARD}"] = NetworkError("pluggy", "sem rede")
        report = sync(conn, pluggy)
        assert [entry.label for entry in report.accounts] == ["Conta"]
        assert ids(conn) == ["b1"]
        assert report.failures[0].message.startswith("Falha de rede ao acessar pluggy")

    def test_categories_failing_does_not_stop_the_sync(self, conn: Conn, pluggy: FakePluggy) -> None:
        pluggy.fail["list_categories"] = PluggyError("erro da Pluggy: boom (HTTP 500).")
        report = sync(conn, pluggy)
        assert len(report.accounts) == 2
        assert [failure.subject for failure in report.failures] == ["categorias"]

    def test_no_accounts_for_a_known_item_changes_nothing(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        before = lines(conn)
        pluggy.accounts[ITEM] = []
        report = sync(conn, pluggy, now=NOW + timedelta(hours=25))
        assert lines(conn) == before
        assert report.failures[0].message == "a Pluggy não devolveu nenhuma conta"

    def test_an_item_that_fails_is_isolated(self, conn: Conn, pluggy: FakePluggy) -> None:
        pluggy.items[ITEM_NEW] = item(ITEM_NEW)
        pluggy.fail[f"get_item:{ITEM_NEW}"] = NetworkError("pluggy", "sem rede")
        report = sync(conn, pluggy, items=(ITEM, ITEM_NEW))
        assert len(report.accounts) == 2
        assert report.failures[0].subject == f"conexão {ITEM_NEW[:8]}"

    def test_items_no_longer_configured_leave_and_their_accounts_stay(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        pluggy.add_item(item(ITEM_NEW))
        sync(conn, pluggy, now=NOW + timedelta(hours=25), items=(ITEM_NEW,))
        assert [state.id for state in repository.item_states(conn)] == [ITEM_NEW]
        assert account_row(conn, CARD)["item_id"] is None
        assert len(lines(conn)) == 5

    def test_another_sync_holding_the_lock(self, conn: Conn, pluggy: FakePluggy) -> None:
        holder = psycopg.connect(TEST_DATABASE_URL, autocommit=True)
        try:
            holder.execute("SELECT pg_advisory_lock(%s)", (repository.SYNC_LOCK,))
            with pytest.raises(SyncInProgressError):
                sync(conn, pluggy)
        finally:
            holder.close()
        assert pluggy.calls == []
        sync(conn, pluggy)  # solto o lock, o sync segue

    def test_the_lock_is_released_after_a_failure(self, conn: Conn, pluggy: FakePluggy, monkeypatch: Any) -> None:
        def boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("bug")

        monkeypatch.setattr(repository, "upsert_categories", boom)
        with pytest.raises(RuntimeError):
            sync(conn, pluggy)
        monkeypatch.undo()
        sync(conn, pluggy)


# --------------------------------------------------------------- renovacao


def renewed(fake: FakePluggy, *, old_status: str = "LOGIN_ERROR") -> None:
    """The consent was renewed: a new item with the same card under new ids.

    The new item reaches back only to November; October lives only in the old one.
    """
    fake.items[ITEM] = item(status=old_status)
    fake.add_item(
        item(ITEM_NEW, consent_expires_at=NOW + timedelta(days=365)), account("new-card", ITEM_NEW, name="Cartão")
    )
    fake.set_transactions(
        "new-card",
        tx("n-c2", "new-card", date(2026, 11, 20), "30", provider_id="pc2", bill_id="bill-new", at="18:00:00"),
        tx("n-p1", "new-card", date(2026, 12, 20), "100", status="PENDING", description="Loja 2/3"),
        tx("n-p2", "new-card", date(2027, 1, 20), "100", status="PENDING", description="Loja 3/3"),
    )
    fake.set_bills("new-card", bill("bill-new", date(2026, 11, 10), "50"))


class TestRenewal:
    def test_with_provider_id_nothing_is_duplicated(self, conn: Conn, pluggy: FakePluggy) -> None:
        pluggy.accounts[ITEM] = [account(CARD, name="Cartão")]
        sync(conn, pluggy)
        before = account_row(conn, CARD)
        renewed(pluggy)
        report = sync(conn, pluggy, now=NOW + timedelta(hours=25), items=(ITEM, ITEM_NEW))
        after = account_row(conn, "new-card")
        assert after["id"] == before["id"]
        assert after["pluggy_id"] == "new-card"
        assert after["replaced_pluggy_ids"] == [CARD]
        assert after["item_id"] == ITEM_NEW
        [card] = report.accounts
        assert card.inherited
        # c2 -> n-c2 pelo providerId; as duas PENDING pela chave natural.
        assert card.remapped == 3
        # c1 so existe no item antigo e fica.
        assert sorted(ids(conn)) == ["c1", "n-c2", "n-p1", "n-p2"]
        assert counts(conn)["transactions"] == 4
        assert counts(conn)["duplicate_suggestions"] == 0
        assert report.notices == ["conexão Cartão ignorada: erro de login"]

    def test_the_bill_takes_the_new_id_and_the_lines_follow(self, conn: Conn, pluggy: FakePluggy) -> None:
        pluggy.accounts[ITEM] = [account(CARD, name="Cartão")]
        sync(conn, pluggy)
        renewed(pluggy)
        sync(conn, pluggy, now=NOW + timedelta(hours=25), items=(ITEM, ITEM_NEW))
        bills = conn.execute("SELECT pluggy_id FROM spending.bills").fetchall()
        assert [row["pluggy_id"] for row in bills] == ["bill-new"]
        old = conn.execute("SELECT bill_id FROM spending.transactions WHERE pluggy_id = 'c1'").fetchone()
        assert old is not None and old["bill_id"] == "bill-new"

    def test_a_second_sync_after_the_renewal_is_quiet(self, conn: Conn, pluggy: FakePluggy) -> None:
        pluggy.accounts[ITEM] = [account(CARD, name="Cartão")]
        sync(conn, pluggy)
        renewed(pluggy)
        sync(conn, pluggy, now=NOW + timedelta(hours=25), items=(ITEM_NEW,))
        before = lines(conn)
        report = sync(conn, pluggy, now=NOW + timedelta(hours=50), items=(ITEM_NEW,))
        assert lines(conn) == before
        assert not report.accounts[0].inherited

    def test_without_provider_id_new_lines_are_suggested_duplicates(self, conn: Conn) -> None:
        fake = FakePluggy()
        fake.add_item(item(), account(CARD, name="Cartão"))
        fake.set_transactions(CARD, tx("c1", CARD, D, "30", description="Padaria"), tx("c2", CARD, D, "12"))
        sync(conn, fake)
        fake.items[ITEM] = item(status="LOGIN_ERROR")
        fake.add_item(item(ITEM_NEW, consent_expires_at=NOW + timedelta(days=365)), account("new-card", ITEM_NEW))
        fake.set_transactions(
            "new-card",
            tx("n1", "new-card", D, "30", description="PADARIA"),
            tx("n3", "new-card", D, "99", description="Outra"),
        )
        report = sync(conn, fake, now=NOW + timedelta(hours=25), items=(ITEM, ITEM_NEW))
        assert report.accounts[0].suggested == 1
        assert report.pending_duplicates == 1
        newer = conn.execute("SELECT id FROM spending.transactions WHERE pluggy_id = 'n1'").fetchone()
        assert newer is not None
        assert repository.suspected_duplicate_ids(conn) == {newer["id"]}

    def suggested(self, conn: Conn) -> tuple[int, int, int]:
        fake = FakePluggy()
        fake.add_item(item(), account(CARD, name="Cartão"))
        fake.set_transactions(CARD, tx("c1", CARD, D, "30", description="Padaria"))
        sync(conn, fake)
        fake.items[ITEM] = item(status="LOGIN_ERROR")
        fake.add_item(item(ITEM_NEW, consent_expires_at=NOW + timedelta(days=365)), account("new-card", ITEM_NEW))
        # A descricao mudou de caixa: a chave natural nao casa, e a sobra vira sugestao.
        fake.set_transactions("new-card", tx("n1", "new-card", D, "30", description="PADARIA"))
        sync(conn, fake, now=NOW + timedelta(hours=25), items=(ITEM, ITEM_NEW))
        row = conn.execute("SELECT id, transaction_id, duplicate_of_id FROM spending.duplicate_suggestions").fetchone()
        assert row is not None
        self.fake = fake
        return row["id"], row["transaction_id"], row["duplicate_of_id"]

    def test_confirming_merges_into_the_older_line(self, conn: Conn) -> None:
        suggestion, newer, older = self.suggested(conn)
        confirm_duplicate(conn, suggestion)
        rows = conn.execute("SELECT id, pluggy_id FROM spending.transactions").fetchall()
        assert [(row["id"], row["pluggy_id"]) for row in rows] == [(older, "n1")]
        assert counts(conn)["duplicate_suggestions"] == 0
        assert repository.suspected_duplicate_ids(conn) == set()
        # O proximo sync casa pelo id novo: nada volta.
        sync(conn, self.fake, now=NOW + timedelta(hours=50), items=(ITEM_NEW,))
        assert ids(conn) == ["n1"]
        assert newer != older

    def test_rejecting_keeps_both_for_good(self, conn: Conn) -> None:
        suggestion, newer, _older = self.suggested(conn)
        reject_duplicate(conn, suggestion)
        assert sorted(ids(conn)) == ["c1", "n1"]
        assert repository.suspected_duplicate_ids(conn) == set()
        sync(conn, self.fake, now=NOW + timedelta(hours=50), items=(ITEM_NEW,), full=True)
        status = conn.execute("SELECT status FROM spending.duplicate_suggestions").fetchall()
        assert [row["status"] for row in status] == ["REJECTED"]
        assert newer in {row["id"] for row in conn.execute("SELECT id FROM spending.transactions").fetchall()}

    def test_resolving_twice_is_an_error(self, conn: Conn) -> None:
        suggestion, _newer, _older = self.suggested(conn)
        reject_duplicate(conn, suggestion)
        with pytest.raises(ValidationError, match="já resolvida"):
            reject_duplicate(conn, suggestion)
        with pytest.raises(ValidationError, match="já resolvida"):
            confirm_duplicate(conn, suggestion)


REFUND = "00:00:00.001"
"""Os estornos de IOF do cartao chegam a 00:00:00.001Z."""


def card_lines(prefix: str, account_id: str) -> list[Any]:
    """A card as Meu Pluggy sends it: no providerId, four identical refunds, an installment."""
    refunds = [
        tx(f"{prefix}-r{index}", account_id, D, "-1.23", description="Estorno IOF", at=REFUND, post_date=D)
        for index in range(4)
    ]
    return [
        tx(f"{prefix}-a", account_id, D, "30", description="Padaria", at="03:00:00.000", post_date=D),
        tx(f"{prefix}-b", account_id, date(2026, 11, 10), "2.5", description="Ajuste", at="12:00:00.001"),
        *refunds,
        tx(
            f"{prefix}-p",
            account_id,
            date(2027, 1, 20),
            "100",
            status="PENDING",
            description="Loja",
            at="03:00:00.000",
            installment=3,
        ),
    ]


class TestWithoutProviderId:
    """What Meu Pluggy really sends (550 of 550 lines without providerId, seen 2026-11-27)."""

    def test_identical_refunds_are_kept_as_many_as_they_are(self, conn: Conn) -> None:
        fake = FakePluggy()
        fake.add_item(item(), account(CARD, name="Cartão"))
        fake.set_transactions(CARD, *card_lines("old", CARD))
        sync(conn, fake)
        refunds = conn.execute(
            "SELECT count(*) AS n FROM spending.transactions WHERE description = 'Estorno IOF'"
        ).fetchone()
        assert refunds is not None and refunds["n"] == 4
        assert counts(conn)["duplicate_suggestions"] == 0

    def test_ids_changing_inside_the_item_are_remapped(self, conn: Conn) -> None:
        fake = FakePluggy()
        fake.add_item(item(), account(CARD, name="Cartão"))
        fake.set_transactions(CARD, *card_lines("old", CARD))
        sync(conn, fake)
        local_ids = [row["id"] for row in conn.execute("SELECT id FROM spending.transactions ORDER BY id").fetchall()]
        fake.set_transactions(CARD, *card_lines("new", CARD))
        report = sync(conn, fake, now=NOW + timedelta(hours=25), full=True)
        assert by_label(report)["Cartão"].remapped == 7
        assert by_label(report)["Cartão"].inserted == 0
        assert by_label(report)["Cartão"].removed_pending == 0
        rows = conn.execute("SELECT id, pluggy_id FROM spending.transactions ORDER BY id").fetchall()
        assert [row["id"] for row in rows] == local_ids
        assert all(row["pluggy_id"].startswith("new-") for row in rows)
        assert counts(conn)["duplicate_suggestions"] == 0

    def test_a_renewal_duplicates_nothing_and_suggests_nothing(self, conn: Conn) -> None:
        fake = FakePluggy()
        fake.add_item(item(), account(CARD, name="Cartão"))
        fake.set_transactions(CARD, *card_lines("old", CARD))
        sync(conn, fake)
        before = counts(conn)
        fake.items[ITEM] = item(status="LOGIN_ERROR")
        fake.add_item(item(ITEM_NEW, consent_expires_at=NOW + timedelta(days=365)), account("new-card", ITEM_NEW))
        fake.set_transactions("new-card", *card_lines("new", "new-card"))
        report = sync(conn, fake, now=NOW + timedelta(hours=25), items=(ITEM, ITEM_NEW))
        [card] = report.accounts
        assert card.inherited
        assert (card.remapped, card.inserted, card.suggested) == (7, 0, 0)
        assert counts(conn) == before | {"items": 2}
        assert report.pending_duplicates == 0

    def test_one_more_identical_refund_is_a_new_line_not_a_duplicate(self, conn: Conn) -> None:
        fake = FakePluggy()
        fake.add_item(item(), account(CARD, name="Cartão"))
        fake.set_transactions(CARD, *card_lines("old", CARD))
        sync(conn, fake)
        extra = tx("old-r4", CARD, D, "-1.23", description="Estorno IOF", at=REFUND, post_date=D)
        fake.set_transactions(CARD, *card_lines("new", CARD), extra)
        report = sync(conn, fake, now=NOW + timedelta(hours=25), full=True)
        assert (by_label(report)["Cartão"].inserted, by_label(report)["Cartão"].suggested) == (1, 0)
        refunds = conn.execute(
            "SELECT count(*) AS n FROM spending.transactions WHERE description = 'Estorno IOF'"
        ).fetchone()
        assert refunds is not None and refunds["n"] == 5

    def test_what_has_no_exact_match_becomes_a_suggestion(self, conn: Conn) -> None:
        fake = FakePluggy()
        fake.add_item(item(), account(CARD, name="Cartão"))
        fake.set_transactions(CARD, tx("old", CARD, D, "30", description="Padaria", at="03:00:00.000"))
        sync(conn, fake)
        # O instante mudou: a chave natural nao casa, mas dia, valor e descricao sim.
        fake.set_transactions(CARD, tx("new", CARD, D, "30", description="Padaria", at="03:00:00.001"))
        report = sync(conn, fake, now=NOW + timedelta(hours=25), full=True)
        assert by_label(report)["Cartão"].suggested == 1
        assert report.pending_duplicates == 1

    def test_the_card_day_is_the_bill_post_date(self, conn: Conn) -> None:
        fake = FakePluggy()
        fake.add_item(item(), account(CARD, name="Cartão"))
        # 02:00Z de 21/11 e 23:00 de 20/11 em Sao Paulo, mas a fatura diz 21/11.
        fake.set_transactions(CARD, tx("c", CARD, date(2026, 11, 21), at="02:00:00.000", post_date=date(2026, 11, 21)))
        sync(conn, fake)
        assert lines(conn)[0][3] == date(2026, 11, 21)


class TestRenewalEdges:
    def test_two_live_items_the_longer_consent_wins(self, conn: Conn, pluggy: FakePluggy) -> None:
        pluggy.accounts[ITEM] = [account(CARD, name="Cartão")]
        sync(conn, pluggy)
        renewed(pluggy, old_status="UPDATED")
        pluggy.calls.clear()
        report = sync(conn, pluggy, now=NOW + timedelta(hours=25), items=(ITEM, ITEM_NEW))
        assert report.notices == ["conexão Cartão ignorada: mesmas contas de MeuPluggy, com consentimento mais longo"]
        assert report.accounts[0].inherited
        assert counts(conn)["accounts"] == 1
        assert ("list_transactions", CARD, None, None) not in pluggy.calls

    def test_an_old_id_coming_back_is_skipped(self, conn: Conn, pluggy: FakePluggy) -> None:
        pluggy.accounts[ITEM] = [account(CARD, name="Cartão")]
        sync(conn, pluggy)
        renewed(pluggy)
        sync(conn, pluggy, now=NOW + timedelta(hours=25), items=(ITEM, ITEM_NEW))
        before = lines(conn)
        pluggy.items[ITEM] = item(status="UPDATED", consent_expires_at=NOW + timedelta(days=500))
        report = sync(conn, pluggy, now=NOW + timedelta(hours=50), items=(ITEM,))
        assert report.notices == ["conexão MeuPluggy ignorada: substituída por uma renovação do consentimento"]
        assert lines(conn) == before
        assert counts(conn)["accounts"] == 1

    def test_ambiguous_identity_creates_a_new_account(self, conn: Conn) -> None:
        fake = FakePluggy()
        fake.add_item(item(ITEM_NEW), account("new-card", ITEM_NEW, name="Cartão"))
        fake.add_item(item())
        conn.execute("INSERT INTO spending.items (id, status, checked_at) VALUES (%s, 'UPDATED', now())", (ITEM,))
        for old in ("old-1", "old-2"):
            repository.insert_account(conn, account(old, ITEM), ITEM)
        report = sync(conn, fake, items=(ITEM_NEW,))
        assert report.notices == ["conta Cartão gravada como nova: mais de uma conta antiga com os mesmos dados"]
        assert counts(conn)["accounts"] == 3
        assert not report.accounts[0].inherited

    def test_another_owner_is_never_inherited(self, conn: Conn, pluggy: FakePluggy) -> None:
        pluggy.accounts[ITEM] = [account(CARD, name="Cartão", tax_number="111")]
        sync(conn, pluggy)
        pluggy.items[ITEM] = item(status="LOGIN_ERROR")
        pluggy.add_item(item(ITEM_NEW), account("new-card", ITEM_NEW, tax_number="222"))
        report = sync(conn, pluggy, now=NOW + timedelta(hours=25), items=(ITEM, ITEM_NEW))
        assert not report.accounts[0].inherited
        assert counts(conn)["accounts"] == 2


# ----------------------------------------------------- sync automatico


def connect() -> Conn:
    from bogle.db import get_connection

    return get_connection(TEST_DATABASE_URL)


class TestSyncIfDue:
    def test_a_recent_sync_calls_nothing(self, conn: Conn) -> None:
        set_value(conn, SPENDING_LAST_SYNC_AT, NOW - timedelta(hours=23))
        auto = sync_if_due(now=NOW, env=ENV, connect=connect, client_factory=no_client)
        assert not auto.ran
        assert auto.notice is None

    def test_a_recent_sync_does_not_even_read_the_configuration(self, conn: Conn) -> None:
        set_value(conn, SPENDING_LAST_SYNC_AT, NOW - timedelta(hours=1))
        auto = sync_if_due(now=NOW, env={}, connect=connect, client_factory=no_client)
        assert auto == sync_if_due(now=NOW, env=ENV, connect=connect, client_factory=no_client)
        assert auto.notice is None

    def test_past_24_hours_it_syncs(self, conn: Conn, pluggy: FakePluggy) -> None:
        set_value(conn, SPENDING_LAST_SYNC_AT, NOW - timedelta(hours=25))
        auto = sync_if_due(now=NOW, env=ENV, connect=connect, client_factory=pluggy.factory())
        assert auto.ran
        assert auto.notice is None
        assert auto.report is not None and len(auto.report.accounts) == 2
        assert ("factory", "client-id") in pluggy.calls
        assert pluggy.closed == 1
        assert get_setting(conn, SPENDING_LAST_SYNC_AT) == NOW

    def test_never_synced_it_syncs(self, conn: Conn, pluggy: FakePluggy) -> None:
        assert sync_if_due(now=NOW, env=ENV, connect=connect, client_factory=pluggy.factory()).ran

    def test_without_configuration_it_names_what_is_missing(self, conn: Conn) -> None:
        auto = sync_if_due(now=NOW, env={}, connect=connect, client_factory=no_client)
        assert not auto.ran
        assert auto.notice == (
            "gastos não sincronizados (faltam PLUGGY_CLIENT_ID, PLUGGY_CLIENT_SECRET, PLUGGY_ITEM_IDS no ambiente "
            "(defina ou rode com op run))"
        )

    def test_a_failure_says_how_old_the_data_is(self, conn: Conn, pluggy: FakePluggy) -> None:
        set_value(conn, SPENDING_LAST_SYNC_AT, NOW - timedelta(days=2))  # 25/11 08:00 em Sao Paulo
        pluggy.fail[f"get_item:{ITEM}"] = NetworkError("pluggy", "sem rede")
        auto = sync_if_due(now=NOW, env=ENV, connect=connect, client_factory=pluggy.factory())
        assert auto.notice is not None
        assert auto.notice.startswith("gastos não sincronizados (conexão ")
        assert auto.notice.endswith("; dados de 25/11 08:00")
        assert auto.problem == auto.notice
        assert get_setting(conn, SPENDING_LAST_SYNC_AT) == NOW - timedelta(days=2)

    def test_a_partial_failure_is_said_and_the_mark_moves(self, conn: Conn, pluggy: FakePluggy) -> None:
        pluggy.fail[f"list_transactions:{CARD}"] = NetworkError("pluggy", "sem rede")
        auto = sync_if_due(now=NOW, env=ENV, connect=connect, client_factory=pluggy.factory())
        assert auto.notice is not None and auto.notice.startswith("gastos sincronizados em parte (conta Cartão: ")
        assert auto.problem == auto.notice
        assert get_setting(conn, SPENDING_LAST_SYNC_AT) == NOW

    def test_a_skipped_connection_is_said_but_is_no_problem(self, conn: Conn, pluggy: FakePluggy) -> None:
        pluggy.add_item(item(ITEM_NEW, status="LOGIN_ERROR"))
        auto = sync_if_due(
            now=NOW,
            env=ENV | {"PLUGGY_ITEM_IDS": f"{ITEM},{ITEM_NEW}"},
            connect=connect,
            client_factory=pluggy.factory(),
        )
        assert auto.notice == "conexão MeuPluggy ignorada: erro de login"
        assert auto.problem is None

    def test_a_database_down_never_raises(self) -> None:
        def down() -> Conn:
            raise psycopg.OperationalError("down")

        auto = sync_if_due(now=NOW, env=ENV, connect=down, client_factory=no_client)
        assert not auto.ran
        assert auto.notice is not None and "não foi possível conectar ao banco" in auto.notice


class TestSyncNow:
    def test_without_configuration_it_raises_before_connecting(self) -> None:
        def never() -> Conn:
            raise AssertionError("não deveria conectar")

        with pytest.raises(PluggyNotConfiguredError, match="faltam"):
            sync_now(env={}, connect=never, client_factory=no_client)

    def test_syncs_whatever_the_last_sync(self, conn: Conn, pluggy: FakePluggy) -> None:
        set_value(conn, SPENDING_LAST_SYNC_AT, NOW - timedelta(minutes=5))
        report = sync_now(now=NOW, env=ENV, connect=connect, client_factory=pluggy.factory())
        assert len(report.accounts) == 2
        assert pluggy.closed == 1

    def test_full_is_passed_through(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        pluggy.calls.clear()
        sync_now(full=True, now=NOW + timedelta(hours=1), env=ENV, connect=connect, client_factory=pluggy.factory())
        assert {call[2:] for call in pluggy.requests("list_transactions")} == {(None, None)}
