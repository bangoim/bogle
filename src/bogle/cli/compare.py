"""``bogle compare`` — portfolio profitability vs indices (issue #26)."""

from __future__ import annotations

from datetime import date

import typer
from rich.console import Console
from rich.table import Table

from bogle import settings as settings_mod
from bogle.charts import export_line_chart_html, open_in_browser, render_line_chart
from bogle.data import default_dispatcher
from bogle.db import get_connection
from bogle.format import signed
from bogle.reports.compare import CompareReport, compute_compare
from bogle.reports.periods import parse_period

_CONSOLE = Console()

_PERIODS = ("12m", "2y", "5y", "10y", "all", "ytd")


def _render_table(report: CompareReport, period: str, console: Console) -> None:
    table = Table(title=f"Carteira v. Indices ({period})", title_style="bold")
    table.add_column("Serie", style="cyan", no_wrap=True)
    table.add_column("Retorno", justify="right")
    for series in report.series:
        table.add_row(series.name, signed(series.accumulated_return, percent=True))
    console.print(table)
    console.print(f"Janela: {report.grid[0].isoformat()} a {report.grid[-1].isoformat()} (base 100 no inicio)")
    if report.data_as_of is not None:
        console.print(f"Dados ate: {report.data_as_of.isoformat()}")


def _render_chart(report: CompareReport) -> None:
    labels = [on.isoformat() for on in report.grid]
    series = [(s.name, [float(level) for level in s.levels]) for s in report.series]
    render_line_chart("Base 100 no inicio do periodo", labels, series)


def _export_chart(report: CompareReport, path: str) -> None:
    dates = list(report.grid)
    # Base 100 -> retorno acumulado em % (baseline 0), como no layout de referencia.
    series = [(s.name, [float(level) - 100 for level in s.levels]) for s in report.series]
    export_line_chart_html("Carteira v. Índices", dates, series, path, y_suffix="%")


def compare(
    index: str | None = typer.Option(
        None, "--index", help="Indices separados por virgula (ex: CDI,IBOV). Default: default_compare_indices."
    ),
    period: str = typer.Option("12m", "--period", help=f"Janela: {', '.join(_PERIODS)}."),
    no_chart: bool = typer.Option(False, "--no-chart", help="So a tabela, sem o grafico de linha (terminal)."),
    output: str | None = typer.Option(
        None, "--output", help="Salva um grafico HTML interativo (plotly) no caminho dado."
    ),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Abrir o HTML gerado no navegador."),
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
    if output is not None:
        _export_chart(report, output)
        typer.echo(f"grafico salvo em {output}")
        if open_browser:
            open_in_browser(output)
    elif not no_chart:
        _render_chart(report)
    if report.excluded:
        _CONSOLE.print(
            f"[yellow]Nota:[/yellow] a serie da carteira nao considera {', '.join(report.excluded)} "
            "(sem historico de precos)."
        )
    for name, message in report.index_errors.items():
        _CONSOLE.print(f"[yellow]Nota:[/yellow] {name}: {message}")
