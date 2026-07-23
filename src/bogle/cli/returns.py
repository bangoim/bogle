"""``bogle return`` — portfolio profitability, optionally vs indices (issue #27)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import typer
from rich.console import Console

from bogle import settings as settings_mod
from bogle.data import default_dispatcher
from bogle.db import get_connection
from bogle.reports.periods import parse_period
from bogle.reports.returns import DEFAULT_PERIODS, PeriodReturn, ReturnsReport, compute_returns

_CONSOLE = Console()

_LABELS = {"total": "Total", "12m": "12 meses", "1m": "Ultimo mes"}


def _pct(value: Decimal | None) -> str:
    return f"{value * 100:+.2f}%" if value is not None else "-"


def _window(row: PeriodReturn) -> str:
    if row.period == "1m":
        return f"({row.start.isoformat()} a {row.end.isoformat()})"
    return f"(desde {row.start.isoformat()})"


def _render(report: ReturnsReport, indices: tuple[str, ...], console: Console) -> None:
    console.print("[bold]Rentabilidade da carteira[/bold]")
    for row in report.rows:
        label = f"{_LABELS[row.period]:<11} {_window(row)}"
        console.print(f"  {label}: {_pct(row.twr)}  (TWR)")

    for index in indices:
        console.print(f"\n[bold]vs {index}:[/bold]")
        for row in report.rows:
            index_return = row.index_returns.get(index)
            if row.twr is None or index_return is None:
                console.print(f"  {_LABELS[row.period]}: -")
                continue
            diff = (row.twr - index_return) * 100
            color = "green" if diff >= 0 else "red"
            console.print(
                f"  {_LABELS[row.period]}: {_pct(row.twr)} carteira / {_pct(index_return)} {index}"
                f"  -> [{color}]{diff:+.2f} p.p.[/{color}]"
            )

    if report.excluded:
        console.print(
            f"\n[yellow]Nota:[/yellow] TWR nao considera {', '.join(report.excluded)} (sem historico de precos)."
        )
    for index, message in report.index_errors.items():
        console.print(f"[yellow]Nota:[/yellow] {index}: {message}")


def _resolve_indices(vs: str | None) -> tuple[str, ...]:
    if vs is None:
        return ()
    if vs.strip().lower() == "default":
        conn = get_connection()
        try:
            configured = settings_mod.get_setting(conn, settings_mod.DEFAULT_COMPARE_INDICES)
        finally:
            conn.close()
        return tuple(configured)
    return tuple(part.strip().upper() for part in vs.split(",") if part.strip())


def return_(
    period: str | None = typer.Option(None, "--period", help="total, 12m ou 1m. Default: painel completo."),
    vs: str | None = typer.Option(
        None, "--vs", help="Indices para comparar (ex: CDI,IPCA); 'default' usa default_compare_indices."
    ),
) -> None:
    periods = DEFAULT_PERIODS if period is None else (parse_period(period, allowed=("total", "12m", "1m")),)
    indices = _resolve_indices(vs)

    conn = get_connection()
    try:
        report = compute_returns(conn, default_dispatcher(), periods=periods, indices=indices, today=date.today())
    finally:
        conn.close()

    _render(report, indices, _CONSOLE)
