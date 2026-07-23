"""``bogle position`` — the current portfolio, priced on the fly (issue #21).

Besides the per-ticker table (weight, drift vs target, PnL, TWR) and the
live totals, it also folds in the portfolio-level snapshot that used to
live in ``bogle summary``: month profit and income received over the last
12 months. Month profit needs historical prices, so it is only computed
when prices are on (omitted under ``--no-prices``).
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
from bogle.position import PortfolioSummary, Position, get_portfolio_summary
from bogle.reports.dividends import twelve_month_start
from bogle.reports.periods import add_months
from bogle.reports.summary import income_received, window_profit
from bogle.reports.valuation import build_portfolio_valuation, patrimony_at
from bogle.repositories.transactions import TransactionRepository

_CONSOLE = Console()


def _money(value: Decimal | None) -> str:
    return f"{value:.2f}" if value is not None else "-"


def _qty(value: Decimal | None) -> str:
    return format(value.normalize(), "f") if value is not None else "-"


def _pct(value: Decimal | None) -> str:
    return f"{value * 100:.2f}%" if value is not None else "-"


def _signed(value: Decimal | None, *, percent: bool) -> str:
    """Signed, colored cell (green >= 0, red < 0); percentage or money."""
    if value is None:
        return "-"
    color = "green" if value >= 0 else "red"
    body = f"{value * 100:+.2f}%" if percent else f"{value:+.2f}"
    return f"[{color}]{body}[/{color}]"


def _dec(value: Decimal | None) -> str | None:
    # Normalized and non-scientific (10.00000000 -> "10", 0E+4 -> "0").
    return format(value.normalize(), "f") if value is not None else None


def _position_json(p: Position) -> dict[str, Any]:
    return {
        "ticker": p.ticker,
        "type": p.asset_type.value,
        "quantity": _dec(p.quantity),
        "price": _dec(p.price),
        "market_value": _dec(p.market_value),
        "current_weight": _dec(p.current_weight),
        "target_weight": _dec(p.target_weight),
        "drift": _dec(p.drift),
        "pnl": _dec(p.pnl),
        "pnl_percent": _dec(p.pnl_percent),
        "twr": _dec(p.twr),
        "dividends": _dec(p.dividends),
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
            "invested": _dec(summary.total_invested),
            "value": _dec(summary.total_value),
            "pnl": _dec(summary.total_pnl),
            "pnl_percent": _dec(summary.total_pnl_percent),
            "dividends": _dec(summary.total_dividends),
            "month_profit": _dec(month_profit),
            "income_12m": _dec(income_12m),
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
            _qty(p.quantity),
            _money(p.price),
            _money(p.market_value),
            _pct(p.current_weight),
            _pct(p.target_weight),
            _signed(p.drift, percent=True),
            _signed(p.pnl, percent=False),
            _signed(p.pnl_percent, percent=True),
            _signed(p.twr, percent=True),
        )
    console.print(table)

    console.print(f"Total investido: {_money(summary.total_invested)}")
    console.print(f"Patrimonio total: {_money(summary.total_value)}")
    console.print(
        f"Variacao: {_signed(summary.total_pnl, percent=False)} ({_signed(summary.total_pnl_percent, percent=True)})"
    )
    console.print(f"Lucro do mes: {_signed(month_profit, percent=False)}")
    console.print(f"Proventos (12m): {_signed(income_12m, percent=False)}")
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
    today = date.today()
    month_start = add_months(today, -1)
    conn = get_connection()
    try:
        dispatcher = None if no_prices else default_dispatcher()
        summary = get_portfolio_summary(conn, dispatcher)
        transactions = TransactionRepository(conn).list()
        # Month profit needs historical prices; skip it under --no-prices.
        valuation = (
            build_portfolio_valuation(conn, dispatcher, start=month_start, end=today)
            if dispatcher is not None
            else None
        )
    finally:
        conn.close()

    income_12m = income_received(transactions, start=twelve_month_start(today), end=today)
    month_profit: Decimal | None = None
    excluded: list[str] = []
    if valuation is not None:
        excluded = valuation.excluded
        value_start = patrimony_at(valuation, month_start)
        value_end = patrimony_at(valuation, today)
        if value_start is not None and value_end is not None:
            month_profit = window_profit(valuation.transactions, value_start, value_end, start=month_start, end=today)

    if as_json:
        payload = _summary_json(summary, month_profit=month_profit, income_12m=income_12m, excluded=excluded)
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if not summary.positions:
        typer.echo("Nenhuma posicao ativa.")
        return
    _render(summary, _CONSOLE, month_profit=month_profit, income_12m=income_12m, excluded=excluded)
