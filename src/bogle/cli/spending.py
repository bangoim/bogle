"""``bogle spending`` — spending from Meu Pluggy (issue #114).

Thin: the sync, its rules and the automatic run live in :mod:`bogle.spending`.
Every command of the group but ``sync`` starts with :func:`auto_sync` (past 24 h
of the last sync), and goes on with what is stored when it fails, after one
``aviso:`` line on stderr. ``sync`` forces one; ``status`` says how fresh each
account is and how far back it goes; ``mark`` sets what a transaction is
(issue #115), over the rules, until ``--auto`` hands it back to them.
``categorize`` sets a transaction's category, optionally with a rule for the
lines like it (with a preview to confirm); ``groups`` and ``rules`` list and
edit the category groups and the rules (issue #116). ``summary`` and ``trend``
show the month by group and category and the monthly series (issue #117); the
sums live in :mod:`bogle.spending.analysis`, here they are only laid out.
``bills`` lists each card's bills with their reconciliation, the open one and
the installments still to post, bill by bill, against the average income, or
one bill by group and category; ``installments`` lists the purchases in
installments still running (issue #118, :mod:`bogle.spending.bills`).

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
from bogle.charts import render_line_chart
from bogle.cli.parsing import parse_decimal
from bogle.cli.prompts import confirmed
from bogle.db import DEFAULT_TIMEZONE, get_connection
from bogle.domain.errors import ValidationError
from bogle.spending import repository
from bogle.spending.analysis import (
    COMPARE_MONTHS,
    MAX_MONTHS,
    TREND_MONTHS,
    MonthSummary,
    Trend,
    change,
    month_label,
    month_summary,
    parse_month,
    spending_trend,
)
from bogle.spending.bills import (
    INCOME_MONTHS,
    BillDetail,
    BillsOverview,
    BillState,
    InstallmentPlan,
    base_description,
    bill_detail,
    bills_overview,
    parse_bill_ref,
)
from bogle.spending.bills import installments as running_installments
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
    GroupNature,
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


# ------------------------------------------------------- analises (#117)


def _today() -> date:
    """Today in Sao Paulo: the day the analyses count up to."""
    return datetime.now(_LOCAL_TZ).date()


def summary(
    month: str | None = typer.Option(None, "--month", help="Mês AAAA-MM (padrão: o corrente).", show_default=False),
    account: str | None = typer.Option(
        None, "--account", help="corrente, cartao ou o nome de uma conta (padrão: todas).", show_default=False
    ),
    compare: int = typer.Option(COMPARE_MONTHS, "--compare", min=1, max=MAX_MONTHS, help="Meses anteriores na média."),
) -> None:
    chosen = parse_month(month) if month is not None else None
    auto_sync()
    conn = get_connection()
    try:
        if not repository.accounts(conn):
            typer.echo(NO_ACCOUNTS)
            return
        result = month_summary(conn, today=_today(), month=chosen, account=account, compare=compare)
    finally:
        conn.close()
    _render_summary(result, _CONSOLE)


def _accounts_suffix(accounts: tuple[str, ...]) -> str:
    return f" ({', '.join(_printable(account) for account in accounts)})" if accounts else ""


def _change(value: Decimal, base: Decimal | None) -> str:
    return fmt.signed_pct(change(value, base))


def _months(compared: tuple[date, ...]) -> str:
    return _month_count(len(compared))


def _month_count(count: int) -> str:
    return "1 mês" if count == 1 else f"{count} meses"


def _tenths(value: Decimal | None) -> str:
    """An average count, one decimal place: ``2.333`` -> ``2,3``."""
    if value is None:
        return fmt.DASH
    return f"{value:.1f}".replace(".", fmt.separators().decimal)


def _render_summary(result: MonthSummary, console: Console) -> None:
    label = month_label(result.month)
    until = f" até o dia {result.until.day}" if result.partial else ""
    table = Table(title=escape(f"Gastos de {label}{until}{_accounts_suffix(result.accounts)}"), title_style="bold")
    table.add_column("Grupo", style="cyan")
    table.add_column("No mês", justify="right")
    compared = bool(result.compared)
    if compared:
        header = f"Média até o dia {result.until.day}" if result.partial else f"Média de {_months(result.compared)}"
        table.add_column(header, justify="right")
        table.add_column("Variação", justify="right")
    for entry in result.groups:
        name = _printable(entry.group.name)
        categories = entry.categories
        if len(categories) == 1:
            # Uma categoria so repetiria os valores do grupo: ela vai no nome dele.
            only = _printable(categories[0].name)
            if only.casefold() != name.casefold():
                name += f" · {only}"
            categories = ()
        if entry.group.nature == GroupNature.FIXED:
            name += " (fixo)"
        cells = [f"[bold]{escape(name)}[/bold]", fmt.money(entry.value)]
        if compared:
            cells += [fmt.money(entry.average), _change(entry.value, entry.average)]
        table.add_row(*cells)
        for category in categories:
            cells = [f"  {escape(_printable(category.name))}", fmt.money(category.value)]
            if compared:
                cells += [fmt.money(category.average), _change(category.value, category.average)]
            table.add_row(*cells, style="dim")
    table.add_section()
    cells = ["[bold]Total[/bold]", f"[bold]{fmt.money(result.total)}[/bold]"]
    if compared:
        cells += [fmt.money(result.total_average), _change(result.total, result.total_average)]
    table.add_row(*cells)
    console.print(table)

    # Estas linhas vao por typer.echo, sem markup do Rich: o nome da conta so passa pelo filtro.
    lines: list[str] = []
    if compared and result.shorter:
        # Conta mais nova que a janela: a media dela vem de menos meses que o cabecalho diz.
        lines.append(
            "; ".join(f"{_printable(name)}: média de {_month_count(count)}" for name, count in result.shorter) + "."
        )
    lines.append(
        f"O mês em uma conta: entrou {fmt.money(result.income)}; fixos {fmt.money(result.fixed)}; "
        f"variável {fmt.money(result.variable)}; sobra {fmt.signed_money(result.leftover)}."
    )
    if result.pace is not None:
        pace = result.pace
        lines.append(
            f"Ritmo do variável: {fmt.money(pace.value)} até o dia {result.until.day}, contra "
            f"{fmt.money(pace.average)} na média de {_months(result.compared)} até o mesmo dia "
            f"({_change(pace.value, pace.average)})."
        )
    if result.last_year is not None:
        last = result.last_year
        same = f"Mesmo mês de {result.month.year - 1}{until}"
        if last.missing:
            # So parte das contas existia: o "agora" e o dessas contas, nao o total da tabela.
            lines.append(
                f"{same} (sem {', '.join(_printable(name) for name in last.missing)}): {fmt.money(last.value)}, "
                f"contra {fmt.money(last.now)} agora ({_change(last.now, last.value)})."
            )
        else:
            lines.append(f"{same}: {fmt.money(last.value)} (agora {_change(last.now, last.value)}).")
    share = result.fallback_share
    if share is not None:
        lines.append(f"Em {_printable(result.fallback)} (sem grupo próprio): {fmt.pct(share)} do gasto do mês.")
    for line in lines:
        typer.echo(line)

    if result.merchants:
        table = Table(title="Estabelecimentos mais frequentes", title_style="bold")
        table.add_column("Estabelecimento", style="cyan")
        table.add_column("Vezes", justify="right")
        if compared:
            table.add_column("Média", justify="right")
            table.add_column("Variação", justify="right")
        table.add_column("Valor", justify="right")
        for merchant in result.merchants:
            cells = [escape(_printable(merchant.name)), fmt.count(merchant.count)]
            if compared:
                cells += [_tenths(merchant.average), _change(Decimal(merchant.count), merchant.average)]
            table.add_row(*cells, fmt.money(merchant.value))
        console.print(table)


def trend(
    months: int = typer.Option(TREND_MONTHS, "--months", min=1, max=MAX_MONTHS, help="Quantos meses, até o corrente."),
    group: str | None = typer.Option(None, "--group", help="Só um grupo (padrão: todos).", show_default=False),
    account: str | None = typer.Option(
        None, "--account", help="corrente, cartao ou o nome de uma conta (padrão: todas).", show_default=False
    ),
    no_chart: bool = typer.Option(False, "--no-chart", help="Só a tabela, sem o gráfico de linha."),
) -> None:
    auto_sync()
    conn = get_connection()
    try:
        if not repository.accounts(conn):
            typer.echo(NO_ACCOUNTS)
            return
        result = spending_trend(conn, today=_today(), months=months, group=group, account=account)
    finally:
        conn.close()
    if not result.points:
        typer.echo("Nenhum mês com dados no período.")
        return
    _render_trend(result, _CONSOLE)
    if not no_chart and len(result.points) > 1:
        _render_trend_chart(result)


def _trend_title(result: Trend) -> str:
    title = "Gastos por mês"
    if result.group is not None:
        title += f": {_printable(result.group.name)}"
    return title + _accounts_suffix(result.accounts)


def _render_trend(result: Trend, console: Console) -> None:
    table = Table(title=escape(_trend_title(result)), title_style="bold")
    table.add_column("Mês", style="cyan", no_wrap=True)
    split = result.group is None
    if split:
        table.add_column("Fixos", justify="right")
        table.add_column("Variável", justify="right")
        table.add_column("Total", justify="right")
    else:
        table.add_column("Gasto", justify="right")
    last = len(result.points) - 1
    for index, point in enumerate(result.points):
        notes: list[str] = []
        if index == last and result.partial:
            notes.append(f"até o dia {result.today.day}")
        if point.missing:
            # A conta ainda nao tinha historico: o mes soma so as outras.
            notes.append(f"sem {', '.join(_printable(name) for name in point.missing)}")
        label = month_label(point.month) + (f" ({'; '.join(notes)})" if notes else "")
        label = escape(label)
        if split:
            table.add_row(label, fmt.money(point.fixed), fmt.money(point.variable), fmt.money(point.total))
        else:
            table.add_row(label, fmt.money(point.total))
    console.print(table)


def _render_trend_chart(result: Trend) -> None:
    labels = [month_label(point.month) for point in result.points]
    name = _printable(result.group.name) if result.group is not None else "Total"
    render_line_chart(_trend_title(result), labels, [(name, [float(point.total) for point in result.points])])


# ------------------------------------------------- faturas e parcelas (#118)

CARD_HELP = "cartao ou o nome de um cartão (padrão: todos)."


def bills(
    bill: str | None = typer.Argument(
        None, help="Uma fatura: mês de vencimento AAAA-MM ou o id (padrão: todas).", show_default=False
    ),
    account: str | None = typer.Option(None, "--account", help=CARD_HELP, show_default=False),
) -> None:
    ref = parse_bill_ref(bill) if bill is not None else None
    auto_sync()
    conn = get_connection()
    try:
        if not repository.accounts(conn):
            typer.echo(NO_ACCOUNTS)
            return
        if ref is not None:
            detail = bill_detail(conn, ref, today=_today(), account=account)
            overview = None
        else:
            detail = None
            overview = bills_overview(conn, today=_today(), account=account)
    finally:
        conn.close()
    if detail is not None:
        _render_bill(detail, _CONSOLE)
    elif overview is not None:
        _render_bills(overview, _CONSOLE)


def _purchases(count: int) -> str:
    return "1 compra" if count == 1 else f"{fmt.count(count)} compras"


def _in_days(closing: date, today: date) -> str:
    days = (closing - today).days
    return "amanhã" if days == 1 else f"em {days} dias"


def _render_bills(overview: BillsOverview, console: Console) -> None:
    warnings: list[str] = []
    for card in overview.cards:
        name = _printable(card.card)
        if card.closed:
            table = Table(title=escape(f"Faturas de {name}"), title_style="bold")
            # O fechamento fica no detalhe: com ele a tabela nao cabe em 80 colunas.
            table.add_column("Id", justify="right", no_wrap=True)
            table.add_column("Vencimento", style="cyan", no_wrap=True)
            for header in ("Total", "Transações", "Diferença", "Pago"):
                table.add_column(header, justify="right", no_wrap=True)
            table.add_column("Estado")
            for bill in card.closed:
                check = bill.reconciliation
                assert check is not None  # toda fatura fechada tem a conciliacao
                table.add_row(
                    str(bill.id),
                    _day(bill.due),
                    fmt.money(bill.total),
                    fmt.money(check.lines),
                    fmt.signed_money(check.difference),
                    fmt.money(bill.paid),
                    bill.state.value,
                )
                if check.unexplained:
                    warnings.append(
                        f"Fatura {month_label(bill.month)} de {name}: diferença não identificada de "
                        f"{fmt.signed_money(check.unexplained)}; detalhe em 'bogle spending bills {bill.id}'"
                    )
            console.print(table)
        else:
            typer.echo(f"{name}: nenhuma fatura fechada na Pluggy.")
        opened = card.open
        if opened is None:
            warnings.append(f"{name}: nenhuma data de fechamento conhecida, sem fatura aberta nem projeção")
            continue
        estimated = " (datas estimadas)" if opened.estimated else ""
        assert opened.closing is not None  # a fatura aberta sempre tem o ciclo
        typer.echo(
            f"Fatura aberta de {name}: {fmt.money(opened.total)} em {_purchases(opened.purchases)}; "
            f"fecha {_in_days(opened.closing, overview.today)} ({_day(opened.closing)}), "
            f"vence em {_day(opened.due)}{estimated}."
        )
    _render_committed(overview, console)
    if warnings:
        console.print(fmt.attention(warnings))


def _render_committed(overview: BillsOverview, console: Console) -> None:
    if not overview.committed:
        typer.echo("Nenhuma parcela a lançar.")
        return
    split = len(overview.cards) > 1
    opened = {card.open.month for card in overview.cards if card.open is not None}
    table = Table(title="Parcelas a lançar por fatura", title_style="bold")
    table.add_column("Fatura", style="cyan", no_wrap=True)
    if split:
        for card in overview.cards:
            table.add_column(escape(_printable(card.card)), justify="right")
    table.add_column("Total" if split else "Parcelas", justify="right", no_wrap=True)
    table.add_column("Da receita média", justify="right", no_wrap=True)
    for entry in overview.committed:
        label = month_label(entry.month) + (" (aberta)" if entry.month in opened else "")
        cells = [label]
        if split:
            cells += [fmt.money(amount) for amount in entry.amounts]
        cells += [fmt.money(entry.total), fmt.pct(overview.share(entry))]
        table.add_row(*cells)
    console.print(table)
    if overview.income is not None:
        typer.echo(f"Receita média dos {INCOME_MONTHS} meses anteriores: {fmt.money(overview.income)}.")
    else:
        typer.echo(f"Nenhuma receita nos {INCOME_MONTHS} meses anteriores.")


def _render_bill(detail: BillDetail, console: Console) -> None:
    bill = detail.bill
    head = f"Fatura de {_printable(bill.card)} com vencimento em {_day(bill.due)}: {bill.state.value}"
    if bill.closing is not None:
        if bill.state == BillState.OPEN:
            head += f"; fecha {_in_days(bill.closing, detail.today)} ({_day(bill.closing)})"
        elif bill.state == BillState.FORECAST:
            head += f"; fecha em {_day(bill.closing)}"
        else:
            head += f"; fechou em {_day(bill.closing)}"
    if bill.state != BillState.FORECAST:
        head += f"; {_purchases(bill.purchases)}"
    typer.echo(head + (" (datas estimadas)." if bill.estimated else "."))

    if detail.groups:
        title = "Parcelas por grupo" if bill.state == BillState.FORECAST else "Gastos por grupo"
        table = Table(title=title, title_style="bold")
        table.add_column("Grupo", style="cyan")
        table.add_column("Valor", justify="right", no_wrap=True)
        for entry in detail.groups:
            name = _printable(entry.group.name)
            categories = entry.categories
            if len(categories) == 1:
                # Uma categoria so repetiria o valor do grupo: ela vai no nome dele.
                only = _printable(categories[0].name)
                if only.casefold() != name.casefold():
                    name += f" · {only}"
                categories = ()
            table.add_row(f"[bold]{escape(name)}[/bold]", fmt.money(entry.value))
            for category in categories:
                table.add_row(f"  {escape(_printable(category.name))}", fmt.money(category.value), style="dim")
        table.add_section()
        table.add_row(
            "[bold]Total[/bold]", f"[bold]{fmt.money(sum((g.value for g in detail.groups), Decimal(0)))}[/bold]"
        )
        console.print(table)
    else:
        typer.echo("Nenhum gasto nesta fatura.")

    if detail.installments:
        table = Table(title="Parcelas", title_style="bold")
        table.add_column("Data", no_wrap=True)
        table.add_column("Compra", style="cyan")
        table.add_column("Parcela", justify="right", no_wrap=True)
        table.add_column("Valor", justify="right", no_wrap=True)
        for line in sorted(detail.installments, key=lambda line: (line.local_date, line.id)):
            table.add_row(
                _day(line.local_date),
                escape(_printable(base_description(line.description))),
                f"{line.installment_number}/{line.total_installments}",
                fmt.money(line.amount),
            )
        console.print(table)

    check = bill.reconciliation
    if check is not None:
        table = Table(title="Conciliação", title_style="bold")
        table.add_column("", style="cyan")
        table.add_column("Valor", justify="right", no_wrap=True)
        table.add_row("Total da Pluggy", fmt.money(check.total))
        table.add_row(f"Transações ({fmt.count(check.count)})", fmt.money(check.lines))
        table.add_row("[bold]Diferença[/bold]", f"[bold]{fmt.signed_money(check.difference)}[/bold]")
        if check.difference or check.explained:
            for charge in check.explained:
                table.add_row(f"  {escape(_printable(charge.name))}", fmt.signed_money(charge.amount), style="dim")
            if check.unexplained:
                table.add_row("  não identificado", fmt.signed_money(check.unexplained))
        paid_on = f" em {_day(bill.paid_on)}" if bill.paid_on is not None else ""
        table.add_section()
        table.add_row(f"Pago{paid_on}", fmt.money(bill.paid))
        console.print(table)


def installments(
    account: str | None = typer.Option(None, "--account", help=CARD_HELP, show_default=False),
) -> None:
    auto_sync()
    conn = get_connection()
    try:
        if not repository.accounts(conn):
            typer.echo(NO_ACCOUNTS)
            return
        plans = running_installments(conn, today=_today(), account=account)
    finally:
        conn.close()
    if not plans:
        typer.echo("Nenhuma compra parcelada em andamento.")
        return
    _render_installments(plans, _CONSOLE)


def _render_installments(plans: tuple[InstallmentPlan, ...], console: Console) -> None:
    split = len({plan.account_id for plan in plans}) > 1
    table = Table(title="Parcelas em andamento", title_style="bold")
    table.add_column("Compra", style="cyan")
    if split:
        table.add_column("Cartão")
    table.add_column("Data da compra", no_wrap=True)
    table.add_column("Parcela", justify="right", no_wrap=True)
    table.add_column("Valor", justify="right", no_wrap=True)
    table.add_column("Falta", justify="right", no_wrap=True)
    table.add_column("Termina", no_wrap=True)
    for plan in plans:
        cells = [escape(_printable(plan.description))]
        if split:
            cells.append(escape(_printable(plan.card)))
        cells += [
            _day(plan.purchase_date),
            plan.label,
            fmt.money(plan.value),
            fmt.money(plan.remaining),
            month_label(plan.last_month),
        ]
        table.add_row(*cells)
    table.add_section()
    cells = ["[bold]Total[/bold]"] + ([""] if split else []) + ["", ""]
    cells += [
        fmt.money(sum((plan.value for plan in plans), Decimal(0))),
        f"[bold]{fmt.money(sum((plan.remaining for plan in plans), Decimal(0)))}[/bold]",
        "",
    ]
    table.add_row(*cells)
    console.print(table)
