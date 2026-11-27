"""``bogle glide`` — the trajectory of targets by age (issue #79).

Thin: the rules, the writes and the table live in :mod:`bogle.glide_path`, which
the TUI's Trajetoria screen also calls. An edit that moves this year's targets
writes them on the spot and says so on stderr, with the same notice as the
application of 01/01.
"""

from __future__ import annotations

from datetime import date

import typer
from rich.console import Console
from rich.table import Table

from bogle.cli.parsing import parse_ticker_values
from bogle.cli.prompts import confirmed
from bogle.db import get_connection
from bogle.format import pct
from bogle.glide_path import (
    GlideEdit,
    GlideSchedule,
    application_notice,
    clear_glide_path,
    load_glide_view,
    point_summary,
    remove_glide_point,
    set_glide_point,
    step_notice,
)
from bogle.repositories.glide_path import GlidePathRepository

_CONSOLE = Console()

NO_BIRTH_YEAR = "Sem ano de nascimento: defina com 'bogle config set birth_year <ano>'."
NO_POINTS = "Trajetória sem pontos: crie com 'bogle glide set <idade> TICKER=PESO ...'."
INACTIVE = "trajetória inativa até definir o ano de nascimento: bogle config set birth_year <ano>."
EMPTIED = "trajetória vazia: os alvos ficam como estão."

ANCHOR = "•"
"""Marks the ages that are points, next to the age."""


def set_(
    age: int = typer.Argument(..., help="Idade do ponto (0 a 120)."),
    weights: list[str] = typer.Argument(  # noqa: B008 — padrao do typer, ArgumentInfo e sentinela imutavel
        ..., help="Pesos do ponto, TICKER=PESO (ex: MUND11=0.70). Ticker omitido = 0.", show_default=False
    ),
) -> None:
    parsed = parse_ticker_values(weights, "Peso", unit="PESO", example="MUND11=0.70")
    conn = get_connection()
    try:
        edit = set_glide_point(conn, age, parsed, date.today())
        anchor = GlidePathRepository(conn).points().get(age, {})
    finally:
        conn.close()
    typer.echo(f"ponto gravado: {point_summary(age, anchor)}.")
    _report(edit)
    if not edit.active:
        typer.echo(f"aviso: {INACTIVE}", err=True)


def remove(age: int = typer.Argument(..., help="Idade do ponto a remover.")) -> None:
    conn = get_connection()
    try:
        edit = remove_glide_point(conn, age, date.today())
    finally:
        conn.close()
    typer.echo(f"ponto dos {age} anos removido.")
    _report(edit)
    if edit.points == 0:
        typer.echo(EMPTIED)


def show() -> None:
    conn = get_connection()
    try:
        view = load_glide_view(conn, date.today())
    finally:
        conn.close()
    if not view.points:
        typer.echo(NO_POINTS)
    if view.birth_year is None:
        typer.echo(NO_BIRTH_YEAR)
    if view.schedule is not None:
        _render(view.schedule, _CONSOLE)


def clear(yes: bool = typer.Option(False, "--yes", "-y", help="Não pede confirmação.")) -> None:
    conn = get_connection()
    try:
        count = len(GlidePathRepository(conn).points())
        if count == 0:
            typer.echo("A trajetória já está vazia.")
            return
        noun = "ponto" if count == 1 else "pontos"
        if not yes and not confirmed(f"Apagar {count} {noun} da trajetória? [s/N]"):
            typer.echo("nada foi alterado.", err=True)
            raise typer.Exit(code=1)
        cleared = clear_glide_path(conn)
    finally:
        conn.close()
    noun = "ponto" if cleared == 1 else "pontos"
    typer.echo(f"trajetória apagada ({cleared} {noun}); os alvos ficam como estão.")


def _report(edit: GlideEdit) -> None:
    notice = application_notice(edit.application)
    if notice is not None:
        typer.echo(f"aviso: {notice}", err=True)


def _render(schedule: GlideSchedule, console: Console) -> None:
    table = Table(title="Trajetória", title_style="bold")
    table.add_column("Ano", no_wrap=True)
    table.add_column("Idade", no_wrap=True)
    for ticker in schedule.tickers:
        table.add_column(ticker, justify="right", header_style="bold cyan")
    for index, row in enumerate(schedule.years):
        age = f"{row.age} {ANCHOR}" if row.anchor else str(row.age)
        # A primeira linha e sempre o ano corrente.
        table.add_row(
            str(row.year),
            age,
            *(pct(row.targets[ticker]) for ticker in schedule.tickers),
            style="bold" if index == 0 else None,
        )
    console.print(table)
    if schedule.next_step is not None:
        # Uma linha so, como o aviso de 01/01: quem quebra e o terminal.
        console.print(step_notice(schedule.next_step), markup=False, highlight=False, soft_wrap=True)
