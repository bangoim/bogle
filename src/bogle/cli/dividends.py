"""``bogle dividends`` — income received, by month or by ticker (issue #30)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

import typer
from rich.console import Console
from rich.table import Table

from bogle.db import get_connection
from bogle.format import money
from bogle.reports.dividends import (
    MonthlyIncome,
    TickerIncome,
    income_by_month,
    income_by_ticker,
    twelve_month_start,
)
from bogle.reports.periods import parse_period
from bogle.repositories.transactions import TransactionRepository

_CONSOLE = Console()


class GroupBy(StrEnum):
    MONTH = "month"
    TICKER = "ticker"


def _title(period: str) -> str:
    return "Proventos recebidos (ultimos 12 meses)" if period == "12m" else "Proventos recebidos (desde o inicio)"


def _render_by_month(rows: list[MonthlyIncome], period: str, console: Console) -> None:
    table = Table(title=_title(period), title_style="bold")
    table.add_column("Mes", style="cyan", no_wrap=True)
    for header in ("Dividendos", "JCP (liq)", "FII rend.", "Juros RF", "Total"):
        table.add_column(header, justify="right")
    for row in rows:
        table.add_row(
            f"{row.month:%Y-%m}",
            money(row.dividend),
            money(row.jcp),
            money(row.rendimento),
            money(row.interest),
            money(row.total),
        )
    table.add_section()
    table.add_row(
        "TOTAL",
        money(sum((r.dividend for r in rows), Decimal("0"))),
        money(sum((r.jcp for r in rows), Decimal("0"))),
        money(sum((r.rendimento for r in rows), Decimal("0"))),
        money(sum((r.interest for r in rows), Decimal("0"))),
        money(sum((r.total for r in rows), Decimal("0"))),
        style="bold",
    )
    console.print(table)


def _render_by_ticker(rows: list[TickerIncome], period: str, console: Console) -> None:
    table = Table(title=_title(period).replace("recebidos", "por ticker"), title_style="bold")
    table.add_column("Ticker", style="cyan", no_wrap=True)
    table.add_column("Tipo", no_wrap=True)
    table.add_column("Total", justify="right")
    for row in rows:
        table.add_row(row.ticker, row.income_type.value, money(row.total))
    table.add_section()
    table.add_row("TOTAL", "", money(sum((r.total for r in rows), Decimal("0"))), style="bold")
    console.print(table)


def dividends(
    period: str = typer.Option("12m", "--period", help="Janela: 12m (default) ou all."),
    by: GroupBy = typer.Option(  # noqa: B008 — padrao do typer, OptionInfo e sentinela imutavel
        GroupBy.MONTH,
        "--by",
        case_sensitive=False,
        help="Agrupamento: month (default) ou ticker.",
    ),
) -> None:
    parsed = parse_period(period, allowed=("12m", "all"))
    today = date.today()
    start = twelve_month_start(today) if parsed == "12m" else None

    conn = get_connection()
    try:
        transactions = TransactionRepository(conn).list()
    finally:
        conn.close()

    if by is GroupBy.MONTH:
        monthly = income_by_month(transactions, start=start, end=today)
        if not monthly:
            typer.echo("Nenhum provento no periodo.")
            return
        _render_by_month(monthly, parsed, _CONSOLE)
        return

    per_ticker = income_by_ticker(transactions, start=start, end=today)
    if not per_ticker:
        typer.echo("Nenhum provento no periodo.")
        return
    _render_by_ticker(per_ticker, parsed, _CONSOLE)
