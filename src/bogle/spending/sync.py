"""The spending sync: Meu Pluggy into the ``spending`` schema (issue #114).

**Idempotent.** Every line is matched to what is stored by its Pluggy id; a
second run in a row inserts nothing. A line whose Pluggy id changed (inside the
item, or because the consent was renewed and a new item took over) keeps its
local id: it is matched by the bank's ``providerId`` when there is one, else by
its exact natural key (instant, amount, description, installment), one to one,
so repeated lines that are legitimately identical stay as many as they are.
What is still left, a new POSTED line that looks like an unmatched stored one
(same day, amount and description), is kept apart from the analyses as a
probable duplicate, for the user to confirm.

**PENDING lines are replaced.** Future installments come as PENDING and may
change or vanish; after each read, the PENDING lines of the window that did not
come back are deleted. Only inside the window, never on an empty answer, and
POSTED lines are never deleted (an expired consent reads as empty).

**Incremental.** The first sync of an account asks for everything; the next
ones from 35 days before the last sync (:func:`~bogle.spending.matching.default_window`).

**Renewed consent.** A new item returns the same accounts under new ids. An
account nobody returned with the same identity (type, number, branch or card
brand) is inherited: it takes the new id and keeps its history, and its lines
are remapped as above. When the old item is still alive, the one with the
longer consent wins; an item whose accounts were already taken over is skipped.

Each account is fetched in full first and written in one transaction, so a
failure leaves it exactly as it was, and the other accounts go on.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import DictRow

from bogle.data.pluggy import (
    PluggyAccount,
    PluggyBill,
    PluggyCategory,
    PluggyClient,
    PluggyItem,
    PluggyTransaction,
    load_config,
)
from bogle.db import DEFAULT_TIMEZONE, get_connection
from bogle.domain.errors import BogleError, SyncInProgressError, ValidationError
from bogle.error_messages import message_for
from bogle.settings import SPENDING_LAST_SYNC_AT, get_setting, set_value
from bogle.spending import repository
from bogle.spending.matching import (
    default_window,
    pair_by_natural_key,
    pair_by_provider_id,
    pair_duplicates,
    request_range,
    same_account,
)
from bogle.spending.models import (
    AccountReport,
    AutoSync,
    StoredAccount,
    StoredTransaction,
    SyncFailure,
    SyncReport,
    SyncWindow,
    account_label,
)

SYNC_INTERVAL = timedelta(hours=24)
"""Pluggy refreshes a Meu Pluggy item once a day: syncing more often reads the same data."""

_LOCAL_TZ = ZoneInfo(DEFAULT_TIMEZONE)

Connection = psycopg.Connection[DictRow]


class PluggySource(Protocol):
    """What the sync reads from Pluggy: :class:`~bogle.data.pluggy.PluggyClient`, or a fake."""

    def get_item(self, item_id: str) -> PluggyItem: ...
    def list_accounts(self, item_id: str) -> list[PluggyAccount]: ...
    def list_transactions(
        self, account_id: str, *, date_from: date | None = None, date_to: date | None = None
    ) -> list[PluggyTransaction]: ...
    def list_bills(self, account_id: str) -> list[PluggyBill]: ...
    def list_categories(self) -> list[PluggyCategory]: ...
    def close(self) -> None: ...


ClientFactory = Callable[[str, str], PluggySource]


@dataclass(frozen=True, slots=True)
class _Plan:
    """An account to sync: what Pluggy returned, and the stored account it is."""

    fresh: PluggyAccount
    item_id: str
    stored: StoredAccount | None
    inherited: bool

    @property
    def label(self) -> str:
        return account_label(self.fresh.name, self.fresh.marketing_name)


# ------------------------------------------------------------------ motor


def run_sync(
    conn: Connection,
    client: PluggySource,
    item_ids: Sequence[str],
    *,
    now: datetime,
    full: bool = False,
    window: SyncWindow | None = None,
) -> SyncReport:
    """Sync every configured connection; failures are isolated and reported.

    ``window`` overrides the window of every account (``dateTo`` included);
    ``full`` asks for the whole history. The global mark of the automatic sync
    is written only when at least one account applied.
    """
    if not repository.try_lock(conn):
        raise SyncInProgressError()
    try:
        report = SyncReport()
        _sync_categories(conn, client, report)
        accepted = _read_items(conn, client, list(item_ids), now, report)
        for plan in _resolve_accounts(conn, accepted, report):
            account_report = _sync_account(conn, client, plan, now=now, full=full, window=window, report=report)
            if account_report is not None:
                report.accounts.append(account_report)
        if report.applied:
            set_value(conn, SPENDING_LAST_SYNC_AT, now)
        report.pending_duplicates = repository.pending_duplicates(conn)
        return report
    finally:
        repository.unlock(conn)


def _failure(subject: str, exc: BogleError) -> SyncFailure:
    return SyncFailure(subject, message_for(exc).rstrip("."))


def _sync_categories(conn: Connection, client: PluggySource, report: SyncReport) -> None:
    try:
        categories = client.list_categories()
    except BogleError as exc:
        report.failures.append(_failure("categorias", exc))
        return
    repository.upsert_categories(conn, categories)


def _item_label(item_id: str, stored: Sequence[StoredAccount], connector: str | None = None) -> str:
    names = list(dict.fromkeys(account.label for account in stored if account.item_id == item_id))
    return ", ".join(names) or connector or item_id[:8]


def _unreadable(item: PluggyItem, now: datetime) -> str | None:
    """Why a connection cannot be read now, or ``None``.

    An expired consent still answers, with empty lists: reading it would look
    like every line vanished.
    """
    if item.consent_expires_at is not None and item.consent_expires_at <= now:
        return "consentimento vencido"
    if item.status == "LOGIN_ERROR":
        return "erro de login"
    return None


def _read_items(
    conn: Connection, client: PluggySource, item_ids: list[str], now: datetime, report: SyncReport
) -> list[tuple[PluggyItem, list[PluggyAccount]]]:
    """Each connection's state (stored, even when unreadable) and its accounts.

    Returns the connections to sync, the longest consent first. A connection
    whose accounts are already another connection's, or were already taken over
    by a renewal, is skipped with a notice.
    """
    stored = repository.accounts(conn)
    replaced = {old for account in stored for old in account.replaced_pluggy_ids}
    readable: list[tuple[PluggyItem, list[PluggyAccount]]] = []
    for item_id in item_ids:
        label = _item_label(item_id, stored)
        try:
            item = client.get_item(item_id)
        except BogleError as exc:
            report.failures.append(_failure(f"conexão {label}", exc))
            continue
        repository.upsert_item(conn, item, checked_at=now)
        label = _item_label(item_id, stored, item.connector_name)
        reason = _unreadable(item, now)
        if reason is not None:
            report.notices.append(f"conexão {label} ignorada: {reason}")
            continue
        try:
            fresh = client.list_accounts(item_id)
        except BogleError as exc:
            report.failures.append(_failure(f"conexão {label}", exc))
            continue
        if not fresh and any(account.item_id == item_id for account in stored):
            report.failures.append(SyncFailure(f"conexão {label}", "a Pluggy não devolveu nenhuma conta"))
            continue
        if any(account.id in replaced for account in fresh):
            report.notices.append(f"conexão {label} ignorada: substituída por uma renovação do consentimento")
            continue
        readable.append((item, fresh))
    repository.delete_items_except(conn, item_ids)

    def consent(entry: tuple[PluggyItem, list[PluggyAccount]]) -> datetime:
        return entry[0].consent_expires_at or datetime.min.replace(tzinfo=UTC)

    accepted: list[tuple[PluggyItem, list[PluggyAccount]]] = []
    for item, fresh in sorted(readable, key=consent, reverse=True):
        clash = next(
            (other for other, taken in accepted if any(same_account(a, b) for a in fresh for b in taken)), None
        )
        if clash is not None:
            label = _item_label(item.id, stored, item.connector_name)
            other = _item_label(clash.id, stored, clash.connector_name)
            report.notices.append(f"conexão {label} ignorada: mesmas contas de {other}, com consentimento mais longo")
            continue
        accepted.append((item, fresh))
    return accepted


def _resolve_accounts(
    conn: Connection, accepted: list[tuple[PluggyItem, list[PluggyAccount]]], report: SyncReport
) -> list[_Plan]:
    """Match each account Pluggy returned to a stored one: by id, else by identity (renewal)."""
    stored = repository.accounts(conn)
    by_pluggy = {account.pluggy_id: account for account in stored}
    returned = {fresh.id for _, accounts in accepted for fresh in accounts}
    claimed: set[int] = set()
    plans: list[_Plan] = []
    for item, accounts in accepted:
        for fresh in accounts:
            known = by_pluggy.get(fresh.id)
            if known is not None:
                claimed.add(known.id)
                plans.append(_Plan(fresh, item.id, known, inherited=False))
                continue
            candidates = [
                account
                for account in stored
                if account.pluggy_id not in returned and account.id not in claimed and same_account(account, fresh)
            ]
            if len(candidates) == 1:
                claimed.add(candidates[0].id)
                plans.append(_Plan(fresh, item.id, candidates[0], inherited=True))
                continue
            plan = _Plan(fresh, item.id, None, inherited=False)
            if candidates:
                report.notices.append(
                    f"conta {plan.label} gravada como nova: mais de uma conta antiga com os mesmos dados"
                )
            plans.append(plan)
    return plans


def _sync_account(
    conn: Connection,
    client: PluggySource,
    plan: _Plan,
    *,
    now: datetime,
    full: bool,
    window: SyncWindow | None,
    report: SyncReport,
) -> AccountReport | None:
    stored = plan.stored
    if window is None:
        incremental = stored is not None and not plan.inherited
        window = default_window(
            stored.last_synced_at if incremental and stored is not None else None,
            repository.oldest_pending(conn, stored.id) if incremental and stored is not None else None,
            full=full or not incremental,
        )
    date_from, date_to = request_range(window)
    try:
        transactions = client.list_transactions(plan.fresh.id, date_from=date_from, date_to=date_to)
        bills = client.list_bills(plan.fresh.id) if plan.fresh.type == "CREDIT" else []
    except BogleError as exc:
        report.failures.append(_failure(f"conta {plan.label}", exc))
        return None
    with conn.transaction():
        return _apply_account(conn, plan, transactions, bills, window, now)


# ---------------------------------------------------------------- escrita


@dataclass(slots=True)
class _Counts:
    inserted: int = 0
    updated: int = 0
    remapped: int = 0
    removed_pending: int = 0
    suggested: int = 0


def _apply_account(
    conn: Connection,
    plan: _Plan,
    transactions: list[PluggyTransaction],
    bills: list[PluggyBill],
    window: SyncWindow,
    now: datetime,
) -> AccountReport:
    if plan.stored is None:
        account_id = repository.insert_account(conn, plan.fresh, plan.item_id)
    else:
        account_id = plan.stored.id
        replaced = plan.stored.pluggy_id if plan.inherited else None
        repository.update_account(conn, account_id, plan.fresh, plan.item_id, replaced=replaced)
    _apply_bills(conn, account_id, bills, now)
    counts = _apply_transactions(conn, account_id, transactions, window, now)
    repository.refresh_freshness(conn, account_id, now=now, today=now.astimezone(_LOCAL_TZ).date())
    after = repository.account(conn, account_id)
    return AccountReport(
        label=after.label,
        inserted=counts.inserted,
        updated=counts.updated,
        remapped=counts.remapped,
        removed_pending=counts.removed_pending,
        suggested=counts.suggested,
        inherited=plan.inherited,
        last_synced_at=after.last_synced_at,
        last_transaction_date=after.last_transaction_date,
        first_transaction_date=after.first_transaction_date,
    )


def _apply_bills(conn: Connection, account_id: int, bills: list[PluggyBill], now: datetime) -> None:
    """Upsert by Pluggy id; a bill that came back under a new id is found by its due date."""
    stored = repository.account_bills(conn, account_id)
    known = {pluggy_id: bill_id for bill_id, pluggy_id, _due in stored}
    fresh_ids = {bill.id for bill in bills}
    orphans = [(bill_id, pluggy_id, due) for bill_id, pluggy_id, due in stored if pluggy_id not in fresh_ids]
    for bill in bills:
        bill_id = known.get(bill.id)
        if bill_id is not None:
            repository.update_bill(conn, bill_id, bill, account_id, now=now)
            continue
        match = next((orphan for orphan in orphans if orphan[2] == bill.due_date), None)
        if match is None:
            repository.insert_bill(conn, bill, account_id, now=now)
            continue
        orphans.remove(match)
        repository.update_bill(conn, match[0], bill, account_id, now=now)
        repository.rewrite_bill_id(conn, account_id, match[1], bill.id)


def _apply_transactions(
    conn: Connection, account_id: int, transactions: list[PluggyTransaction], window: SyncWindow, now: datetime
) -> _Counts:
    counts = _Counts()
    fresh = list({tx.id: tx for tx in transactions}.values())
    stored = repository.account_transactions(conn, account_id)
    by_pluggy = {row.pluggy_id: row for row in stored}
    fresh_ids = {tx.id for tx in fresh}
    seen: set[int] = set()

    unmatched: list[PluggyTransaction] = []
    for tx in fresh:
        row = by_pluggy.get(tx.id)
        if row is None:
            unmatched.append(tx)
            continue
        repository.update_transaction(conn, row.id, tx, account_id, now=now)
        seen.add(row.id)
        counts.updated += 1

    # Id novo para uma linha gravada: primeiro pelo providerId do banco, depois
    # pela chave natural exata (instante, valor, descricao, parcela), 1 para 1.
    orphans = [row for row in stored if row.pluggy_id not in fresh_ids]
    remapped: set[str] = set()
    for pairing in (pair_by_provider_id, pair_by_natural_key):
        left = [row for row in orphans if row.id not in seen]
        for row, tx in pairing(left, [tx for tx in unmatched if tx.id not in remapped]):
            repository.update_transaction(conn, row.id, tx, account_id, now=now)
            seen.add(row.id)
            remapped.add(tx.id)
            counts.remapped += 1

    # Resposta vazia nao apaga nada: e o que um consentimento vencido devolve.
    if fresh:
        stale = [
            row.id
            for row in stored
            if row.id not in seen and row.status == "PENDING" and window.contains(row.local_date)
        ]
        counts.removed_pending = repository.delete_transactions(conn, stale)

    inserted: list[StoredTransaction] = []
    for tx in unmatched:
        if tx.id in remapped:
            continue
        new_id = repository.insert_transaction(conn, tx, account_id, now=now)
        inserted.append(
            StoredTransaction(
                new_id,
                tx.id,
                tx.provider_id,
                tx.local_date,
                tx.amount,
                tx.status,
                tx.description,
                tx.date,
                tx.installment_number,
            )
        )
    counts.inserted = len(inserted)

    already = repository.suggested_candidates(conn, account_id)
    candidates = [
        row
        for row in stored
        if row.id not in seen and row.status == "POSTED" and row.id not in already and window.contains(row.local_date)
    ]
    for newer, older in pair_duplicates(inserted, candidates):
        repository.insert_suggestion(conn, newer.id, older.id)
        counts.suggested += 1
    return counts


# ------------------------------------------------------------- duplicatas


def confirm_duplicate(conn: Connection, suggestion_id: int) -> None:
    """The suggestion was right: the newer line is folded into the older one."""
    with conn.transaction():
        if not repository.merge_duplicate(conn, suggestion_id):
            raise ValidationError(f"Sugestão de duplicata {suggestion_id} não encontrada ou já resolvida.")


def reject_duplicate(conn: Connection, suggestion_id: int) -> None:
    """The suggestion was wrong: both lines stay, in the analyses too."""
    if not repository.reject_duplicate(conn, suggestion_id):
        raise ValidationError(f"Sugestão de duplicata {suggestion_id} não encontrada ou já resolvida.")


# ------------------------------------------------------ pontos de entrada


def sync_now(
    *,
    full: bool = False,
    now: datetime | None = None,
    env: Mapping[str, str] | None = None,
    connect: Callable[[], Connection] = get_connection,
    client_factory: ClientFactory = PluggyClient,
) -> SyncReport:
    """``bogle spending sync``: sync right away, whatever the last one was.

    Raises when the configuration is missing (before touching anything) or
    another sync holds the lock; per-connection failures are in the report.
    """
    config = load_config(os.environ if env is None else env)
    moment = now or datetime.now(UTC)
    conn = connect()
    try:
        client = client_factory(config.client_id, config.client_secret)
        try:
            return run_sync(conn, client, config.item_ids, now=moment, full=full)
        finally:
            client.close()
    finally:
        conn.close()


def _stamp(moment: datetime) -> str:
    return f"{moment.astimezone(_LOCAL_TZ):%d/%m %H:%M}"


def not_synced(message: str, last: datetime | None) -> str:
    """``gastos não sincronizados (<why>); dados de 26/11 08:00``."""
    text = f"gastos não sincronizados ({message.rstrip('.')})"
    return f"{text}; dados de {_stamp(last)}" if last is not None else text


def _failures(report: SyncReport) -> str:
    return "; ".join(f"{failure.subject}: {failure.message}" for failure in report.failures)


def _outcome(report: SyncReport, last: datetime | None) -> tuple[str | None, str | None]:
    """The stderr notice and the failure for ``bogle check`` of a sync that ran."""
    if not report.applied:
        parts = [_failures(report)] if report.failures else []
        parts += report.notices
        if not parts:
            return None, None
        notice = not_synced("; ".join(parts), last)
        return notice, (notice if report.failures else None)
    problem = f"gastos sincronizados em parte ({_failures(report)})" if report.failures else None
    parts = [problem] if problem else []
    parts += report.notices
    return ("; ".join(parts) or None), problem


def sync_if_due(
    *,
    now: datetime | None = None,
    env: Mapping[str, str] | None = None,
    connect: Callable[[], Connection] = get_connection,
    client_factory: ClientFactory = PluggyClient,
) -> AutoSync:
    """The automatic sync of every spending command (and of the TUI's Gastos, #120).

    Nothing happens within :data:`SYNC_INTERVAL` of the last sync that applied:
    no configuration read, no client built, no request. Past it, a sync runs.
    It never raises for an expected failure: what went wrong comes back as a
    terse notice, and the command goes on with what is stored.
    """
    moment = now or datetime.now(UTC)
    last: datetime | None = None
    try:
        conn = connect()
        try:
            last = get_setting(conn, SPENDING_LAST_SYNC_AT)
            if last is not None and moment - last < SYNC_INTERVAL:
                return AutoSync(ran=False)
            config = load_config(os.environ if env is None else env)
            client = client_factory(config.client_id, config.client_secret)
            try:
                report = run_sync(conn, client, config.item_ids, now=moment)
            finally:
                client.close()
        finally:
            conn.close()
    except (BogleError, psycopg.Error) as exc:
        notice = not_synced(message_for(exc), last)
        return AutoSync(ran=False, notice=notice, problem=notice)
    notice, problem = _outcome(report, last)
    return AutoSync(ran=True, report=report, notice=notice, problem=problem)
