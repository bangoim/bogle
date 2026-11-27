"""``bogle check`` — the portfolio's pendencies, for launchd or cron (issue #108).

Prints one line per pendency (see :mod:`bogle.alerts`) and exits 1 when there is
any, 0 when there is none, 3 when the check itself failed. ``--notify`` also
shows them as a macOS notification, the one channel a scheduled run has: nobody
reads its stdout or stderr.

The callback in :mod:`bogle.cli` skips this command: the migrations run here,
inside the error handling, and the glide path of 01/01 is left to the next run
someone sees — applied under launchd, its notice would reach nobody.
"""

from __future__ import annotations

import subprocess
import traceback
from datetime import date

import psycopg
import typer

from bogle.alerts import CHECK_FAILED, CheckResult, listing, notification_text, settings_alerts, stale_prices_alert
from bogle.data import default_dispatcher
from bogle.db import get_connection, migrate_if_pending, migrated_notice
from bogle.domain.errors import BogleError
from bogle.error_messages import message_for
from bogle.reports.overview import PortfolioOverview, compute_current_overview

NOTIFY_SCRIPT = (
    "on run argv",
    'display notification (item 1 of argv) with title "bogle"',
    "end run",
)
"""O texto vai como argumento do ``run``, nunca dentro do script: um ticker ou
uma mensagem de erro com aspas nao tem como virar AppleScript."""


def load_overview(today: date) -> PortfolioOverview:
    """The Home summary, with the same prices and the same notion of stale."""
    conn = get_connection()
    try:
        return compute_current_overview(conn, default_dispatcher(), today=today)
    finally:
        conn.close()


def _describe(exc: Exception) -> str:
    """The failure in the CLI's words; an unexpected one also leaves its traceback."""
    if isinstance(exc, BogleError | psycopg.Error):
        return message_for(exc)
    # Bug ou falha de rede de algum cliente: o traceback vai para o stderr (o log
    # do agendador), e a linha curta para a saida e a notificacao.
    traceback.print_exception(exc)
    return f"{type(exc).__name__}: {exc}"


def run_check(today: date) -> CheckResult:
    """Every pendency that could be checked, and what could not.

    The database first: without it nothing else can be known. Prices last, and
    on their own: a provider down hides the stale prices, not the two reminders.
    """
    try:
        applied = migrate_if_pending()
        if applied:
            typer.echo(f"aviso: {migrated_notice(applied)}", err=True)
        conn = get_connection()
        try:
            alerts = settings_alerts(conn, today)
        finally:
            conn.close()
    except Exception as exc:
        return CheckResult(errors=[_describe(exc)])
    try:
        stale = stale_prices_alert(load_overview(today))
    except Exception as exc:
        return CheckResult(alerts=alerts, errors=[f"preços não verificados ({_describe(exc)})"])
    if stale is not None:
        alerts.append(stale)
    return CheckResult(alerts=alerts)


def send_notification(text: str) -> bool:
    """Show ``text`` in a macOS notification; ``False`` when it did not go out."""
    command = ["osascript"]
    for line in NOTIFY_SCRIPT:
        command += ["-e", line]
    command += ["--", text]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def check(
    notify: bool = typer.Option(False, "--notify", help="Também mostrar as pendências numa notificação do macOS."),
) -> None:
    result = run_check(date.today())
    if result.alerts:
        for line in listing(result.alerts):
            typer.echo(line)
    elif not result.errors:
        typer.echo("Nenhuma pendência.")
    for error in result.errors:
        typer.echo(f"erro: {error}", err=True)

    code = result.exit_code
    # Falha tambem notifica: sob o launchd, uma checagem quebrada em silencio
    # seria lida como "nada pendente".
    if notify and (result.alerts or result.errors) and not send_notification(notification_text(result)):
        typer.echo("erro: notificação não enviada (osascript).", err=True)
        code = CHECK_FAILED
    raise typer.Exit(code)
