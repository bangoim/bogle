"""Cycle screen: where the rebalance evaluation stands (issue #76).

Same content as ``bogle status``. It exists as a screen because the reminder that
arrives as a toast on the Home screen only says the cycle is overdue — this is
where the dates behind it are. In retirement it also says whether the annual
rebalance of the year is pending (issue #81).
"""

from __future__ import annotations

from typing import override

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Footer, Header, Static

from bogle.rebalancing import AnnualRebalanceState
from bogle.tui import services
from bogle.tui.screens.data import DataScreen

_NEVER = "Nenhuma avaliação registrada ainda. Use a tela de Aporte (ou 'bogle suggest') para registrar a primeira."


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
    lines = [f"[bold]Ciclo de avaliação[/bold] {cycle.period_months} meses"]
    if cycle.last_evaluation is not None:
        lines.append(f"Última avaliação   {cycle.last_evaluation.isoformat()}")
    if cycle.next_evaluation is not None and cycle.days is not None:
        when = cycle.next_evaluation.isoformat()
        if cycle.days > 0:
            lines.append(f"Próxima avaliação  {when} (em {cycle.days} dia(s))")
        else:
            lines.append(f"[red]Avaliação vencida[/red]  desde {when} (há {-cycle.days} dia(s))")
    annual = _annual_markup(cycle.annual)
    if annual:
        lines.extend(["", annual])
    return "\n".join(lines)


def _annual_markup(state: AnnualRebalanceState | None) -> str:
    """The annual rebalance (issue #81); empty without ``birth_year``."""
    if state is None:
        return ""
    title = "[bold]Rebalanceamento anual[/bold]"
    if not state.in_phase:
        return f"{title} a partir de {state.year}"
    if state.done:
        return f"{title} {state.year} feito"
    return f"{title} [red]{state.year} pendente[/red] ({services.ANNUAL_REBALANCE_WHERE})"


def _note_for(cycle: services.CycleStatus) -> str:
    if cycle.last_evaluation is None:
        return f"[yellow]{_NEVER}[/yellow]"
    if cycle.days is not None and cycle.days <= 0:
        return "[dim]Uma sugestão de aporte conta como avaliação e reinicia o ciclo.[/dim]"
    return ""
