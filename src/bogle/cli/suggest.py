"""``bogle suggest`` — how to split a contribution to shrink drift (issue #23).

``--price TICKER=VALOR`` is for a limit order: the quote says what the paper costs
now, this says what you intend to pay, and the shares and the effective cost are
computed on it. The split itself is not — see
:func:`~bogle.rebalancing.suggest_allocation`.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from bogle import settings as settings_mod
from bogle.cli.parsing import parse_decimal, parse_price_overrides
from bogle.data import default_dispatcher
from bogle.db import get_connection
from bogle.format import exact, exact_or_none, money, pct, shortfall, signed
from bogle.position import get_allocation_summary, price_provenance
from bogle.rebalancing import FEE_BASIS, AporteSuggestion, suggest_allocation

_CONSOLE = Console()


def _suggestion_json(suggestion: AporteSuggestion) -> dict[str, Any]:
    return {
        "amount": exact_or_none(suggestion.amount),
        "items": [
            {
                "ticker": item.ticker,
                "type": item.asset_type.value,
                "price": exact_or_none(item.price),
                "quoted_price": exact_or_none(item.quoted_price),
                "manual_price": item.is_manual_price,
                "price_source": item.price_source,
                "as_of": item.as_of.isoformat() if item.as_of else None,
                "allocation": exact_or_none(item.allocation),
                "quantity": exact_or_none(item.quantity),
                "effective_cost": exact_or_none(item.effective_cost),
                "target_weight": exact_or_none(item.target_weight),
                "current_weight": exact_or_none(item.current_weight),
                "weight_after": exact_or_none(item.weight_after),
                "drift_after": exact_or_none(item.drift_after),
            }
            for item in suggestion.items
        ],
        "totals": {
            "allocated": exact_or_none(suggestion.total_allocated),
            "estimated_fees": exact_or_none(suggestion.estimated_fees),
            "with_fees": exact_or_none(suggestion.total_with_fees),
            "leftover": exact_or_none(suggestion.leftover),
        },
        "warnings": suggestion.warnings,
    }


def _render(suggestion: AporteSuggestion, console: Console) -> None:
    table = Table(title="Sugestao de aporte", title_style="bold")
    table.add_column("Ticker", style="cyan", no_wrap=True)
    for header in (
        "Preco",
        "Valor sugerido",
        "Qtde papeis",
        "Custo efetivo",
        "Peso atual",
        "Target",
        "Peso apos",
        "Drift apos",
    ):
        table.add_column(header, justify="right")
    for item in suggestion.items:
        # O asterisco separa o preco que voce informou do que veio do provedor.
        price = f"{money(item.price)} *" if item.is_manual_price else money(item.price)
        table.add_row(
            item.ticker,
            price,
            money(item.allocation),
            exact(item.quantity),
            money(item.effective_cost),
            pct(item.current_weight),
            pct(item.target_weight),
            pct(item.weight_after),
            signed(item.drift_after, percent=True),
        )
    console.print(table)

    console.print(
        f"Total alocado: {money(suggestion.total_allocated)} / Taxa B3 (est.): {money(suggestion.estimated_fees)}"
        f" / Total com taxa: {money(suggestion.total_with_fees)}"
    )
    console.print(f"Aporte: {money(suggestion.amount)} / Sobra (caixa): {shortfall(suggestion.leftover)}")
    origin = price_provenance((item.price_source, item.as_of) for item in suggestion.items)
    if origin.sources:
        console.print(f"Fonte(s) de preco: {', '.join(origin.sources)}")
    if origin.latest is not None:
        console.print(f"Cotacao mais recente: {origin.latest:%Y-%m-%d %H:%M}")
    for warning in suggestion.warnings:
        console.print(f"[yellow]Atencao:[/yellow] {warning}")
    if suggestion.estimated_fees > 0:
        console.print(f"[dim]{FEE_BASIS}[/dim]")
    if suggestion.unquoted:
        # O motor diz o que ficou de fora; como trazer de volta e coisa da CLI.
        example = suggestion.unquoted[0].ticker
        console.print(f"Para incluir no aporte: --price {example}=VALOR (repetivel, um por ticker).")


def suggest(
    amount: str = typer.Option(..., "--amount", "-a", help="Valor do aporte (ex: 10000)."),
    # Repetivel em vez de lista separada por virgula (como --index faz): a virgula
    # tambem e separador decimal, e "VWRA11=114,86" ficaria ambiguo.
    price: list[str] = typer.Option(  # noqa: B008 — padrao do typer, OptionInfo e sentinela imutavel
        [],
        "--price",
        "-p",
        help="Preco que voce pretende pagar num ticker (ex: VWRA11=114,86). Repetivel; "
        "muda as cotas e o custo, nao a divisao do aporte.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Saida em JSON para scripts."),
) -> None:
    value = parse_decimal(amount, "--amount")
    prices = parse_price_overrides(price, "--price")
    conn = get_connection()
    try:
        # get_allocation_summary, e nao a posicao: um ativo cadastrado com target
        # e sem compra nenhuma tambem tem direito ao aporte — e comecar a posicao
        # e justamente o que a sugestao existe para dizer como fazer.
        summary = get_allocation_summary(conn, default_dispatcher())
        suggestion = suggest_allocation(summary, value, prices=prices)
        # Sugerir aporte e a "avaliacao" do ciclo de rebalanceamento (issue #24).
        settings_mod.set_value(conn, settings_mod.LAST_REBALANCE_DATE, date.today())
    finally:
        conn.close()

    if as_json:
        typer.echo(json.dumps(_suggestion_json(suggestion), ensure_ascii=False, indent=2))
        return
    _render(suggestion, _CONSOLE)
