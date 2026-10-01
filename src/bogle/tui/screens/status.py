"""Cycle screen: where the rebalance evaluation stands (issue #76).

Same content as ``bogle status``. It exists as a screen because the reminder that
arrives as a toast on the Home screen only says the cycle is overdue — this is
where the dates behind it are.
"""

from __future__ import annotations

from typing import override

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Footer, Header, Static

from bogle.tui import services
from bogle.tui.screens.data import DataScreen

_NEVER = "Nenhuma avaliacao registrada ainda. Use a tela de Aporte (ou 'bogle suggest') para registrar a primeira."


class StatusScreen(DataScreen[services.CycleStatus]):
    SUB_TITLE = "status"
    LOADING = "#cycle"
    NOTE = "#status-note"

    def __init__(self) -> None:
        super().__init__()
        self.panel = ""
        """Plain text of the panel (read by the tests)."""

    @override
    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="status"):
            yield Static(id="cycle")
            yield Static(id="status-note")
        yield Footer()

    @override
    def load(self) -> services.CycleStatus:
        return services.load_cycle()

    @override
    def clear_content(self) -> None:
        self._show_panel("")

    @override
    def render_report(self, report: services.CycleStatus) -> None:
        self._show_panel(_panel_markup(report))
        self.show_note(_note_for(report))

    def _show_panel(self, markup: str) -> None:
        rendered = Text.from_markup(markup)
        self.panel = rendered.plain
        self.query_one("#cycle", Static).update(rendered)


def _panel_markup(cycle: services.CycleStatus) -> str:
    lines = [f"[bold]Ciclo de avaliacao[/bold] {cycle.period_months} meses"]
    if cycle.last_evaluation is None:
        return lines[0]
    lines.append(f"Ultima avaliacao   {cycle.last_evaluation.isoformat()}")
    if cycle.next_evaluation is not None and cycle.days is not None:
        when = cycle.next_evaluation.isoformat()
        if cycle.days > 0:
            lines.append(f"Proxima avaliacao  {when} (em {cycle.days} dia(s))")
        else:
            lines.append(f"[red]Avaliacao vencida[/red]  desde {when} (ha {-cycle.days} dia(s))")
    return "\n".join(lines)


def _note_for(cycle: services.CycleStatus) -> str:
    if cycle.last_evaluation is None:
        return f"[yellow]{_NEVER}[/yellow]"
    if cycle.days is not None and cycle.days <= 0:
        return "[dim]Uma sugestao de aporte conta como avaliacao e reinicia o ciclo.[/dim]"
    return ""
