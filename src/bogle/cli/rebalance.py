"""``bogle rebalance`` — the annual rebalance of the retirement phase (issue #81).

Shows the plan of the year — what to sell, the IR of each sale, and what to buy
with the net — and writes nothing: the trades are registered afterwards with
``bogle sell`` and ``bogle buy``. ``--done`` records the year as done, which is
what silences the reminder; seeing the plan does not.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from bogle.annual_rebalance import plan_annual_rebalance, record_annual_rebalance
from bogle.data import default_dispatcher
from bogle.db import get_connection
from bogle.format import DASH, attention, exact, exact_or_none, money, pct, shortfall
from bogle.position import price_provenance
from bogle.rebalancing import AnnualRebalance, RebalanceItem, Recommendation

_CONSOLE = Console()

OPERATIONS = {
    Recommendation.SELL: "Vender",
    Recommendation.BUY: "Comprar",
    Recommendation.HOLD: "Manter",
}
"""O nome de cada recomendacao na tabela; a TUI usa o mesmo."""


def rebalance_title(plan: AnnualRebalance) -> str:
    return f"Rebalanceamento anual de {plan.year} (tolerância {pct(plan.threshold)})"


def recorded_message(year: int) -> str:
    return f"Rebalanceamento anual de {year} registrado."


def _rebalance_json(plan: AnnualRebalance) -> dict[str, Any]:
    return {
        "year": plan.year,
        "threshold": exact_or_none(plan.threshold),
        "items": [
            {
                "ticker": item.ticker,
                "type": item.asset_type.value,
                "operation": item.recommendation.value,
                "reason": item.sale_reason.value if item.sale_reason is not None else None,
                "price": exact_or_none(item.price),
                "price_source": item.price_source,
                "as_of": item.as_of.isoformat() if item.as_of else None,
                "quantity": exact_or_none(item.quantity),
                "value": exact_or_none(item.value),
                "income_tax": exact_or_none(item.income_tax),
                "target_weight": exact_or_none(item.target_weight),
                "current_weight": exact_or_none(item.current_weight),
                "weight_after": exact_or_none(item.weight_after),
            }
            for item in plan.items
        ],
        "totals": {
            "sold": exact_or_none(plan.total_sold),
            "income_tax": exact_or_none(plan.total_tax),
            "available": exact_or_none(plan.available),
            "bought": exact_or_none(plan.total_bought),
            "estimated_fees": exact_or_none(plan.estimated_fees),
            "leftover": exact_or_none(plan.leftover),
        },
        "warnings": plan.warnings,
    }


def _value(item: RebalanceItem) -> str:
    # Manter nao movimenta nada: traco, e nao um 0,00 que parece uma ordem.
    return DASH if item.recommendation is Recommendation.HOLD else money(item.value)


def _render(plan: AnnualRebalance, console: Console) -> None:
    table = Table(title=rebalance_title(plan), title_style="bold")
    table.add_column("Ticker", style="cyan", no_wrap=True)
    table.add_column("Operação")
    for header in ("Preço", "Qtde", "Valor", "IR (est.)", "Target", "Peso atual", "Peso após"):
        table.add_column(header, justify="right")
    for item in plan.items:
        table.add_row(
            item.ticker,
            OPERATIONS[item.recommendation],
            money(item.price),
            exact(item.quantity),
            _value(item),
            money(item.income_tax),
            pct(item.target_weight),
            pct(item.current_weight),
            pct(item.weight_after),
        )
    console.print(table)

    console.print(
        f"Vendas: {money(plan.total_sold)} / IR (est.): {money(plan.total_tax)} / Para compras: {money(plan.available)}"
    )
    console.print(
        f"Compras: {money(plan.total_bought)} / Taxa B3 (est.): {money(plan.estimated_fees)}"
        f" / Sobra (caixa): {shortfall(plan.leftover)}"
    )
    origin = price_provenance((item.price_source, item.as_of) for item in plan.items)
    if origin.sources:
        console.print(f"Fonte(s): {', '.join(origin.sources)}")
    if origin.latest is not None:
        console.print(f"Cotação: {origin.latest:%Y-%m-%d %H:%M}")
    if plan.warnings:
        console.print(attention(plan.warnings))


def rebalance(
    done: bool = typer.Option(
        False, "--done", help="Registrar o rebalanceamento anual deste ano como feito (o aviso some)."
    ),
    as_json: bool = typer.Option(False, "--json", help="Saída em JSON para scripts."),
) -> None:
    today = date.today()
    if done:
        conn = get_connection()
        try:
            year = record_annual_rebalance(conn, today=today)
        finally:
            conn.close()
        typer.echo(json.dumps({"recorded_year": year}) if as_json else recorded_message(year))
        return

    conn = get_connection()
    try:
        # So le: ver o plano nao e executa-lo, e o aviso continua ate o --done.
        plan = plan_annual_rebalance(conn, default_dispatcher(), today=today)
    finally:
        conn.close()
    if as_json:
        typer.echo(json.dumps(_rebalance_json(plan), ensure_ascii=False, indent=2))
        return
    _render(plan, _CONSOLE)
