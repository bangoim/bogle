"""History screen: patrimony over time (issue #75).

Same table and same chart as ``bogle history`` — fixed income valued by present
value, TESOURO reported as excluded (#17) — with the window on ``t`` and the
interactive HTML export on ``o``.

The chart plots reais, so the privacy mode (``h``) takes it off the screen
instead of masking it: a curve is an amount drawn instead of written, and an
axis labeled in thousands gives away exactly what the toggle exists to hide.
The same rule blocks the export while amounts are hidden — opening a browser
with the real numbers would defeat the point.
"""

from __future__ import annotations

from decimal import Decimal
from typing import ClassVar, override

from rich.markup import escape
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.widgets import DataTable, Footer, Header, Static

from bogle import format as fmt
from bogle.charts import Series
from bogle.reports.history import HistoryReport
from bogle.tui import cells, services
from bogle.tui.errors import HANDLED, message_for
from bogle.tui.screens.data import PeriodScreen
from bogle.tui.widgets.chart import LineChart

_TITLE = "Evolucao do patrimonio"
_GRANULARITY = {"daily": "diaria", "weekly": "semanal", "monthly": "mensal"}
_HIDDEN_CHART = "[dim]Grafico oculto enquanto os valores estao ocultos ('h' mostra).[/dim]"
_HIDDEN_EXPORT = "valores ocultos: mostre com 'h' antes de exportar o grafico."


class HistoryScreen(PeriodScreen[HistoryReport]):
    SUBJECT = "historico"
    PERIODS = ("12m", "2y", "5y", "10y", "all")
    AUTO_FOCUS = "#points"
    LOADING = "#points"
    NOTE = "#history-note"
    BINDINGS: ClassVar[list[BindingType]] = [Binding("o", "export", "Exportar")]

    @override
    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="history"):
            table = DataTable(id="points", cursor_type="row", zebra_stripes=True)
            table.add_columns("Data", "Patrimonio", "Variacao", "Variacao %")
            yield table
            yield LineChart(id="chart")
            yield Static(id="chart-hidden")
            yield Static(id="history-note")
        yield Footer()

    # --- acoes ----------------------------------------------------------

    def action_export(self) -> None:
        report = self.report
        if report is None:
            self.notify("nada para exportar ainda.", severity="warning")
            return
        if fmt.amounts_hidden():
            self.notify(_HIDDEN_EXPORT, severity="warning", timeout=8, markup=False)
            return
        self._export(report, self.period)

    # --- carga ----------------------------------------------------------

    @override
    def load(self) -> HistoryReport:
        return services.load_history(period=self.period)

    @override
    def clear_content(self) -> None:
        self.query_one(DataTable).clear()
        self.query_one(LineChart).clear()

    @override
    def render_report(self, report: HistoryReport) -> None:
        table = self.query_one(DataTable)
        table.clear()  # mantem as colunas
        previous: Decimal | None = None
        for point in report.points:
            delta = point.value - previous if previous is not None else None
            percent = delta / previous if delta is not None and previous and previous > 0 else None
            table.add_row(
                cells.text(point.date.isoformat()),
                cells.money(point.value),
                cells.signed(delta, percent=False),
                cells.signed(percent, percent=True),
                key=point.date.isoformat(),
            )
            previous = point.value
        self._render_chart(report)
        self.show_note(_note_for(report))

    def _render_chart(self, report: HistoryReport) -> None:
        hidden = fmt.amounts_hidden()
        chart = self.query_one(LineChart)
        masked = self.query_one("#chart-hidden", Static)
        masked.update(Text.from_markup(_HIDDEN_CHART) if hidden else "")
        chart.display = not hidden
        masked.display = hidden
        if hidden:
            chart.clear()
            return
        chart.draw(_TITLE, [point.date.isoformat() for point in report.points], _series(report))

    # --- export ---------------------------------------------------------

    @work(thread=True, group="export")
    def _export(self, report: HistoryReport, period: str) -> None:
        try:
            path = services.export_chart(
                title=_TITLE,
                x_values=[point.date for point in report.points],
                series=_series(report),
                path=services.chart_path(f"history-{period}"),
                y_title="R$",
            )
        except (*HANDLED, OSError) as exc:
            self.app.call_from_thread(self._export_failed, message_for(exc))
            return
        self.app.call_from_thread(self._exported, path)

    def _exported(self, path: object) -> None:
        self.notify(f"grafico salvo em {path}", title="exportado", timeout=8, markup=False)

    def _export_failed(self, message: str) -> None:
        self.notify(f"nao foi possivel exportar: {message}", title="erro", severity="error", markup=False)


def _series(report: HistoryReport) -> Series:
    return [("Patrimonio", [float(point.value) for point in report.points])]


def _note_for(report: HistoryReport) -> str:
    granularity = _GRANULARITY[report.granularity]
    lines = [f"[dim]{len(report.points)} pontos, amostragem {granularity}[/dim]"]
    if report.excluded:
        excluded = escape(", ".join(report.excluded))
        lines.append(f"[yellow]Nota:[/yellow] patrimonio nao considera {excluded} (sem historico de precos).")
    return "\n".join(lines)
