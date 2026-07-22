"""``bogle status`` — where the rebalance evaluation cycle stands (issue #24)."""

from __future__ import annotations

from datetime import date

import typer

from bogle.db import get_connection
from bogle.rebalancing import next_evaluation_date
from bogle.settings import LAST_REBALANCE_DATE, REBALANCE_PERIOD_MONTHS, get_setting


def status() -> None:
    conn = get_connection()
    try:
        period = get_setting(conn, REBALANCE_PERIOD_MONTHS)
        last = get_setting(conn, LAST_REBALANCE_DATE)
    finally:
        conn.close()

    typer.echo(f"Ciclo de avaliacao: {period} meses.")
    if last is None:
        typer.echo("Nenhuma avaliacao registrada ainda. Rode 'bogle suggest' para registrar a primeira.")
        return

    next_eval = next_evaluation_date(last, period)
    days = (next_eval - date.today()).days
    typer.echo(f"Ultima avaliacao: {last.isoformat()}.")
    if days > 0:
        typer.echo(f"Proxima avaliacao em {days} dia(s) ({next_eval.isoformat()}).")
    else:
        typer.echo(f"Avaliacao vencida ha {-days} dia(s) (desde {next_eval.isoformat()}). Rode 'bogle suggest'.")
