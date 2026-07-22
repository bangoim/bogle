"""``bogle suggest`` — how to split a contribution to shrink drift (issue #23)."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from bogle import settings as settings_mod
from bogle.cli.parsing import parse_decimal
from bogle.data import default_dispatcher
from bogle.db import get_connection
from bogle.position import get_portfolio_summary
from bogle.rebalancing import AporteSuggestion, suggest_allocation

_CONSOLE = Console()


def _money(value: Decimal) -> str:
    return f"{value:.2f}"


def _pct(value: Decimal) -> str:
    return f"{value * 100:.2f}%"


def _dec(value: Decimal | None) -> str | None:
    # Normalized and non-scientific (10.00000000 -> "10", 0E+4 -> "0").
    return format(value.normalize(), "f") if value is not None else None


def _suggestion_json(suggestion: AporteSuggestion) -> dict[str, Any]:
    return {
        "amount": _dec(suggestion.amount),
        "items": [
            {
                "ticker": item.ticker,
                "type": item.asset_type.value,
                "price": _dec(item.price),
                "allocation": _dec(item.allocation),
                "quantity": _dec(item.quantity),
                "effective_cost": _dec(item.effective_cost),
                "target_weight": _dec(item.target_weight),
                "weight_after": _dec(item.weight_after),
            }
            for item in suggestion.items
        ],
        "totals": {
            "allocated": _dec(suggestion.total_allocated),
            "leftover": _dec(suggestion.leftover),
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
            _money(item.price),
            _money(item.allocation),
            format(item.quantity, "f") if item.quantity is not None else "-",
            _money(item.effective_cost),
            _pct(item.weight_after),
        )
    console.print(table)

    console.print(f"Total alocado: {_money(suggestion.total_allocated)} / Aporte: {_money(suggestion.amount)}")
    console.print(f"Sobra (caixa): {_money(suggestion.leftover)}")
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
