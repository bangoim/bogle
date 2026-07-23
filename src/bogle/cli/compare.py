"""``bogle compare`` — portfolio profitability vs indices (issue #26)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import typer
from rich.console import Console
from rich.table import Table

from bogle import settings as settings_mod
from bogle.data import default_dispatcher
from bogle.db import get_connection
from bogle.reports.compare import CompareReport, compute_compare
from bogle.reports.periods import parse_period

_CONSOLE = Console()

_PERIODS = ("12m", "2y", "5y", "10y", "all", "ytd")


def _pct(value: Decimal) -> str:
    return f"{value * 100:+.2f}%"


def _render_table(report: CompareReport, period: str, console: Console) -> None:
    table = Table(title=f"Carteira vs indices ({period})", title_style="bold")
    table.add_column("Serie", style="cyan", no_wrap=True)
    table.add_column("Retorno acumulado", justify="right")
    for series in report.series:
        value = series.accumulated_return
        color = "green" if value >= 0 else "red"
        table.add_row(series.name, f"[{color}]{_pct(value)}[/{color}]")
    console.print(table)
    console.print(f"Janela: {report.grid[0].isoformat()} a {report.grid[-1].isoformat()} (base 100 no inicio)")


def _render_chart(report: CompareReport) -> None:
    import plotext as plt

    plt.clear_figure()
    labels = [on.isoformat() for on in report.grid]
    ticks = list(range(len(labels)))
    for series in report.series:
        plt.plot(ticks, [float(level) for level in series.levels], label=series.name)
    step = max(1, len(ticks) // 6)
    plt.xticks(ticks[::step], labels[::step])
    plt.title("Base 100 no inicio do periodo")
    plt.plotsize(100, 25)
    plt.theme("clear")
    plt.show()


def compare(
    index: str | None = typer.Option(
        None, "--index", help="Indices separados por virgula (ex: CDI,IBOV). Default: default_compare_indices."
    ),
    period: str = typer.Option("12m", "--period", help=f"Janela: {', '.join(_PERIODS)}."),
    no_chart: bool = typer.Option(False, "--no-chart", help="So a tabela, sem o grafico ASCII."),
) -> None:
    parsed = parse_period(period, allowed=_PERIODS)

    conn = get_connection()
    try:
        if index is None:
            configured = settings_mod.get_setting(conn, settings_mod.DEFAULT_COMPARE_INDICES)
            indices = tuple(configured)
        else:
            indices = tuple(part.strip().upper() for part in index.split(",") if part.strip())
        report = compute_compare(conn, default_dispatcher(), period=parsed, indices=indices, today=date.today())
    finally:
        conn.close()

    _render_table(report, parsed, _CONSOLE)
    if not no_chart:
        _render_chart(report)
    if report.excluded:
        _CONSOLE.print(
            f"[yellow]Nota:[/yellow] a serie da carteira nao considera {', '.join(report.excluded)} "
            "(sem historico de precos)."
        )
    for name, message in report.index_errors.items():
        _CONSOLE.print(f"[yellow]Nota:[/yellow] {name}: {message}")
