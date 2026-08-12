from __future__ import annotations

import sys
from datetime import date

import psycopg
import typer
from dotenv import load_dotenv

from bogle import format as fmt
from bogle.cli import assets as assets_cli
from bogle.cli import compare as compare_cli
from bogle.cli import config as config_cli
from bogle.cli import dividends as dividends_cli
from bogle.cli import history as history_cli
from bogle.cli import position as position_cli
from bogle.cli import profit as profit_cli
from bogle.cli import returns as returns_cli
from bogle.cli import status as status_cli
from bogle.cli import suggest as suggest_cli
from bogle.cli import transactions as transactions_cli
from bogle.db import get_connection
from bogle.domain.errors import BogleError
from bogle.rebalancing import overdue_notice
from bogle.settings import DECIMAL_SEPARATOR, LAST_REBALANCE_DATE, REBALANCE_PERIOD_MONTHS, get_setting

app = typer.Typer(
    help="bogle - CLI tool for passive portfolio rebalancing.",
    # Sem subcomando o bogle abre a TUI (issue #73), entao o help deixa de ser
    # o comportamento default de `bogle` sem argumentos.
    invoke_without_command=True,
)

app.command("add", help="Adicionar um novo ativo a carteira.")(assets_cli.add)
app.command("update", help="Atualizar o peso-alvo e/ou o tipo de um ativo.")(assets_cli.update)
app.command("remove", help="Remover um ativo da carteira.")(assets_cli.remove)
app.command("list", help="Listar todos os ativos cadastrados.")(assets_cli.list_assets)

app.command("buy", help="Registrar uma compra.")(transactions_cli.buy)
app.command("sell", help="Registrar uma venda (parcial ou total).")(transactions_cli.sell)
app.command("income", help="Registrar um provento (dividendo, JCP, rendimento, juros).")(transactions_cli.income)
app.command("transactions", help="Listar transacoes registradas.")(transactions_cli.list_transactions)

app.command("position", help="Posicao atual: tabela por ativo + totais, lucro do mes e proventos (precos ao vivo).")(
    position_cli.position
)
app.command("suggest", help="Sugerir a divisao de um aporte para reduzir o drift.")(suggest_cli.suggest)
app.command("status", help="Mostrar em que pe esta o ciclo de avaliacao de rebalanceamento.")(status_cli.status)
app.command("dividends", help="Proventos recebidos, por mes ou por ticker.")(dividends_cli.dividends)
app.command("return", help="Rentabilidade da carteira (TWR), opcionalmente vs indices.")(returns_cli.return_)
app.command("compare", help="Comparar a carteira com indices (base 100).")(compare_cli.compare)
app.command("history", help="Evolucao do patrimonio ao longo do tempo.")(history_cli.history)
app.command("profit", help="Lucro decomposto: ganho de capital + proventos.")(profit_cli.profit)

transaction_app = typer.Typer(help="Operacoes sobre uma transacao individual.", no_args_is_help=True)
transaction_app.command("remove", help="Remover uma transacao pelo ID.")(transactions_cli.remove)
app.add_typer(transaction_app, name="transaction")

config_app = typer.Typer(help="Ler e alterar configuracoes do usuario.", no_args_is_help=True)
config_app.command("get", help="Mostrar o valor de uma configuracao.")(config_cli.get)
config_app.command("set", help="Definir o valor de uma configuracao.")(config_cli.set_)
config_app.command("unset", help="Reverter uma configuracao ao default.")(config_cli.unset)
config_app.command("list", help="Listar todas as configuracoes.")(config_cli.list_settings)
app.add_typer(config_app, name="config")


def _read_preferences() -> tuple[str, str | None]:
    """The display separator and the overdue-cycle reminder, in one round trip.

    Best-effort by design: any failure (database down, migrations pending) leaves
    the canonical number format and no reminder, instead of breaking the command
    that follows.
    """
    try:
        conn = get_connection()
        try:
            separator = get_setting(conn, DECIMAL_SEPARATOR)
            period = get_setting(conn, REBALANCE_PERIOD_MONTHS)
            last = get_setting(conn, LAST_REBALANCE_DATE)
        finally:
            conn.close()
    except Exception:
        return fmt.CANONICAL_DECIMAL, None
    return separator, overdue_notice(last, period, today=date.today())


def _is_interactive() -> bool:
    """True when both ends are a real terminal, which a full-screen TUI needs.

    Keeps ``bogle | cat`` (and any non-tty invocation) printing the help instead
    of blowing up inside Textual.
    """
    return sys.stdin.isatty() and sys.stdout.isatty()


@app.callback()
def _main(ctx: typer.Context) -> None:
    """Catch-all entry point. Subcommand runs after this returns."""
    if ctx.invoked_subcommand is None:
        # `bogle` sem argumentos abre a interface interativa (issue #73);
        # `bogle --help` e `bogle <comando>` seguem intocados.
        if not _is_interactive():
            # Sem terminal, o comportamento e o mesmo de antes da TUI (quando
            # `no_args_is_help` cuidava disso): help na saida padrao e status 2,
            # que scripts existentes podem estar checando.
            typer.echo(ctx.get_help(), nl=False)
            raise typer.Exit(code=2)
        from bogle.tui import run_tui  # import tardio: comandos diretos nao pagam pelo textual

        run_tui()
        return
    separator, notice = _read_preferences()
    fmt.configure(separator)
    # `status` ja reporta o ciclo por inteiro; avisar de novo seria ruido.
    if notice is not None and ctx.invoked_subcommand != "status":
        typer.echo(f"aviso: {notice}", err=True)


def _run() -> None:  # pragma: no cover - tiny shim for the console_script
    load_dotenv()  # picks up BRAPI_TOKEN (and future secrets) from a local .env
    try:
        app()
    except BogleError as exc:
        typer.echo(f"erro: {exc}", err=True)
        sys.exit(1)
    except psycopg.OperationalError:
        typer.echo(
            "erro: nao foi possivel conectar ao banco de dados. "
            "Verifique BOGLE_DATABASE_URL e se o PostgreSQL esta rodando.",
            err=True,
        )
        sys.exit(1)
