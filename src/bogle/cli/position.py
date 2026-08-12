"""``bogle position`` — the current portfolio, priced on the fly (issue #21).

Besides the per-ticker table (weight, drift vs target, PnL, TWR) and the
live totals, it also folds in the portfolio-level snapshot that used to
live in ``bogle summary``: month profit and income received over the last
12 months. Month profit needs historical prices, so it is only computed
when prices are on (omitted under ``--no-prices``).

The numbers come from :func:`~bogle.reports.snapshot.compute_snapshot`, shared
with the TUI's Position screen (#73); this module only renders them.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from bogle.data import default_dispatcher
from bogle.db import get_connection
from bogle.format import DASH, exact, exact_or_none, money, pct, signed
from bogle.position import PortfolioSummary, Position
from bogle.reports.snapshot import compute_snapshot

_CONSOLE = Console()


def _position_json(p: Position) -> dict[str, Any]:
    return {
        "ticker": p.ticker,
        "type": p.asset_type.value,
        "quantity": exact_or_none(p.quantity),
        "price": exact_or_none(p.price),
        "market_value": exact_or_none(p.market_value),
        "current_weight": exact_or_none(p.current_weight),
        "target_weight": exact_or_none(p.target_weight),
        "drift": exact_or_none(p.drift),
        "pnl": exact_or_none(p.pnl),
        "pnl_percent": exact_or_none(p.pnl_percent),
        "twr": exact_or_none(p.twr),
        "dividends": exact_or_none(p.dividends),
        "price_source": p.price_source,
        "as_of": p.as_of.isoformat() if p.as_of else None,
    }


def _summary_json(
    summary: PortfolioSummary,
    *,
    month_profit: Decimal | None = None,
    income_12m: Decimal | None = None,
    excluded: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "positions": [_position_json(p) for p in summary.positions],
        "totals": {
            "invested": exact_or_none(summary.total_invested),
            "value": exact_or_none(summary.total_value),
            "pnl": exact_or_none(summary.total_pnl),
            "pnl_percent": exact_or_none(summary.total_pnl_percent),
            "dividends": exact_or_none(summary.total_dividends),
            "month_profit": exact_or_none(month_profit),
            "income_12m": exact_or_none(income_12m),
            "month_profit_excluded": list(excluded),
        },
    }


def _render(
    summary: PortfolioSummary,
    console: Console,
    *,
    month_profit: Decimal | None = None,
    income_12m: Decimal | None = None,
    excluded: Sequence[str] = (),
    has_prices: bool = True,
) -> None:
    table = Table(title="Posicao", title_style="bold")
    table.add_column("Ticker", style="cyan", no_wrap=True)
    table.add_column("Tipo", no_wrap=True)
    for header in ("Qtd", "Preco", "Valor", "Peso atual", "Target", "Drift", "PnL R$", "PnL %", "TWR"):
        table.add_column(header, justify="right")
    for p in summary.positions:
        table.add_row(
            p.ticker,
            p.asset_type.value,
            exact(p.quantity),
            money(p.price),
            money(p.market_value),
            pct(p.current_weight),
            pct(p.target_weight),
            signed(p.drift, percent=True),
            signed(p.pnl, percent=False),
            signed(p.pnl_percent, percent=True),
            signed(p.twr, percent=True),
        )
    console.print(table)

    console.print(f"Total investido: {money(summary.total_invested)}")
    # Sem nenhuma posicao precificada os totais de mercado somam zero, o que nao
    # e o mesmo que a carteira valer zero (issue #74, revisao).
    value = money(summary.total_value) if has_prices else DASH
    pnl = signed(summary.total_pnl, percent=False) if has_prices else DASH
    pnl_percent = signed(summary.total_pnl_percent, percent=True) if has_prices else DASH
    console.print(f"Patrimonio total: {value}")
    console.print(f"Variacao: {pnl} ({pnl_percent})")
    console.print(f"Lucro do mes: {signed(month_profit, percent=False)}")
    console.print(f"Proventos (12m): {signed(income_12m, percent=False)}")
    sources = sorted({p.price_source for p in summary.positions if p.price_source})
    if sources:
        console.print(f"Fonte(s) de preco: {', '.join(sources)}")
    timestamps = [p.as_of for p in summary.positions if p.as_of is not None]
    if timestamps:
        console.print(f"Cotacao mais recente: {max(timestamps):%Y-%m-%d %H:%M}")
    if excluded:
        console.print(
            f"[yellow]Nota:[/yellow] lucro do mes nao considera {', '.join(excluded)} (sem historico de precos)."
        )


def position(
    no_prices: bool = typer.Option(False, "--no-prices", help="Usa so dados da base, sem bater nas APIs."),
    as_json: bool = typer.Option(False, "--json", help="Saida em JSON para scripts."),
) -> None:
    conn = get_connection()
    try:
        # Month profit needs historical prices; it is skipped under --no-prices.
        dispatcher = None if no_prices else default_dispatcher()
        snapshot = compute_snapshot(conn, dispatcher, today=date.today())
    finally:
        conn.close()

    if as_json:
        payload = _summary_json(
            snapshot.summary,
            month_profit=snapshot.month_profit,
            income_12m=snapshot.income_12m,
            excluded=snapshot.excluded,
        )
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if not snapshot.summary.positions:
        typer.echo("Nenhuma posicao ativa.")
        return
    _render(
        snapshot.summary,
        _CONSOLE,
        month_profit=snapshot.month_profit,
        income_12m=snapshot.income_12m,
        excluded=snapshot.excluded,
        has_prices=snapshot.has_prices,
    )
