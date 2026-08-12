"""``bogle suggest`` — how to split a contribution to shrink drift (issue #23)."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from bogle import settings as settings_mod
from bogle.cli.parsing import parse_decimal
from bogle.data import default_dispatcher
from bogle.db import get_connection
from bogle.format import exact, exact_or_none, money, pct
from bogle.position import get_portfolio_summary
from bogle.rebalancing import AporteSuggestion, suggest_allocation

_CONSOLE = Console()


def _suggestion_json(suggestion: AporteSuggestion) -> dict[str, Any]:
    return {
        "amount": exact_or_none(suggestion.amount),
        "items": [
            {
                "ticker": item.ticker,
                "type": item.asset_type.value,
                "price": exact_or_none(item.price),
                "allocation": exact_or_none(item.allocation),
                "quantity": exact_or_none(item.quantity),
                "effective_cost": exact_or_none(item.effective_cost),
                "target_weight": exact_or_none(item.target_weight),
                "weight_after": exact_or_none(item.weight_after),
            }
            for item in suggestion.items
        ],
        "totals": {
            "allocated": exact_or_none(suggestion.total_allocated),
            "leftover": exact_or_none(suggestion.leftover),
        },
        "warnings": suggestion.warnings,
    }


def _render(suggestion: AporteSuggestion, console: Console) -> None:
    table = Table(title="Sugestao de aporte", title_style="bold")
    table.add_column("Ticker", style="cyan", no_wrap=True)
    for header in ("Preco", "Valor sugerido", "Qtde papeis", "Custo efetivo", "Peso apos aporte"):
        table.add_column(header, justify="right")
    for item in suggestion.items:
        table.add_row(
            item.ticker,
            money(item.price),
            money(item.allocation),
            exact(item.quantity),
            money(item.effective_cost),
            pct(item.weight_after),
        )
    console.print(table)

    console.print(f"Total alocado: {money(suggestion.total_allocated)} / Aporte: {money(suggestion.amount)}")
    console.print(f"Sobra (caixa): {money(suggestion.leftover)}")
    for warning in suggestion.warnings:
        console.print(f"[yellow]Atencao:[/yellow] {warning}")


def suggest(
    amount: str = typer.Option(..., "--amount", "-a", help="Valor do aporte (ex: 10000)."),
    as_json: bool = typer.Option(False, "--json", help="Saida em JSON para scripts."),
) -> None:
    value = parse_decimal(amount, "--amount")
    conn = get_connection()
    try:
        summary = get_portfolio_summary(conn, default_dispatcher())
        suggestion = suggest_allocation(summary, value)
        # Sugerir aporte e a "avaliacao" do ciclo de rebalanceamento (issue #24).
        settings_mod.set_value(conn, settings_mod.LAST_REBALANCE_DATE, date.today())
    finally:
        conn.close()

    if as_json:
        typer.echo(json.dumps(_suggestion_json(suggestion), ensure_ascii=False, indent=2))
        return
    _render(suggestion, _CONSOLE)
