"""``bogle spending`` — spending from Meu Pluggy (issue #114).

Thin: the sync, its rules and the automatic run live in :mod:`bogle.spending`.
Every command of the group but ``sync`` starts with :func:`auto_sync` (past 24 h
of the last sync), and goes on with what is stored when it fails, after one
``aviso:`` line on stderr. ``sync`` forces one; ``status`` says how fresh each
account is and how far back it goes; ``mark`` sets what a transaction is
(issue #115), over the rules, until ``--auto`` hands it back to them.

The call is in each command, not in the group's callback: the callback runs
before the subcommand reads its own arguments, so ``status --help`` would go to
the network first.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import typer
from rich.console import Console
from rich.table import Table

from bogle import format as fmt
from bogle.db import DEFAULT_TIMEZONE, get_connection
from bogle.spending import repository
from bogle.spending.marking import KIND_NAMES, parse_kind, set_kind
from bogle.spending.models import AccountStatus, ItemState, SyncReport, TransactionKind
from bogle.spending.sync import sync_if_due, sync_now

_CONSOLE = Console()
_LOCAL_TZ = ZoneInfo(DEFAULT_TIMEZONE)

NO_ACCOUNTS = "Nenhuma conta de gastos ainda. Rode 'bogle spending sync'."
NOTHING_SYNCED = "Nenhuma conta sincronizada."


def auto_sync() -> None:
    """The automatic sync, first thing in every command of the group but ``sync``."""
    auto = sync_if_due()
    if auto.notice is not None:
        typer.echo(f"aviso: {auto.notice}", err=True)


def _moment(value: datetime | None) -> str:
    return f"{value.astimezone(_LOCAL_TZ):%Y-%m-%d %H:%M}" if value is not None else fmt.DASH


def _day(value: date | None) -> str:
    return value.isoformat() if value is not None else fmt.DASH


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:]


def sync(
    full: bool = typer.Option(False, "--full", help="Pedir todo o histórico, não só as últimas semanas."),
) -> None:
    report = sync_now(full=full)
    _render_sync(report, _CONSOLE)
    if report.failures:
        raise typer.Exit(code=1)


def _render_sync(report: SyncReport, console: Console) -> None:
    if report.accounts:
        when = _moment(report.accounts[0].last_synced_at)
        table = Table(title=f"Sync de {when}", title_style="bold")
        table.add_column("Conta", style="cyan", no_wrap=True)
        for header in ("Novas", "Atualizadas", "Removidas"):
            table.add_column(header, justify="right")
        table.add_column("Última transação", no_wrap=True)
        table.add_column("Desde", no_wrap=True)
        for account in report.accounts:
            table.add_row(
                account.label,
                fmt.count(account.inserted),
                fmt.count(account.updated + account.remapped),
                fmt.count(account.removed_pending),
                _day(account.last_transaction_date),
                _day(account.first_transaction_date),
            )
        console.print(table)
    else:
        typer.echo(NOTHING_SYNCED)
    warnings = [
        f"Conta {account.label} renovada: histórico mantido" for account in report.accounts if account.inherited
    ]
    warnings += [_sentence(notice) for notice in report.notices]
    warnings += [f"{_sentence(failure.subject)}: {failure.message}" for failure in report.failures]
    warnings += _duplicates(report.pending_duplicates)
    if warnings:
        console.print(fmt.attention(warnings))


def _duplicates(count: int) -> list[str]:
    if count == 0:
        return []
    noun = "duplicata provável pendente" if count == 1 else "duplicatas prováveis pendentes"
    return [f"{fmt.count(count)} {noun}"]


def status() -> None:
    auto_sync()
    conn = get_connection()
    try:
        accounts = repository.account_statuses(conn)
        items = repository.item_states(conn)
        duplicates = repository.pending_duplicates(conn)
    finally:
        conn.close()
    if not accounts and not items:
        typer.echo(NO_ACCOUNTS)
        return
    _render_status(accounts, items, duplicates, _CONSOLE)


def _render_status(accounts: list[AccountStatus], items: list[ItemState], duplicates: int, console: Console) -> None:
    if accounts:
        table = Table(title="Contas", title_style="bold")
        table.add_column("Conta", style="cyan", no_wrap=True)
        table.add_column("Último sync", no_wrap=True)
        table.add_column("Última transação", no_wrap=True)
        table.add_column("Desde", no_wrap=True)
        for account in accounts:
            table.add_row(
                account.label,
                _moment(account.last_synced_at),
                _day(account.last_transaction_date),
                _day(account.first_transaction_date),
            )
        console.print(table)
    if items:
        table = Table(title="Conexões", title_style="bold")
        table.add_column("Conexão", style="cyan", no_wrap=True)
        table.add_column("Estado", no_wrap=True)
        table.add_column("Atualizada em", no_wrap=True)
        table.add_column("Consentimento até", no_wrap=True)
        for item in items:
            expires = item.consent_expires_at
            table.add_row(
                item.label,
                item.status,
                _moment(item.last_updated_at),
                _day(expires.astimezone(_LOCAL_TZ).date() if expires is not None else None),
            )
        console.print(table)
    warnings = _duplicates(duplicates)
    if warnings:
        console.print(fmt.attention(warnings))


def mark(
    transaction_id: int = typer.Argument(..., help="Id da transação."),
    kind: str | None = typer.Argument(None, help=f"Tipo: {', '.join(KIND_NAMES)}.", show_default=False),
    auto: bool = typer.Option(False, "--auto", help="Devolver a transação às regras automáticas."),
) -> None:
    if (kind is None) == (not auto):
        typer.echo(f"Informe um tipo ({', '.join(KIND_NAMES)}) ou --auto, não os dois.", err=True)
        raise typer.Exit(code=2)
    chosen = parse_kind(kind) if kind is not None else None
    auto_sync()
    conn = get_connection()
    try:
        after = set_kind(conn, transaction_id, chosen, now=datetime.now(UTC))
    finally:
        conn.close()
    typer.echo(_marked(after))


def _marked(entry: TransactionKind) -> str:
    """``Transação 42 (Conta, 2026-01-10, Compra, -20,00): ignorada, à mão; regra: ...``."""
    # A descricao vem da Pluggy: sem caractere de controle (ESC, quebra de linha) no terminal.
    description = "".join(char for char in entry.description if char.isprintable())
    head = (
        f"Transação {entry.id} ({entry.account}, {_day(entry.local_date)}, {description}, "
        f"{fmt.signed_money(entry.amount)})"
    )
    rule = f"{entry.auto_kind} ({entry.auto_reason})" if entry.auto_kind is not None else "ainda sem regra aplicada"
    if entry.manual_kind is not None:
        return f"{head}: {entry.manual_kind}, à mão; pela regra seria {rule}"
    return f"{head}: {rule}, pela regra"
