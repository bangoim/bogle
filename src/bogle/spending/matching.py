"""The pure rules of the spending sync (issue #114): windows, identities, pairings.

Nothing here touches the database or the network, so every rule the sync
depends on is tested on plain values.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Protocol

from bogle.data.pluggy import PluggyTransaction, local_day
from bogle.spending.models import StoredTransaction, SyncWindow

__all__ = [
    "INCREMENTAL_MARGIN",
    "REQUEST_MARGIN",
    "AccountLike",
    "account_identity",
    "default_window",
    "local_day",
    "natural_key",
    "normalize_description",
    "pair_by_natural_key",
    "pair_by_provider_id",
    "pair_duplicates",
    "request_range",
    "same_account",
]

INCREMENTAL_MARGIN = timedelta(days=35)
"""How far before the last sync an incremental one starts again.

A bank may post a line days after it happened, and a card statement closes once
a month: 35 days catches both without asking for the whole history every day.
"""

REQUEST_MARGIN = timedelta(days=2)
"""Extra days asked of Pluggy on each closed end, so a line near the edge (a
time zone, a late posting) is never missing from the answer. Pruning uses the
window without it."""


def default_window(last_synced_at: datetime | None, oldest_pending: date | None, *, full: bool) -> SyncWindow:
    """The window an account syncs with when nobody gave one.

    The whole history for a new account, one that just inherited an older one,
    one never synced, or ``--full``. Otherwise from 35 days before the last sync,
    or from the oldest PENDING line still stored when that is older: a PENDING
    line outside the window could never be confirmed or pruned. ``date_to`` stays
    open, so every future installment comes back and none is pruned for being
    out of range.
    """
    if full or last_synced_at is None:
        return SyncWindow()
    start = local_day(last_synced_at) - INCREMENTAL_MARGIN
    if oldest_pending is not None and oldest_pending < start:
        start = oldest_pending
    return SyncWindow(date_from=start)


def request_range(window: SyncWindow) -> tuple[date | None, date | None]:
    """``dateFrom``/``dateTo`` for Pluggy: the window widened by the margin."""
    start = window.date_from - REQUEST_MARGIN if window.date_from is not None else None
    end = window.date_to + REQUEST_MARGIN if window.date_to is not None else None
    return start, end


class AccountLike(Protocol):
    @property
    def type(self) -> str: ...
    @property
    def subtype(self) -> str: ...
    @property
    def number(self) -> str | None: ...
    @property
    def transfer_number(self) -> str | None: ...
    @property
    def credit_brand(self) -> str | None: ...
    @property
    def tax_number(self) -> str | None: ...


def account_identity(account: AccountLike) -> tuple[str, ...] | None:
    """What makes two Pluggy accounts the same account at the bank.

    Without a number there is no identity: two accounts are never matched on
    type alone.
    """
    if not account.number:
        return None
    if account.type == "BANK":
        return ("BANK", account.subtype, account.number, account.transfer_number or "")
    return (account.type, account.subtype, account.number, account.credit_brand or "")


def same_account(a: AccountLike, b: AccountLike) -> bool:
    """Same identity, and the same owner whenever both say who it is."""
    identity = account_identity(a)
    if identity is None or identity != account_identity(b):
        return False
    return a.tax_number is None or b.tax_number is None or a.tax_number == b.tax_number


_NOT_WORD = re.compile(r"[^0-9a-z]+")


def normalize_description(text: str) -> str:
    """Case, accents and punctuation out: ``Padaria  São João`` == ``PADARIA SAO JOAO``."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return _NOT_WORD.sub(" ", plain).strip()


