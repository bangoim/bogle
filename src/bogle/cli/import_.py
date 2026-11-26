"""``bogle import``: purchases and sales from the B3's Negociacao statement (issue #34).

Thin: reading the file is :mod:`bogle.imports.b3`, deciding what goes in is
:mod:`bogle.imports.plan`, which the TUI's Importar screen also calls. The report
is printed once, before the question, and is the same with ``--dry-run``.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import typer
from rich.console import Console

from bogle import format as fmt
from bogle.cli.parsing import parse_fee_rate
from bogle.cli.prompts import confirmed
from bogle.db import get_connection
from bogle.imports.b3 import read_statement
from bogle.imports.plan import apply_import, plan_import, report_lines

# soft_wrap: uma linha do relatorio quebrada pela largura do console (80 fora de
# um terminal) viraria duas, e o numero dela no bloco deixaria de ser uma linha so.
_CONSOLE = Console(soft_wrap=True, highlight=False)


def _operations(n: int) -> str:
    return f"{fmt.count(n)} {'operação' if n == 1 else 'operações'}"


def _entries(n: int) -> str:
    return f"{fmt.count(n)} {'lançamento' if n == 1 else 'lançamentos'}"


def import_(
    path: str = typer.Argument(..., help="Extrato de Negociação da B3, o .xlsx baixado do portal."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Só a prévia: não grava nem pergunta nada."),
    fee_rate: str | None = typer.Option(
        None,
        "--fee-rate",
        help="Taxa por operação, fração de quantidade × preço arredondada para cima ao centavo (ex: 0.00032).",  # noqa: RUF001 — sinal de vezes
    ),
    replace: bool = typer.Option(
        False,
        "--replace",
        help="Troca os dias com a quantidade do ledger e outro valor pelos lançamentos da B3, rateando taxas e IR retido.",
    ),
) -> None:
    # Taxa e arquivo antes do banco: nenhum dos dois precisa dele para ser recusado.
    rate: Decimal | None = parse_fee_rate(fee_rate, "--fee-rate") if fee_rate is not None else None
    statement = read_statement(Path(path))
    conn = get_connection()
    try:
        plan = plan_import(conn, statement, fee_rate=rate, replace=replace)
        _CONSOLE.print(fmt.attention(report_lines(plan)))
        if dry_run:
            typer.echo("prévia: nada foi gravado.")
            return
        if not plan.inserts:
            return
        removed = len(plan.removals)
        swap = f" no lugar de {_entries(removed)}" if removed else ""
        if not confirmed(f"Importar {_operations(len(plan.inserts))}{swap}? [s/N]"):
            typer.echo("nada foi alterado.", err=True)
            raise typer.Exit(code=1)
        written = apply_import(conn, plan)
    finally:
        conn.close()
    done = f"{_operations(len(written))} {'gravada' if len(written) == 1 else 'gravadas'}"
    if removed:
        done += f", {_entries(removed)} {'removido' if removed == 1 else 'removidos'}"
    typer.echo(f"{done}.")
