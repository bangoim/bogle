"""``bogle spending`` — spending from Meu Pluggy (issue #114).

Thin: the sync, its rules and the automatic run live in :mod:`bogle.spending`.
Every command of the group but ``sync`` starts with :func:`auto_sync` (past 24 h
of the last sync), and goes on with what is stored when it fails, after one
``aviso:`` line on stderr. ``sync`` forces one; ``status`` says how fresh each
account is and how far back it goes; ``mark`` sets what a transaction is
(issue #115), over the rules, until ``--auto`` hands it back to them.
``categorize`` sets a transaction's category, optionally with a rule for the
lines like it (with a preview to confirm); ``groups`` and ``rules`` list and
edit the category groups and the rules (issue #116).

The call is in each command, not in the group's callback: the callback runs
before the subcommand reads its own arguments, so ``status --help`` would go to
the network first.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from bogle import format as fmt
from bogle.cli.parsing import parse_decimal
from bogle.cli.prompts import confirmed
from bogle.db import DEFAULT_TIMEZONE, get_connection
from bogle.domain.errors import ValidationError
from bogle.spending import repository
from bogle.spending.categories import (
    category_groups,
    create_group,
    find_category,
    group_summaries,
    map_category,
    parse_nature,
    remove_group,
    rename_group,
    set_category,
    set_nature,
    unmapped_categories,
)
from bogle.spending.marking import KIND_NAMES, parse_kind, set_kind
from bogle.spending.models import (
    AccountStatus,
    Category,
    CategoryGroup,
    DescriptionMatch,
    GroupSummary,
    ItemState,
    StoredRule,
    SyncReport,
    TransactionCategory,
    TransactionKind,
)
from bogle.spending.rules import (
    RuleBasis,
    RulePreview,
    create_rule,
    describe,
    draft_for,
    preview,
    remove_rule,
    undo_last,
)
from bogle.spending.rules import rules as list_rules
from bogle.spending.sync import sync_if_due, sync_now

_CONSOLE = Console()
_LOCAL_TZ = ZoneInfo(DEFAULT_TIMEZONE)

NO_ACCOUNTS = "Nenhuma conta de gastos ainda. Rode 'bogle spending sync'."
NOTHING_SYNCED = "Nenhuma conta sincronizada."
NO_RULES = "Nenhuma regra de categoria."
MAP_HINT = "mapeie com 'bogle spending groups map <categoria> <grupo>'"


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
    warnings += _unmapped(report.unmapped_categories)
    if warnings:
        console.print(fmt.attention(warnings))


def _unmapped(names: list[str]) -> list[str]:
    if not names:
        return []
    noun = "categoria sem grupo" if len(names) == 1 else "categorias sem grupo"
    return [f"{fmt.count(len(names))} {noun} ({MAP_HINT}): {', '.join(names)}"]


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


# ------------------------------------------------------ categorias (#116)


def _printable(text: str) -> str:
    # O texto vem da Pluggy: sem caractere de controle (ESC, quebra de linha) no terminal.
    return "".join(char for char in text if char.isprintable())


def _money_option(value: str | None, option: str) -> Decimal | None:
    if value is None:
        return None
    amount = parse_decimal(value, option)
    if amount < 0:
        raise ValidationError(f"{option} não pode ser negativo (vale sobre o valor absoluto).")
    return amount


def categorize(
    transaction_id: int = typer.Argument(..., help="Id da transação."),
    category: str | None = typer.Argument(
        None, help="Categoria: id da Pluggy ou nome (em português ou inglês).", show_default=False
    ),
    auto: bool = typer.Option(False, "--auto", help="Devolver a transação à regra ou à categoria da Pluggy."),
    rule: bool = typer.Option(False, "--rule", help="Criar também uma regra a partir da transação."),
    by: RuleBasis | None = typer.Option(  # noqa: B008 — padrao do typer, OptionInfo e sentinela imutavel
        None, "--by", help="Base da regra (padrão: documento quando houver, senão descrição).", show_default=False
    ),
    text: str | None = typer.Option(None, "--text", help="Trecho da descrição (padrão: a descrição inteira)."),
    match: DescriptionMatch | None = typer.Option(  # noqa: B008 — idem
        None, "--match", help="Como casar a descrição (padrão: contem com --text, senão igual).", show_default=False
    ),
    min_value: str | None = typer.Option(None, "--min", help="Valor mínimo (absoluto, em reais)."),
    max_value: str | None = typer.Option(None, "--max", help="Valor máximo (absoluto, em reais)."),
    same_account: bool = typer.Option(False, "--account", help="Só na conta da transação."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Não pede confirmação da regra."),
) -> None:
    if (category is None) == (not auto):
        typer.echo("Informe uma categoria ou --auto, não os dois.", err=True)
        raise typer.Exit(code=2)
    rule_options = by is not None or text is not None or match is not None or min_value or max_value or same_account
    if not rule and rule_options:
        typer.echo("--by, --text, --match, --min, --max e --account só valem com --rule.", err=True)
        raise typer.Exit(code=2)
    if rule and auto:
        typer.echo("--rule precisa de uma categoria, não de --auto.", err=True)
        raise typer.Exit(code=2)
    low = _money_option(min_value, "--min")
    high = _money_option(max_value, "--max")
    auto_sync()
    now = datetime.now(UTC)
    conn = get_connection()
    try:
        chosen = find_category(conn, category) if category is not None else None
        if not rule:
            after = set_category(conn, transaction_id, chosen, now=now)
            typer.echo(_categorized(after))
            return
        assert chosen is not None  # --rule exige a categoria, conferido acima
        draft = draft_for(
            conn,
            transaction_id,
            chosen,
            basis=by,
            text=text,
            match=match,
            min_value=low,
            max_value=high,
            same_account=same_account,
        )
        shown = preview(conn, draft)
        typer.echo(_previewed(shown))
        if not yes and not confirmed("Criar a regra? [s/N]"):
            typer.echo("nada foi alterado.", err=True)
            raise typer.Exit(code=1)
        with conn.transaction():
            rule_id, changed = create_rule(conn, draft, now=now)
            after = set_category(conn, transaction_id, chosen, now=now)
    finally:
        conn.close()
    noun = "transação mudou" if changed == 1 else "transações mudaram"
    typer.echo(f"Regra {rule_id} criada; {fmt.count(changed)} {noun}.")
    typer.echo(_categorized(after))


def _categorized(entry: TransactionCategory) -> str:
    """``Transação 42 (Conta, 2026-01-10, Compra, -20,00): Farmácia (Saúde), à mão; sem ela: Compras, da Pluggy``."""
    head = (
        f"Transação {entry.id} ({entry.account}, {_day(entry.local_date)}, {_printable(entry.description)}, "
        f"{fmt.signed_money(entry.amount)})"
    )
    if entry.rule_category is not None:
        underneath = f"{entry.rule_category}, da regra {entry.rule_id}"
    else:
        underneath = f"{entry.pluggy_category or 'sem categoria'}, da Pluggy"
    if entry.manual_category is not None:
        return f"{head}: {entry.manual_category} ({entry.group}), à mão; sem ela: {underneath}"
    return f"{head}: {underneath} ({entry.group})"


def _previewed(shown: RulePreview) -> str:
    draft = shown.draft
    noun = "transação já gravada" if shown.count == 1 else "transações já gravadas"
    lines = [
        f"Regra: {describe(draft.conditions, draft.account)} -> {draft.category.name}",
        f"Muda {fmt.count(shown.count)} {noun}.",
    ]
    if shown.manual:
        noun = (
            "tem categoria à mão, que continua valendo"
            if shown.manual == 1
            else "têm categoria à mão, que continua valendo"
        )
        lines[-1] = lines[-1][:-1] + f"; {fmt.count(shown.manual)} delas {noun}."
    return "\n".join(lines)


def groups(
    ctx: typer.Context,
    show_all: bool = typer.Option(False, "--all", help="Listar todas as categorias, com o id e o grupo."),
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    auto_sync()
    conn = get_connection()
    try:
        summaries = group_summaries(conn)
        listed = category_groups(conn) if show_all else []
        unmapped = unmapped_categories(conn)
    finally:
        conn.close()
    _render_groups(summaries, listed, unmapped, _CONSOLE)


def _render_groups(
    summaries: list[GroupSummary],
    listed: list[tuple[Category, CategoryGroup, bool]],
    unmapped: list[Category],
    console: Console,
) -> None:
    table = Table(title="Grupos", title_style="bold")
    table.add_column("Grupo", style="cyan", no_wrap=True)
    table.add_column("Natureza", no_wrap=True)
    table.add_column("Categorias")
    table.add_column("Total", justify="right")
    for summary in summaries:
        name = f"{summary.group.name} (reserva)" if summary.group.fallback else summary.group.name
        table.add_row(
            escape(name), summary.group.nature, escape(", ".join(summary.mapped)), fmt.count(summary.categories)
        )
    console.print(table)
    if listed:
        table = Table(title="Categorias", title_style="bold")
        table.add_column("Id", no_wrap=True)
        table.add_column("Categoria")
        table.add_column("Grupo", style="cyan", no_wrap=True)
        for category, group, _mapped in listed:
            indent = "  " if category.parent_id else ""
            table.add_row(category.id, escape(f"{indent}{category.name}"), escape(group.name))
        console.print(table)
    warnings = _unmapped([f"{category.name} ({category.id})" for category in unmapped])
    if warnings:
        console.print(fmt.attention(warnings))


def create_group_(
    name: str = typer.Argument(..., help="Nome do grupo."),
    nature: str = typer.Argument(..., help="fixo ou variavel."),
) -> None:
    chosen = parse_nature(nature)
    auto_sync()
    conn = get_connection()
    try:
        group = create_group(conn, name, chosen)
    finally:
        conn.close()
    typer.echo(f"Grupo {group.name} criado ({group.nature}).")


def rename_group_(
    name: str = typer.Argument(..., help="Nome atual do grupo."),
    new_name: str = typer.Argument(..., help="Nome novo."),
) -> None:
    auto_sync()
    conn = get_connection()
    try:
        group, renamed = rename_group(conn, name, new_name)
    finally:
        conn.close()
    typer.echo(f"Grupo {group.name} agora se chama {renamed}.")


def group_nature(
    name: str = typer.Argument(..., help="Nome do grupo."),
    nature: str = typer.Argument(..., help="fixo ou variavel."),
) -> None:
    chosen = parse_nature(nature)
    auto_sync()
    conn = get_connection()
    try:
        group = set_nature(conn, name, chosen)
    finally:
        conn.close()
    typer.echo(f"Grupo {group.name}: {chosen.value}.")


def map_category_(
    category: str = typer.Argument(..., help="Categoria: id da Pluggy ou nome."),
    group: str = typer.Argument(..., help="Grupo de destino."),
) -> None:
    auto_sync()
    conn = get_connection()
    try:
        mapped, target = map_category(conn, category, group)
    finally:
        conn.close()
    typer.echo(f"Categoria {mapped.name} ({mapped.id}) no grupo {target.name}.")


def remove_group_(
    name: str = typer.Argument(..., help="Grupo a remover."),
    destination: str = typer.Option(..., "--to", help="Grupo que recebe as categorias dele.", show_default=False),
) -> None:
    auto_sync()
    conn = get_connection()
    try:
        group, target, moved = remove_group(conn, name, destination)
    finally:
        conn.close()
    noun = "categoria foi" if moved == 1 else "categorias foram"
    typer.echo(f"Grupo {group.name} removido; {fmt.count(moved)} {noun} para {target.name}.")


def rules(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is not None:
        return
    auto_sync()
    conn = get_connection()
    try:
        stored = list_rules(conn)
    finally:
        conn.close()
    if not stored:
        typer.echo(NO_RULES)
        return
    _render_rules(stored, _CONSOLE)


def _render_rules(stored: list[StoredRule], console: Console) -> None:
    table = Table(title="Regras (a mais nova vence)", title_style="bold")
    table.add_column("Id", justify="right", no_wrap=True)
    table.add_column("Condições")
    table.add_column("Categoria", style="cyan")
    table.add_column("Transações", justify="right")
    table.add_column("Criada em", no_wrap=True)
    for rule in stored:
        table.add_row(
            str(rule.id),
            escape(describe(rule.conditions, rule.account)),
            escape(rule.category),
            fmt.count(rule.transactions),
            _moment(rule.created_at),
        )
    console.print(table)


def remove_rule_(rule_id: int = typer.Argument(..., help="Id da regra.")) -> None:
    auto_sync()
    conn = get_connection()
    try:
        rule, released = remove_rule(conn, rule_id)
    finally:
        conn.close()
    noun = "transação volta" if released == 1 else "transações voltam"
    typer.echo(f"Regra {rule.id} removida; {fmt.count(released)} {noun} a outra regra ou à Pluggy.")


def undo_rule() -> None:
    auto_sync()
    conn = get_connection()
    try:
        undone = undo_last(conn)
    finally:
        conn.close()
    noun = "transação voltou ao que era" if undone.restored == 1 else "transações voltaram ao que eram"
    typer.echo(f"Regra {undone.rule_id} desfeita e removida; {fmt.count(undone.restored)} {noun}.")
