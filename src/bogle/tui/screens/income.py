"""Income screen: what was received, by month or by ticker (issue #75).

Same rows as ``bogle dividends`` — JCP net of the tax withheld at source, the
rest gross, empty months kept so the series reads as continuous. ``t`` switches
the window (last 12 calendar months / since inception) and ``g`` the grouping.

Switching the grouping costs no round trip: both groupings come from the same
ledger read (see :class:`bogle.tui.services.IncomeReport`).
"""

from __future__ import annotations

from decimal import Decimal
from typing import ClassVar, override

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.widgets import DataTable, Footer, Header, Static

from bogle import format as fmt
from bogle.tui import cells, services
from bogle.tui.screens.data import PeriodScreen

_ZERO = Decimal("0")

_MONTH_COLUMNS = ("Mes", "Dividendos", "JCP (liq)", "FII rend.", "Juros RF", "Total")
_TICKER_COLUMNS = ("Ticker", "Tipo", "Total")
_MONTH_FIELDS = ("dividend", "jcp", "rendimento", "interest", "total")

_EMPTY = "Nenhum provento no periodo."
_LEGEND = "JCP liquido do IR retido na fonte; os outros tipos, valor bruto."


class IncomeScreen(PeriodScreen[services.IncomeReport]):
    SUBJECT = "proventos"
    PERIODS = ("12m", "all")
    AUTO_FOCUS = "#income-rows"
    LOADING = "#income-rows"
    NOTE = "#income-note"
    BINDINGS: ClassVar[list[BindingType]] = [Binding("g", "toggle_grouping", "Agrupar")]

    def __init__(self) -> None:
        super().__init__()
        self.by_ticker = False
        """``False`` groups by calendar month (the command's default), ``True`` by ticker."""

    @override
    def subtitle(self) -> str:
        return f"proventos - {self.period} {'por ticker' if self.by_ticker else 'por mes'}"

    @override
    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="income"):
            yield DataTable(id="income-rows", cursor_type="row", zebra_stripes=True)
            yield Static(id="income-note")
        yield Footer()

    # --- acoes ----------------------------------------------------------

    def action_toggle_grouping(self) -> None:
        # Redesenha do que ja esta carregado: as duas visoes vieram na mesma leitura.
        self.by_ticker = not self.by_ticker
        self.sub_title = self.subtitle()
        if self.report is not None:
            self.render_report(self.report)

    # --- carga ----------------------------------------------------------

    @override
    def load(self) -> services.IncomeReport:
        return services.load_income(period=self.period)

    @override
    def clear_content(self) -> None:
        self.query_one(DataTable).clear()

    @override
    def render_report(self, report: services.IncomeReport) -> None:
        table = self.query_one(DataTable)
        table.clear(columns=True)  # as colunas mudam com o agrupamento
        table.add_columns(*(_TICKER_COLUMNS if self.by_ticker else _MONTH_COLUMNS))
        if self.by_ticker:
            self._rows_by_ticker(report)
        else:
            self._rows_by_month(report)
        self.show_note(_note_for(report, by_ticker=self.by_ticker))

    def _rows_by_month(self, report: services.IncomeReport) -> None:
        table = self.query_one(DataTable)
        for row in report.by_month:
            table.add_row(
                cells.text(f"{row.month:%Y-%m}"),
                cells.money(row.dividend),
                cells.money(row.jcp),
                cells.money(row.rendimento),
                cells.money(row.interest),
                cells.money(row.total),
                key=row.month.isoformat(),
            )
        if not report.by_month:
            return
        table.add_row(
            cells.total("TOTAL"),
            *(
                cells.total(fmt.money(sum((getattr(row, field) for row in report.by_month), _ZERO)))
                for field in _MONTH_FIELDS
            ),
            key="total",
        )

    def _rows_by_ticker(self, report: services.IncomeReport) -> None:
        table = self.query_one(DataTable)
        for row in report.by_ticker:
            table.add_row(
                cells.ticker(row.ticker),
                cells.text(row.income_type.value),
                cells.money(row.total),
                key=f"{row.ticker}-{row.income_type.value}",
            )
        if not report.by_ticker:
            return
        total = sum((row.total for row in report.by_ticker), _ZERO)
        table.add_row(cells.total("TOTAL"), cells.text(""), cells.total(fmt.money(total)), key="total")


def _note_for(report: services.IncomeReport, *, by_ticker: bool) -> str:
    rows = report.by_ticker if by_ticker else report.by_month
    if not rows:
        return f"[yellow]{_EMPTY}[/yellow]"
    start = report.start.isoformat() if report.start is not None else "o inicio"
    return f"[dim]De {start} a {report.end.isoformat()}. {_LEGEND}[/dim]"
