"""``bogle status`` — where the rebalance evaluation cycle stands (issue #24), and
the annual rebalance of the retirement phase (issue #81)."""

from __future__ import annotations

from datetime import date

import typer

from bogle.annual_rebalance import load_annual_state
from bogle.db import get_connection
from bogle.rebalancing import AnnualRebalanceState, next_evaluation_date
from bogle.settings import LAST_REBALANCE_DATE, REBALANCE_PERIOD_MONTHS, get_setting


def annual_line(state: AnnualRebalanceState | None) -> str | None:
    """The annual-rebalance line of ``bogle status``; ``None`` without ``birth_year``."""
    if state is None:
        return None
    if not state.in_phase:
        return f"Rebalanceamento anual: a partir de {state.year}."
    if state.done:
        return f"Rebalanceamento anual de {state.year} feito."
    return f"Rebalanceamento anual de {state.year} pendente. Rode 'bogle rebalance'."


def status() -> None:
    today = date.today()
    conn = get_connection()
    try:
        period = get_setting(conn, REBALANCE_PERIOD_MONTHS)
        last = get_setting(conn, LAST_REBALANCE_DATE)
        annual = load_annual_state(conn, today)
    finally:
        conn.close()

    typer.echo(f"Ciclo de avaliação: {period} meses.")
    if last is None:
        typer.echo("Nenhuma avaliação registrada ainda. Rode 'bogle suggest' para registrar a primeira.")
    else:
        next_eval = next_evaluation_date(last, period)
        days = (next_eval - today).days
        typer.echo(f"Última avaliação: {last.isoformat()}.")
        if days > 0:
            typer.echo(f"Próxima avaliação em {days} dia(s) ({next_eval.isoformat()}).")
        else:
            typer.echo(f"Avaliação vencida há {-days} dia(s) (desde {next_eval.isoformat()}). Rode 'bogle suggest'.")
    line = annual_line(annual)
    if line is not None:
        typer.echo(line)
