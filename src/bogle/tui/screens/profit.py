"""Profit screen: capital gain plus income (issue #75).

Same decomposition as ``bogle profit``: realized gain (from the sequential
cost-basis replay) plus unrealized (market value minus the average cost of what
is still held), then income per type with JCP net of the tax withheld at source.

``t`` switches the income window between since-inception and the last 12 calendar
months. Capital gain is always since inception — windowing it would need the
patrimony at the window's start — so in the 12m view the total is deliberately
left out instead of adding two different windows together.
"""

from __future__ import annotations

from typing import override

from rich.markup import escape
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Footer, Header, Static

from bogle import format as fmt
from bogle.domain.transactions import TransactionType
from bogle.reports.profit import ProfitReport
from bogle.tui import services
from bogle.tui.screens.data import PeriodScreen

_INCOME_LABELS: tuple[tuple[TransactionType, str], ...] = (
    (TransactionType.DIVIDEND, "Dividendos"),
    (TransactionType.JCP, "JCP (liquido)"),
    (TransactionType.RENDIMENTO, "FII rendimentos"),
    (TransactionType.INTEREST, "Renda fixa juros"),
)

_WIDTH = 24
"""Width of the panel's label column, so every amount lines up under the next."""

_PARTIAL_TOTAL = "Lucro total omitido: ganho de capital e desde o inicio; proventos, 12 meses."


class ProfitScreen(PeriodScreen[ProfitReport]):
    SUBJECT = "lucro"
    PERIODS = ("all", "12m")
    LOADING = "#profit-panel"
    NOTE = "#profit-note"

    def __init__(self) -> None:
        super().__init__()
        self.panel = ""
        """Plain text of the panel (read by the tests)."""

    @override
    def subtitle(self) -> str:
        # O periodo aqui e so a janela dos proventos; "lucro - 12m" leria como se
        # o ganho de capital tambem fosse de 12 meses, o que ele nao e.
        return f"lucro - proventos {self.period}"

    @override
    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="profit"):
            yield Static(id="profit-panel")
            yield Static(id="profit-note")
        yield Footer()

    @override
    def load(self) -> ProfitReport:
        return services.load_profit(period=self.period)

    @override
    def clear_content(self) -> None:
        self._show_panel("")

    @override
    def render_report(self, report: ProfitReport) -> None:
        self._show_panel(_panel_markup(report, self.period))
        self.show_note(_note_for(report))

    def _show_panel(self, markup: str) -> None:
        rendered = Text.from_markup(markup)
        self.panel = rendered.plain
        self.query_one("#profit-panel", Static).update(rendered)


def _line(label: str, value: str, *, indent: int = 0) -> str:
    return f"{' ' * indent}{label:<{_WIDTH - indent}}{value}"


def _panel_markup(report: ProfitReport, period: str) -> str:
    income = report.income_by_type
    lines = [
        f"[bold]Lucro da carteira[/bold] [dim]desde {report.since.isoformat()}[/dim]",
        "",
        _line("Ganho de capital", fmt.signed(report.capital_total, percent=False)),
        _line("Realizado (vendas)", fmt.signed(report.realized, percent=False), indent=2),
        _line("Nao realizado", fmt.signed(report.unrealized, percent=False), indent=2),
        "",
        _line(
            "Proventos (12m)" if period == "12m" else "Proventos recebidos",
            fmt.signed(report.income_total, percent=False),
        ),
        *(_line(label, fmt.signed(income[kind], percent=False), indent=2) for kind, label in _INCOME_LABELS),
        "",
    ]
    if period == "12m":
        lines.append(f"[dim]{_PARTIAL_TOTAL}[/dim]")
    else:
        lines.append(_line("Lucro total", fmt.signed(report.total, percent=False)))
    return "\n".join(lines)


def _note_for(report: ProfitReport) -> str:
    if report.unpriced:
        unpriced = escape(", ".join(report.unpriced))
        return f"[yellow]Nota:[/yellow] ganho nao realizado nao considera {unpriced} (sem preco atual)."
    return ""
