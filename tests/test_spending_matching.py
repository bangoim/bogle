"""Tests for the pure rules of the spending sync (issue #114): windows, identities, pairings."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal

from bogle.spending.matching import (
    account_identity,
    default_window,
    natural_key,
    normalize_description,
    pair_by_natural_key,
    pair_by_provider_id,
    pair_duplicates,
    request_range,
    same_account,
)
from bogle.spending.models import StoredTransaction, SyncWindow
from tests.pluggy_fakes import account, tx

LAST = datetime(2026, 11, 26, 11, tzinfo=UTC)
D = date(2026, 11, 18)


def stored(
    row_id: int,
    day: date,
    amount: str = "10",
    *,
    provider_id: str | None = None,
    status: str = "POSTED",
    description: str = "Compra",
    at: time = time(0),
    installment: int | None = None,
) -> StoredTransaction:
    moment = datetime.combine(day, at, tzinfo=UTC)
    return StoredTransaction(
        row_id, f"old-{row_id}", provider_id, day, Decimal(amount), status, description, moment, installment
    )


class TestWindow:
    def test_first_sync_asks_for_everything(self) -> None:
        assert default_window(None, None, full=False) == SyncWindow()
        assert SyncWindow().full

    def test_full_asks_for_everything(self) -> None:
        assert default_window(LAST, None, full=True).full

    def test_incremental_starts_35_days_before_the_last_sync(self) -> None:
        window = default_window(LAST, None, full=False)
        assert window == SyncWindow(date_from=date(2026, 10, 22))

    def test_an_older_pending_line_pulls_the_start_back(self) -> None:
        assert default_window(LAST, date(2026, 9, 1), full=False).date_from == date(2026, 9, 1)
        assert default_window(LAST, date(2026, 11, 1), full=False).date_from == date(2026, 10, 22)

    def test_the_request_is_two_days_wider_on_each_closed_end(self) -> None:
        assert request_range(SyncWindow()) == (None, None)
        assert request_range(SyncWindow(date(2026, 10, 22))) == (date(2026, 10, 20), None)
        assert request_range(SyncWindow(date(2026, 10, 22), date(2026, 12, 31))) == (
            date(2026, 10, 20),
            date(2027, 1, 2),
        )

    def test_contains(self) -> None:
        window = SyncWindow(date(2026, 10, 1), date(2026, 10, 31))
        assert window.contains(date(2026, 10, 1))
        assert window.contains(date(2026, 10, 31))
        assert not window.contains(date(2026, 9, 30))
        assert not window.contains(date(2026, 11, 1))
        assert SyncWindow(date(2026, 10, 1)).contains(date(2030, 1, 1))


class TestAccountIdentity:
    def test_a_card_is_type_number_and_brand(self) -> None:
        assert account_identity(account("a")) == ("CREDIT", "CREDIT_CARD", "9999", "MASTERCARD")

    def test_a_bank_account_carries_the_branch(self) -> None:
        bank = account("b", kind="BANK", number="12345-6")
        assert account_identity(bank) == ("BANK", "CHECKING_ACCOUNT", "12345-6", "0001/12345-6")

    def test_without_a_number_there_is_no_identity(self) -> None:
        assert account_identity(account("a", number=None)) is None
        assert not same_account(account("a", number=None), account("b", number=None))

    def test_same_account_under_new_ids(self) -> None:
        assert same_account(account("old"), account("new"))

    def test_different_owners_are_different_accounts(self) -> None:
        assert not same_account(account("a", tax_number="1"), account("b", tax_number="2"))
        assert same_account(account("a", tax_number=None), account("b", tax_number="2"))

    def test_different_numbers_or_types(self) -> None:
        assert not same_account(account("a"), account("b", number="1111"))
        assert not same_account(account("a", number="9999"), account("b", kind="BANK", number="9999"))


class TestDescription:
    def test_case_accents_and_punctuation_do_not_count(self) -> None:
        assert normalize_description("Padaria  São-João!") == normalize_description("PADARIA SAO JOAO")


class TestProviderIdPairs:
    def test_one_to_one(self) -> None:
        old = stored(1, date(2026, 11, 1), provider_id="p1")
        new = tx("n1", "acc", date(2026, 11, 1), provider_id="p1")
        assert pair_by_provider_id([old], [new]) == [(old, new)]

    def test_without_provider_id_nothing_pairs(self) -> None:
        assert pair_by_provider_id([stored(1, date(2026, 11, 1))], [tx("n1", "acc", date(2026, 11, 1))]) == []

    def test_a_repeated_provider_id_pairs_by_day_and_amount(self) -> None:
        first = stored(1, date(2026, 11, 1), "10", provider_id="p")
        second = stored(2, date(2026, 12, 1), "10", provider_id="p")
        new_second = tx("n2", "acc", date(2026, 12, 1), "10", provider_id="p")
        new_other = tx("n3", "acc", date(2027, 1, 1), "10", provider_id="p")
        assert pair_by_provider_id([first, second], [new_second, new_other]) == [(second, new_second)]


REFUND_AT = time(0, 0, 0, 1000)
"""Os estornos de IOF do cartao chegam a 00:00:00.001Z."""


class TestNaturalKeyPairs:
    def test_the_same_line_under_a_new_id(self) -> None:
        old = stored(1, D, "-1.23", description="Estorno IOF", at=REFUND_AT)
        new = tx("n1", "acc", D, "-1.23", description="Estorno IOF", at="00:00:00.001")
        assert natural_key(old) == natural_key(new)
        assert pair_by_natural_key([old], [new]) == [(old, new)]

    def test_the_key_is_exact(self) -> None:
        old = stored(1, D, "30", description="Padaria", at=time(3))
        assert pair_by_natural_key([old], [tx("n", "acc", D, "30", description="PADARIA", at="03:00:00.000")]) == []
        assert pair_by_natural_key([old], [tx("n", "acc", D, "30", description="Padaria", at="03:00:00.001")]) == []
        assert pair_by_natural_key([old], [tx("n", "acc", D, "31", description="Padaria", at="03:00:00.000")]) == []

    def test_the_installment_is_part_of_the_key(self) -> None:
        old = stored(1, D, "100", description="Loja", installment=2)
        assert pair_by_natural_key([old], [tx("n", "acc", D, "100", description="Loja", installment=3)]) == []
        new = tx("n", "acc", D, "100", description="Loja", installment=2)
        assert pair_by_natural_key([old], [new]) == [(old, new)]

    def test_four_identical_refunds_pair_four_times(self) -> None:
        olds = [stored(index, D, "-1.23", description="Estorno IOF", at=REFUND_AT) for index in range(4)]
        news = [tx(f"n{index}", "acc", D, "-1.23", description="Estorno IOF", at="00:00:00.001") for index in range(4)]
        pairs = pair_by_natural_key(olds, news)
        assert len(pairs) == 4
        assert len({old.id for old, _ in pairs}) == 4
        assert len({new.id for _, new in pairs}) == 4

    def test_a_surplus_on_either_side_stays_unpaired(self) -> None:
        olds = [stored(index, D, "-1.23", description="Estorno IOF", at=REFUND_AT) for index in range(3)]
        news = [tx(f"n{index}", "acc", D, "-1.23", description="Estorno IOF", at="00:00:00.001") for index in range(5)]
        assert len(pair_by_natural_key(olds, news)) == 3
        assert len(pair_by_natural_key(olds, news[:2])) == 2


class TestDuplicatePairs:
    def test_same_day_amount_and_description_without_provider_id(self) -> None:
        older = stored(1, date(2026, 11, 1), description="Mercado Exemplo")
        newer = stored(9, date(2026, 11, 1), description="MERCADO EXEMPLO")
        assert pair_duplicates([newer], [older]) == [(newer, older)]

    def test_one_side_with_provider_id_is_enough(self) -> None:
        older = stored(1, date(2026, 11, 1), provider_id="p1")
        newer = stored(9, date(2026, 11, 1))
        assert pair_duplicates([newer], [older]) == [(newer, older)]

    def test_two_provider_ids_are_two_transactions(self) -> None:
        older = stored(1, date(2026, 11, 1), provider_id="p1")
        newer = stored(9, date(2026, 11, 1), provider_id="p2")
        assert pair_duplicates([newer], [older]) == []

    def test_one_to_one(self) -> None:
        older = stored(1, date(2026, 11, 1))
        first, second = stored(8, date(2026, 11, 1)), stored(9, date(2026, 11, 1))
        assert pair_duplicates([first, second], [older]) == [(first, older)]

    def test_only_posted_lines(self) -> None:
        assert pair_duplicates([stored(9, date(2026, 11, 1), status="PENDING")], [stored(1, date(2026, 11, 1))]) == []
        assert pair_duplicates([stored(9, date(2026, 11, 1))], [stored(1, date(2026, 11, 1), status="PENDING")]) == []

    def test_a_different_amount_or_day(self) -> None:
        older = stored(1, date(2026, 11, 1), "10")
        assert pair_duplicates([stored(9, date(2026, 11, 1), "11")], [older]) == []
        assert pair_duplicates([stored(9, date(2026, 11, 2), "10")], [older]) == []