def pair_by_provider_id(
    stored: Iterable[StoredTransaction], fresh: Iterable[PluggyTransaction]
) -> list[tuple[StoredTransaction, PluggyTransaction]]:
    """Stored lines that came back under a new Pluggy id, matched by ``providerId``.

    The bank's own id survives a renewed consent (and the id changes Pluggy
    makes inside an item). One stored and one fresh line with the same
    providerId pair directly; a providerId repeated on either side pairs only
    where the day and the amount agree too.
    """
    stored_by_id: dict[str, list[StoredTransaction]] = defaultdict(list)
    for row in stored:
        if row.provider_id is not None:
            stored_by_id[row.provider_id].append(row)
    fresh_by_id: dict[str, list[PluggyTransaction]] = defaultdict(list)
    for tx in fresh:
        if tx.provider_id is not None:
            fresh_by_id[tx.provider_id].append(tx)

    pairs: list[tuple[StoredTransaction, PluggyTransaction]] = []
    for provider_id, fresh_rows in fresh_by_id.items():
        stored_rows = stored_by_id.get(provider_id)
        if not stored_rows:
            continue
        if len(stored_rows) == 1 and len(fresh_rows) == 1:
            pairs.append((stored_rows[0], fresh_rows[0]))
            continue
        remaining = list(stored_rows)
        for tx in fresh_rows:
            match = next(
                (row for row in remaining if row.local_date == tx.local_date and row.amount == tx.amount), None
            )
            if match is not None:
                remaining.remove(match)
                pairs.append((match, tx))
    return pairs


NaturalKey = tuple[datetime, Decimal, str, int | None]


def natural_key(row: StoredTransaction | PluggyTransaction) -> NaturalKey:
    """The exact instant, amount, description and installment of a line.

    What identifies a line when Pluggy changed its id and the bank sends no
    providerId (Meu Pluggy sent none, in 550 of 550 lines, seen live
    2026-11-27). Exact on purpose: the instant carries milliseconds, so two
    lines only share a key when they are the same line, or truly repeated ones.
    """
    moment = row.moment if isinstance(row, StoredTransaction) else row.date
    return (moment, row.amount, row.description, row.installment_number)


def pair_by_natural_key(
    stored: Iterable[StoredTransaction], fresh: Iterable[PluggyTransaction]
) -> list[tuple[StoredTransaction, PluggyTransaction]]:
    """Stored lines that came back under a new Pluggy id, matched by :func:`natural_key`.

    One to one, by count: four identical refunds stored and four returned pair
    four times, never more. Repeated lines are legitimate (the card sends
    identical IOF refunds on the same day), so a surplus on either side stays
    unpaired, never merged.
    """
    by_key: dict[NaturalKey, list[StoredTransaction]] = defaultdict(list)
    for row in stored:
        by_key[natural_key(row)].append(row)
    pairs: list[tuple[StoredTransaction, PluggyTransaction]] = []
    for tx in fresh:
        options = by_key.get(natural_key(tx))
        if options:
            pairs.append((options.pop(0), tx))
    return pairs


def _duplicate_key(row: StoredTransaction) -> tuple[date, object, str]:
    return (row.local_date, row.amount, normalize_description(row.description))


def pair_duplicates(
    new: Sequence[StoredTransaction], candidates: Sequence[StoredTransaction]
) -> list[tuple[StoredTransaction, StoredTransaction]]:
    """``(new, older)`` pairs that are probably the same line, one to one.

    Only POSTED lines, only where at least one side lacks a providerId (two
    different providerIds are two transactions), and only on the same day,
    amount and normalized description. These are suggestions for the user to
    confirm, never merged here.
    """
    by_key: dict[tuple[date, object, str], list[StoredTransaction]] = defaultdict(list)
    for row in candidates:
        if row.status == "POSTED":
            by_key[_duplicate_key(row)].append(row)
    pairs: list[tuple[StoredTransaction, StoredTransaction]] = []
    for row in new:
        if row.status != "POSTED":
            continue
        options = by_key.get(_duplicate_key(row), [])
        match = next((option for option in options if row.provider_id is None or option.provider_id is None), None)
        if match is not None:
            options.remove(match)
            pairs.append((row, match))
    return pairs
