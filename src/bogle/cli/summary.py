"""``bogle summary`` — invested vs patrimony at a glance (issue #28)."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

import typer
from rich.console import Console

from bogle.data import default_dispatcher
from bogle.db import get_connection
from bogle.position import get_portfolio_summary
from bogle.reports.dividends import twelve_month_start
from bogle.reports.periods import add_months
from bogle.reports.summary import income_received, window_profit
from bogle.reports.valuation import build_portfolio_valuation, patrimony_at
from bogle.repositories.transactions import TransactionRepository

_CONSOLE = Console()


def _money(value: Decimal | None) -> str:
    return f"{value:.2f}" if value is not None else "-"


def _signed_money(value: Decimal | None) -> str:
    if value is None:
        return "-"
    color = "green" if value >= 0 else "red"
    return f"[{color}]{value:+.2f}[/{color}]"


def _dec(value: Decimal | None) -> str | None:
    return format(value.normalize(), "f") if value is not None else None


def summary(
    as_json: bool = typer.Option(False, "--json", help="Saida em JSON para scripts."),
) -> None:
    today = date.today()
    month_start = add_months(today, -1)
    conn = get_connection()
    try:
        dispatcher = default_dispatcher()
        portfolio = get_portfolio_summary(conn, dispatcher)
        transactions = TransactionRepository(conn).list()
        valuation = build_portfolio_valuation(conn, dispatcher, start=month_start, end=today)
    finally:
        conn.close()

    if not portfolio.positions:
        typer.echo("Nenhuma posicao ativa.")
        return

    variation = portfolio.total_value - portfolio.total_invested
    variation_percent = portfolio.total_pnl_percent
    income_12m = income_received(transactions, start=twelve_month_start(today), end=today)

    month_profit: Decimal | None = None
    value_start = patrimony_at(valuation, month_start)
    value_end = patrimony_at(valuation, today)
    if value_start is not None and value_end is not None:
        month_profit = window_profit(valuation.transactions, value_start, value_end, start=month_start, end=today)

    if as_json:
        payload: dict[str, Any] = {
            "total_value": _dec(portfolio.total_value),
            "total_invested": _dec(portfolio.total_invested),
            "variation": _dec(variation),
            "variation_percent": _dec(variation_percent),
            "month_profit": _dec(month_profit),
            "income_12m": _dec(income_12m),
            "month_profit_excluded": valuation.excluded,
        }
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    _CONSOLE.print("[bold]Resumo da carteira[/bold]")
    _CONSOLE.print(f"  Patrimonio total:  {_money(portfolio.total_value)}")
    _CONSOLE.print(f"  Total investido:   {_money(portfolio.total_invested)}")
    percent = f" ({variation_percent * 100:+.2f}%)" if variation_percent is not None else ""
    _CONSOLE.print(f"  Variacao:          {_signed_money(variation)}{percent}")
    _CONSOLE.print(f"  Lucro do mes:      {_signed_money(month_profit)}")
    _CONSOLE.print(f"  Proventos (12m):   {_signed_money(income_12m)}")
    if valuation.excluded:
        _CONSOLE.print(
            f"[yellow]Nota:[/yellow] lucro do mes nao considera {', '.join(valuation.excluded)} "
            "(sem historico de precos)."
        )
