"""Profitability screen: TWR over the three windows (issue #75).

The ``bogle return`` panel, tabular: one row per window (total / 12 months /
last month), and two columns per index — its own accumulated return and the
distance to the portfolio, in percentage points. Which indices to show is
editable in place (``i``), so measuring against the CDI and then against the
IBOV never leaves the screen.
"""

from __future__ import annotations

from itertools import chain
from typing import ClassVar, override

from rich.markup import escape
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.widgets import DataTable, Footer, Header, Static

from bogle.reports.returns import PeriodReturn, ReturnsReport
from bogle.tui import cells, services
from bogle.tui.screens.data import DataScreen
from bogle.tui.widgets.indices import IndicesInput

_LABELS = {"total": "Total", "12m": "12 meses", "1m": "Ultimo mes"}

_TWR_LEGEND = "TWR: exclui o efeito de aportes e retiradas e considera proventos."


class ReturnsScreen(DataScreen[ReturnsReport]):
    SUB_TITLE = "rentabilidade"
    # A tabela nasce com o foco: enquanto o campo de indices o tem, o textual
    # desativa os atalhos de uma letra (r/i).
    AUTO_FOCUS = "#periods"
    LOADING = "#periods"
    NOTE = "#returns-note"
    BINDINGS: ClassVar[list[BindingType]] = [Binding("i", "focus_indices", "Indices")]

    def __init__(self) -> None:
        super().__init__()
        self.indices: tuple[str, ...] | None = None
        """Indices in use; ``None`` until the worker reads the configured default."""
        self._showed_indices = False

    @override
    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="returns"):
            yield IndicesInput(id="indices")
            yield DataTable(id="periods", cursor_type="row", zebra_stripes=True)
            yield Static(id="returns-note")
        yield Footer()

    # --- acoes ----------------------------------------------------------

    def action_focus_indices(self) -> None:
        self.query_one(IndicesInput).focus_input()

    def on_indices_input_applied(self, event: IndicesInput.Applied) -> None:
        self.indices = event.indices
        self.fetch()

    # --- carga ----------------------------------------------------------

    @override
    def load(self) -> ReturnsReport:
        if self.indices is None:
            self.indices = services.default_indices()
        return services.load_returns(indices=self.indices)

    @override
    def clear_content(self) -> None:
        self.query_one(DataTable).clear()

    @override
    def render_report(self, report: ReturnsReport) -> None:
        indices = self.indices or ()
        if not self._showed_indices:
            # Uma vez so: depois disso o campo e do usuario, e recarregar nao
            # pode desfazer o que ele digitou.
            self.query_one(IndicesInput).show(indices)
            self._showed_indices = True

        table = self.query_one(DataTable)
        table.clear(columns=True)  # o numero de colunas depende dos indices
        table.add_columns(
            "Periodo",
            "Janela",
            "Carteira (TWR)",
            *chain.from_iterable((index, f"vs {index}") for index in indices),
        )
        for row in report.rows:
            table.add_row(
                cells.text(_LABELS[row.period]),
                cells.text(_window(row)),
                cells.signed(row.twr, percent=True),
                *chain.from_iterable(_versus(row, index) for index in indices),
                key=row.period,
            )
        self.show_note(_note_for(report))


def _window(row: PeriodReturn) -> str:
    """``1m`` is a closed window; the other two run from a start to today."""
    if row.period == "1m":
        return f"{row.start.isoformat()} a {row.end.isoformat()}"
    return f"desde {row.start.isoformat()}"


def _versus(row: PeriodReturn, index: str) -> tuple[Text, Text]:
    """The index's own return, and how far the portfolio is from it."""
    index_return = row.index_returns.get(index)
    difference = row.twr - index_return if row.twr is not None and index_return is not None else None
    return cells.signed(index_return, percent=True), cells.points(difference)


def _note_for(report: ReturnsReport) -> str:
    lines = [f"[dim]{_TWR_LEGEND}[/dim]"]
    if report.excluded:
        excluded = escape(", ".join(report.excluded))
        lines.append(f"[yellow]Nota:[/yellow] TWR nao considera {excluded} (sem historico de precos).")
    lines.extend(
        f"[yellow]Nota:[/yellow] {escape(index)}: {escape(message)}" for index, message in report.index_errors.items()
    )
    return "\n".join(lines)
