"""``bogle profit`` — profit decomposed into capital gain + income (issue #29)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import typer
from rich.console import Console

from bogle.data import default_dispatcher
from bogle.db import get_connection
from bogle.domain.transactions import TransactionType
from bogle.format import signed
from bogle.position import get_portfolio_summary
from bogle.reports.dividends import twelve_month_start
from bogle.reports.periods import parse_period
from bogle.reports.profit import ProfitReport, compute_profit
from bogle.repositories.transactions import TransactionRepository

_CONSOLE = Console()

_INCOME_LABELS = (
    (TransactionType.DIVIDEND, "Dividendos"),
    (TransactionType.JCP, "JCP (liquido)"),
    (TransactionType.RENDIMENTO, "FII rendimentos"),
    (TransactionType.INTEREST, "Renda fixa juros"),
)


def _gain(value: Decimal) -> str:
    """A gain/loss figure: signed and colored (green >= 0, red < 0)."""
    return signed(value, percent=False)


def _render(report: ProfitReport, period: str, console: Console) -> None:
    console.print(f"[bold]Lucro da carteira (desde {report.since.isoformat()})[/bold]")
    console.print(f"  Ganho de capital:      {_gain(report.capital_total)}")
    console.print(f"    Realizado (vendas):  {_gain(report.realized)}")
    console.print(f"    Nao realizado:       {_gain(report.unrealized)}")
    console.print()
    income_window = " (ultimos 12 meses)" if period == "12m" else ""
    console.print(f"  Proventos recebidos:{income_window}   {_gain(report.income_total)}")
    for kind, label in _INCOME_LABELS:
        console.print(f"    {label:<19}{_gain(report.income_by_type[kind])}")
    console.print()
    if period == "12m":
        console.print("  (Lucro total omitido: ganho de capital e desde o inicio; proventos, 12 meses.)")
    else:
        console.print(f"  Lucro total:           {_gain(report.total)}")
    if report.unpriced:
        console.print(
            f"[yellow]Nota:[/yellow] ganho nao realizado nao considera {', '.join(report.unpriced)} (sem preco atual)."
        )


def profit(
    period: str = typer.Option(
        "all", "--period", help="Janela dos proventos: all (default) ou 12m. Ganho de capital e sempre total."
    ),
) -> None:
    parsed = parse_period(period, allowed=("all", "12m"))
    today = date.today()
    income_start = twelve_month_start(today) if parsed == "12m" else None

    conn = get_connection()
    try:
        portfolio = get_portfolio_summary(conn, default_dispatcher())
        transactions = TransactionRepository(conn).list()
    finally:
        conn.close()

    report = compute_profit(portfolio, transactions, income_start=income_start, income_end=today)
    _render(report, parsed, _CONSOLE)
