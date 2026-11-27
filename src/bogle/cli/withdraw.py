"""``bogle withdraw`` — which tickers to sell for a withdrawal in retirement (issue #80).

The value is the gross: the sales cover it, rounded up to whole shares, and the
footer says what the IR leaves of it. Only a suggestion — nothing is written, and
the sale is registered afterwards with ``bogle sell``.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from bogle.cli.parsing import parse_decimal
from bogle.data import default_dispatcher
from bogle.db import get_connection
from bogle.format import attention, exact, exact_or_none, money, pct, shortfall, signed
from bogle.position import price_provenance
from bogle.rebalancing import WithdrawalSuggestion
from bogle.withdrawal import plan_withdrawal

_CONSOLE = Console()


def _withdrawal_json(suggestion: WithdrawalSuggestion) -> dict[str, Any]:
    return {
        "amount": exact_or_none(suggestion.amount),
        "retired": suggestion.retired,
        "items": [
            {
                "ticker": item.ticker,
                "type": item.asset_type.value,
                "stages": [stage.value for stage in item.stages],
                "price": exact_or_none(item.price),
                "price_source": item.price_source,
                "as_of": item.as_of.isoformat() if item.as_of else None,
                "allocation": exact_or_none(item.allocation),
                "quantity": exact_or_none(item.quantity),
                "gross": exact_or_none(item.gross),
                "income_tax": exact_or_none(item.income_tax),
                "net": exact_or_none(item.net),
                "target_weight": exact_or_none(item.target_weight),
                "current_weight": exact_or_none(item.current_weight),
                "weight_after": exact_or_none(item.weight_after),
            }
            for item in suggestion.items
        ],
        "totals": {
            "gross": exact_or_none(suggestion.total_gross),
            "income_tax": exact_or_none(suggestion.total_tax),
            "net": exact_or_none(suggestion.total_net),
            "residual": exact_or_none(suggestion.residual),
            "net_vs_amount": exact_or_none(suggestion.net_vs_amount),
        },
        "warnings": suggestion.warnings,
    }


def _render(suggestion: WithdrawalSuggestion, console: Console) -> None:
    table = Table(title="Sugestão de saque", title_style="bold")
    table.add_column("Ticker", style="cyan", no_wrap=True)
    for header in ("Preço", "Qtde", "Bruto", "IR (est.)", "Líquido", "Target", "Peso atual", "Peso após"):
        table.add_column(header, justify="right")
    for item in suggestion.items:
        table.add_row(
            item.ticker,
            money(item.price),
            exact(item.quantity),
            money(item.gross),
            money(item.income_tax),
            money(item.net),
            pct(item.target_weight),
            pct(item.current_weight),
            pct(item.weight_after),
        )
    console.print(table)

    console.print(
        f"Bruto: {money(suggestion.total_gross)} / IR (est.): {money(suggestion.total_tax)}"
        f" / Líquido: {money(suggestion.total_net)}"
    )
    console.print(
        f"Saque: {money(suggestion.amount)} / Resíduo (cotas inteiras): {shortfall(suggestion.residual)}"
        f" / Líquido vs saque: {signed(suggestion.net_vs_amount, percent=False)}"
    )
    origin = price_provenance((item.price_source, item.as_of) for item in suggestion.items)
    if origin.sources:
        console.print(f"Fonte(s): {', '.join(origin.sources)}")
    if origin.latest is not None:
        console.print(f"Cotação: {origin.latest:%Y-%m-%d %H:%M}")
    if suggestion.warnings:
        console.print(attention(suggestion.warnings))


def withdraw(
    amount: str = typer.Argument(
        ..., metavar="VALOR", help="Valor bruto a sacar (ex: 5000); as vendas cobrem este valor."
    ),
    as_json: bool = typer.Option(False, "--json", help="Saída em JSON para scripts."),
) -> None:
    value = parse_decimal(amount, "VALOR")
    conn = get_connection()
    try:
        # So le: a venda e registrada depois, com `bogle sell`, e sacar nao e a
        # avaliacao do ciclo (last_rebalance_date fica como esta).
        suggestion = plan_withdrawal(conn, default_dispatcher(), value, today=date.today())
    finally:
        conn.close()

    if as_json:
        typer.echo(json.dumps(_withdrawal_json(suggestion), ensure_ascii=False, indent=2))
        return
    _render(suggestion, _CONSOLE)
