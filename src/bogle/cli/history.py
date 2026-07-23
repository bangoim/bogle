"""``bogle history`` — patrimony evolution table + ASCII chart (issue #25)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import typer
from rich.console import Console
from rich.table import Table

from bogle.data import default_dispatcher
from bogle.db import get_connection
from bogle.reports.history import HistoryReport, compute_history
from bogle.reports.periods import parse_period

_CONSOLE = Console()

_PERIODS = ("12m", "2y", "5y", "10y", "all")
_GRANULARITY_LABEL = {"daily": "diaria", "weekly": "semanal", "monthly": "mensal"}


def _money(value: Decimal) -> str:
    return f"{value:.2f}"


def _signed(value: Decimal, *, percent: bool) -> str:
    color = "green" if value >= 0 else "red"
    body = f"{value * 100:+.2f}%" if percent else f"{value:+.2f}"
    return f"[{color}]{body}[/{color}]"


def _render_table(report: HistoryReport, period: str, console: Console) -> None:
    granularity = _GRANULARITY_LABEL[report.granularity]
    table = Table(title=f"Evolucao do patrimonio ({period}, {granularity})", title_style="bold")
    table.add_column("Data", style="cyan", no_wrap=True)
    for header in ("Patrimonio", "Variacao", "Variacao %"):
        table.add_column(header, justify="right")
    previous: Decimal | None = None
    for point in report.points:
        delta = point.value - previous if previous is not None else None
        percent = delta / previous if delta is not None and previous and previous > 0 else None
        table.add_row(
            point.date.isoformat(),
            _money(point.value),
            _signed(delta, percent=False) if delta is not None else "-",
            _signed(percent, percent=True) if percent is not None else "-",
        )
        previous = point.value
    console.print(table)


def _render_chart(report: HistoryReport) -> None:
    import plotext as plt

    plt.clear_figure()
    labels = [point.date.isoformat() for point in report.points]
    ticks = list(range(len(labels)))
    plt.plot(ticks, [float(point.value) for point in report.points], label="Patrimonio")
    step = max(1, len(ticks) // 6)
    plt.xticks(ticks[::step], labels[::step])
    plt.title("Evolucao do patrimonio")
    plt.plotsize(100, 25)
    plt.theme("clear")
    plt.show()


def history(
    period: str = typer.Option("12m", "--period", help=f"Janela: {', '.join(_PERIODS)}."),
    no_chart: bool = typer.Option(False, "--no-chart", help="So a tabela, sem o grafico ASCII."),
) -> None:
    parsed = parse_period(period, allowed=_PERIODS)

    conn = get_connection()
    try:
        report = compute_history(conn, default_dispatcher(), period=parsed, today=date.today())
    finally:
        conn.close()

    _render_table(report, parsed, _CONSOLE)
    if not no_chart:
        _render_chart(report)
    if report.excluded:
        _CONSOLE.print(
            f"[yellow]Nota:[/yellow] patrimonio nao considera {', '.join(report.excluded)} (sem historico de precos)."
        )
