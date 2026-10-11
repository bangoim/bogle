"""``bogle backup`` and ``bogle restore`` (issues #32, #33).

Thin: the work and every check live in :mod:`bogle.backup`, which the TUI's
Config screen also calls. Restore is CLI-only on purpose — the interface lives on
top of the database a restore drops.
"""

from __future__ import annotations

import json

import typer

from bogle import format as fmt
from bogle.backup import DumpFormat, RestorePlan, create_backup, describe_rows, plan_restore, restore
from bogle.db import migrated_notice

_YES = ("s", "sim", "y", "yes")


def backup(
    output: str | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Arquivo ou diretório de destino. Default: ~/.local/share/bogle/backups/.",
    ),
    dump_format: DumpFormat = typer.Option(  # noqa: B008 — padrao do typer, OptionInfo e sentinela imutavel
        DumpFormat.CUSTOM,
        "--format",
        "-f",
        case_sensitive=False,
        help="custom (.dump, comprimido e verificável) ou plain (.sql legível).",
    ),
    as_json: bool = typer.Option(False, "--json", help="Saída em JSON para scripts."),
) -> None:
    result = create_backup(output=output, dump_format=dump_format)
    if as_json:
        payload = {
            "path": str(result.path),
            "format": result.format.value,
            "bytes": result.size,
            "rows": result.rows,
        }
        typer.echo(json.dumps(payload, ensure_ascii=False))
        return
    typer.echo(f"backup gravado em {result.path}")
    typer.echo(f"{result.format.value}, {fmt.file_size(result.size)}: {describe_rows(result.rows)}.")


def restore_(
    path: str = typer.Argument(..., help="Arquivo gerado por 'bogle backup' (custom ou plain)."),
    force: bool = typer.Option(False, "--force", help="Não pede confirmação."),
    no_safety_backup: bool = typer.Option(
        False, "--no-safety-backup", help="Não grava o backup de segurança do banco atual antes de substituí-lo."
    ),
) -> None:
    plan = plan_restore(path)
    if not force and not _confirmed(plan):
        typer.echo("nada foi alterado.", err=True)
        raise typer.Exit(code=1)
    result = restore(plan, safety_backup=not no_safety_backup)
    if result.safety is not None:
        typer.echo(f"backup de segurança: {result.safety.path}")
    if result.applied:
        typer.echo(f"aviso: {migrated_notice(result.applied)}", err=True)
    typer.echo(f"banco restaurado: {describe_rows(result.rows)}.")


def _confirmed(plan: RestorePlan) -> bool:
    typer.echo(_question(plan))
    try:
        answer = typer.prompt("Continuar? [s/N]", default="", show_default=False, prompt_suffix=" ")
    except typer.Abort:  # stdin fechado (EOF) ou ctrl+c: e um nao
        typer.echo("")
        return False
    return answer.strip().casefold() in _YES


def _question(plan: RestorePlan) -> str:
    dump = plan.dump
    when = f", de {dump.created_at:%Y-%m-%d %H:%M}" if dump.created_at is not None else ""
    head = f"Restaurar {dump.path} ({dump.format.value}, {fmt.file_size(dump.size)}{when})"
    if not plan.has_schema:
        return f"{head}\nno banco {plan.database}, hoje sem o schema do bogle."
    rows = describe_rows(plan.rows)
    # "a, b, c e d": a ultima virgula vira "e", como se diria.
    listed = " e ".join(rows.rsplit(", ", 1)) if rows else "nada"
    return f"{head}\nno banco {plan.database}, substituindo {listed}."
