"""``bogle config`` — read and write user settings (issue #31)."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from bogle import settings as settings_mod
from bogle.db import get_connection

_CONSOLE = Console()


def get(key: str = typer.Argument(..., help="Chave da configuracao (ex: rebalance_period_months).")) -> None:
    conn = get_connection()
    try:
        value = settings_mod.get_setting(conn, key)
    finally:
        conn.close()
    typer.echo(settings_mod.format_value(value))


def set_(
    key: str = typer.Argument(..., help="Chave da configuracao."),
    value: str = typer.Argument(..., help="Novo valor (listas separadas por virgula, datas YYYY-MM-DD)."),
) -> None:
    conn = get_connection()
    try:
        typed = settings_mod.set_setting(conn, key, value)
    finally:
        conn.close()
    typer.echo(f"{key} = {settings_mod.format_value(typed)}")


def unset(key: str = typer.Argument(..., help="Chave da configuracao.")) -> None:
    conn = get_connection()
    try:
        settings_mod.unset_setting(conn, key)
    finally:
        conn.close()
    typer.echo(f"{key} voltou ao valor default.")


def list_settings() -> None:
    conn = get_connection()
    try:
        entries = settings_mod.list_settings(conn)
    finally:
        conn.close()

    table = Table(title="Configuracoes", title_style="bold")
    table.add_column("Chave", style="cyan", no_wrap=True)
    table.add_column("Valor", justify="right")
    table.add_column("Tipo", no_wrap=True)
    table.add_column("Atualizado em", no_wrap=True)
    table.add_column("Descricao")
    for entry in entries:
        table.add_row(
            entry.key,
            settings_mod.format_value(entry.value),
            entry.type_name,
            "(default)" if entry.is_default else f"{entry.updated_at:%Y-%m-%d %H:%M}",
            entry.description,
        )
    _CONSOLE.print(table)
