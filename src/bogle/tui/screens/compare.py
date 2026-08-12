"""Comparison screen: portfolio vs indices, base 100 (issue #75).

Same numbers as ``bogle compare`` — the portfolio series is the cumulative TWR
level, so contributions never read as performance — with the window (``t``) and
the indices (``i``) switchable without leaving the screen. ``o`` writes the same
interactive HTML the command's ``--output`` writes and opens it in the browser.
"""

from __future__ import annotations

from typing import ClassVar, override

from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.widgets import DataTable, Footer, Header, Static

from bogle.charts import Series
from bogle.reports.compare import CompareReport
from bogle.tui import cells, services
from bogle.tui.errors import HANDLED, message_for
from bogle.tui.screens.data import PeriodScreen
from bogle.tui.widgets.chart import LineChart
from bogle.tui.widgets.indices import IndicesInput

_CHART_TITLE = "Base 100 no inicio do periodo"
_EXPORT_TITLE = "Carteira v. Índices"


class CompareScreen(PeriodScreen[CompareReport]):
    SUBJECT = "comparar"
    PERIODS = ("12m", "2y", "5y", "10y", "ytd", "all")
    AUTO_FOCUS = "#series"
    LOADING = "#series"
    NOTE = "#compare-note"
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("i", "focus_indices", "Indices"),
        Binding("o", "export", "Exportar"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.indices: tuple[str, ...] | None = None
        """Indices in use; ``None`` until the worker reads the configured default."""
        self._showed_indices = False

    @override
    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="compare"):
            yield IndicesInput(id="indices")
            table = DataTable(id="series", cursor_type="row", zebra_stripes=True)
            table.add_columns("Serie", "Retorno")
            yield table
            yield LineChart(id="chart")
            yield Static(id="compare-note")
        yield Footer()

    # --- acoes ----------------------------------------------------------

    def action_focus_indices(self) -> None:
        self.query_one(IndicesInput).focus_input()

    def on_indices_input_applied(self, event: IndicesInput.Applied) -> None:
        self.indices = event.indices
        self.fetch()

    def action_export(self) -> None:
        report = self.report
        if report is None:
            self.notify("nada para exportar ainda.", severity="warning")
            return
        self._export(report, self.period)

    # --- carga ----------------------------------------------------------

    @override
    def load(self) -> CompareReport:
        if self.indices is None:
            self.indices = services.default_indices()
        return services.load_compare(period=self.period, indices=self.indices)

    @override
    def clear_content(self) -> None:
        self.query_one(DataTable).clear()
        self.query_one(LineChart).clear()

    @override
    def render_report(self, report: CompareReport) -> None:
        if not self._showed_indices:
            self.query_one(IndicesInput).show(self.indices or ())
            self._showed_indices = True

        table = self.query_one(DataTable)
        table.clear()  # mantem as colunas
        for series in report.series:
            table.add_row(cells.ticker(series.name), cells.signed(series.accumulated_return, percent=True))
        # Base 100 e um nivel, nao um valor em reais: o grafico continua legivel
        # (e exportavel) com o modo privacidade ligado.
        self.query_one(LineChart).draw(_CHART_TITLE, [on.isoformat() for on in report.grid], _series(report))
        self.show_note(_note_for(report))

    # --- export ---------------------------------------------------------

    @work(thread=True, group="export")
    def _export(self, report: CompareReport, period: str) -> None:
        try:
            path = services.export_chart(
                title=_EXPORT_TITLE,
                x_values=list(report.grid),
                # Base 100 -> retorno acumulado em % (baseline 0), como no --output.
                series=[(s.name, [float(level) - 100 for level in s.levels]) for s in report.series],
                path=services.chart_path(f"compare-{period}"),
                y_suffix="%",
            )
        except (*HANDLED, OSError) as exc:
            self.app.call_from_thread(self._export_failed, message_for(exc))
            return
        self.app.call_from_thread(self._exported, path)

    def _exported(self, path: object) -> None:
        self.notify(f"grafico salvo em {path}", title="exportado", timeout=8, markup=False)

    def _export_failed(self, message: str) -> None:
        self.notify(f"nao foi possivel exportar: {message}", title="erro", severity="error", markup=False)


def _series(report: CompareReport) -> Series:
    return [(series.name, [float(level) for level in series.levels]) for series in report.series]


def _note_for(report: CompareReport) -> str:
    window = f"{report.grid[0].isoformat()} a {report.grid[-1].isoformat()}"
    lines = [f"[dim]Janela {window} (base 100 no inicio)[/dim]"]
    if report.data_as_of is not None:
        # O ultimo ponto pode estar forward-filled: quem manda e a data do dado.
        lines[0] += f"   [dim]Dados ate {report.data_as_of.isoformat()}[/dim]"
    if report.excluded:
        excluded = escape(", ".join(report.excluded))
        lines.append(f"[yellow]Nota:[/yellow] a serie da carteira nao considera {excluded} (sem historico de precos).")
    lines.extend(
        f"[yellow]Nota:[/yellow] {escape(index)}: {escape(message)}" for index, message in report.index_errors.items()
    )
    return "\n".join(lines)
