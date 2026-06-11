from __future__ import annotations

import sys

import typer

from bogle.cli import assets as assets_cli
from bogle.cli import transactions as transactions_cli
from bogle.domain.errors import BogleError

app = typer.Typer(
    help="bogle - CLI tool for passive portfolio rebalancing.",
    no_args_is_help=True,
)

app.command("add", help="Adicionar um novo ativo a carteira.")(assets_cli.add)
app.command("update", help="Atualizar o peso-alvo de um ativo.")(assets_cli.update)
app.command("remove", help="Remover um ativo da carteira.")(assets_cli.remove)
app.command("list", help="Listar todos os ativos cadastrados.")(assets_cli.list_assets)

app.command("buy", help="Registrar uma compra.")(transactions_cli.buy)
app.command("sell", help="Registrar uma venda (parcial ou total).")(transactions_cli.sell)
app.command("income", help="Registrar um provento (dividendo, JCP, rendimento, juros).")(transactions_cli.income)
app.command("transactions", help="Listar transacoes registradas.")(transactions_cli.list_transactions)

transaction_app = typer.Typer(help="Operacoes sobre uma transacao individual.", no_args_is_help=True)
transaction_app.command("remove", help="Remover uma transacao pelo ID.")(transactions_cli.remove)
app.add_typer(transaction_app, name="transaction")


@app.callback()
def _main() -> None:
    """Catch-all entry point. Subcommand runs after this returns."""


def _run() -> None:  # pragma: no cover - tiny shim for the console_script
    try:
        app()
    except BogleError as exc:
        typer.echo(f"erro: {exc}", err=True)
        sys.exit(1)
