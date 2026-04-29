from __future__ import annotations

from decimal import Decimal, InvalidOperation

import typer
from rich.console import Console
from rich.table import Table

from bogle.db import get_connection
from bogle.domain.errors import ValidationError
from bogle.repositories.assets import AssetRepository


def _parse_weight(value: str) -> Decimal:
    """Parse a CLI weight argument, validating the (0, 1] range.

    The argument arrives as a string so we get exact decimal handling
    instead of going through ``float`` (and its 0.1 + 0.2 surprises).
    """
    try:
        weight = Decimal(value)
    except InvalidOperation:
        raise ValidationError(
            f"--weight deve ser um numero decimal, recebido {value!r}."
        ) from None
    if not (Decimal("0") < weight <= Decimal("1")):
        raise ValidationError(
            f"--weight deve estar em (0, 1], recebido {weight}."
        )
    return weight


def add(
    ticker: str = typer.Argument(..., help="Ticker do ativo (ex: VTI)."),
    weight: str = typer.Option(
        ...,
        "--weight",
        "-w",
        help="Peso-alvo em decimal entre 0 e 1 (ex: 0.6 = 60%).",
    ),
) -> None:
    weight_dec = _parse_weight(weight)
    conn = get_connection()
    try:
        asset = AssetRepository(conn).add(ticker, weight_dec)
    finally:
        conn.close()
    typer.echo(
        f"asset {asset.ticker} adicionado com peso {asset.target_weight:.2%}."
    )


def update(
    ticker: str = typer.Argument(..., help="Ticker do ativo a atualizar."),
    weight: str | None = typer.Option(
        None,
        "--weight",
        "-w",
        help="Novo peso-alvo em decimal entre 0 e 1.",
    ),
) -> None:
    if weight is None:
        raise ValidationError("Nada para atualizar. Informe --weight.")
    weight_dec = _parse_weight(weight)
    conn = get_connection()
    try:
        asset = AssetRepository(conn).update_weight(ticker, weight_dec)
    finally:
        conn.close()
    typer.echo(
        f"asset {asset.ticker} atualizado para peso {asset.target_weight:.2%}."
    )


def remove(
    ticker: str = typer.Argument(..., help="Ticker do ativo a remover."),
) -> None:
    conn = get_connection()
    try:
        AssetRepository(conn).remove(ticker)
    finally:
        conn.close()
    typer.echo(f"asset {ticker.upper()} removido.")


def list_assets() -> None:
    conn = get_connection()
    try:
        assets = AssetRepository(conn).list()
    finally:
        conn.close()

    if not assets:
        typer.echo("Nenhum ativo cadastrado. Use 'bogle add' para comecar.")
        return

    table = Table(title="Carteira", title_style="bold")
    table.add_column("Ticker", style="cyan", no_wrap=True)
    table.add_column("Target Weight", justify="right")
    for asset in assets:
        table.add_row(asset.ticker, f"{asset.target_weight:.2%}")

    total = sum((a.target_weight for a in assets), start=Decimal("0"))
    table.caption = f"Soma dos pesos: {total:.2%}"

    Console().print(table)
