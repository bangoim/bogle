from __future__ import annotations

import sys

import typer

from bogle.cli import assets as assets_cli
from bogle.domain.errors import BogleError

app = typer.Typer(
    help="bogle - CLI tool for passive portfolio rebalancing.",
    no_args_is_help=True,
)

app.command("add", help="Adicionar um novo ativo a carteira.")(assets_cli.add)
app.command("update", help="Atualizar o peso-alvo de um ativo.")(assets_cli.update)
app.command("remove", help="Remover um ativo da carteira.")(assets_cli.remove)
app.command("list", help="Listar todos os ativos cadastrados.")(assets_cli.list_assets)


@app.callback()
def _main() -> None:
    """Catch-all entry point. Subcommand runs after this returns."""


def _run() -> None:  # pragma: no cover - tiny shim for the console_script
    try:
        app()
    except BogleError as exc:
        typer.echo(f"erro: {exc}", err=True)
        sys.exit(1)
