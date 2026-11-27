"""Annual rebalance screen: the plan of the retirement phase's yearly sale (issue #81).

Same plan as ``bogle rebalance`` — what is out of the band sells (the tickers the
glide path is taking out down to the target of the year, the others down to
``target + tolerance``), with the IR of each sale, and the net buys what is below
target — and, like it, it writes nothing on its own: the trades go through
Registrar. ``f`` is ``--done``: it records the year as done, which is what
silences the reminder.

Nothing to type: the plan is the portfolio's, so the screen loads it on opening.
"""

from __future__ import annotations

from typing import ClassVar, override

from rich.columns import Columns
from rich.console import Group
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.widgets import DataTable, Footer, Header, Static

from bogle import format as fmt
from bogle.cli.rebalance import OPERATIONS, recorded_message
from bogle.position import price_provenance
from bogle.rebalancing import AnnualRebalance, RebalanceItem, Recommendation
from bogle.tui import cells, services
from bogle.tui.errors import HANDLED, message_for
from bogle.tui.screens.data import DataScreen

_COLUMNS = (
    "Ticker",
    "Operação",
    "Preço",
    "Qtde",
    "Valor",
    "IR (est.)",
    "Target",
    "Peso atual",
    "Peso após",
)


class RebalanceScreen(DataScreen[AnnualRebalance]):
    SUB_TITLE = "rebalanceamento anual"
    AUTO_FOCUS = "#trades"
    LOADING = "#trades"
    NOTE = "#rebalance-note"
    LIVE_PRICES = True
    BINDINGS: ClassVar[list[BindingType]] = [Binding("f", "done", "Feito")]

    def __init__(self) -> None:
        super().__init__()
        self.totals = ""
        """Plain text of the totals (read by the tests)."""

    @override
    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="rebalance"):
            table = DataTable(id="trades", cursor_type="row", zebra_stripes=True)
            table.add_columns(*_COLUMNS)
            yield table
            # Os avisos antes dos totais, como no Saque: dizem como ler os numeros.
            yield Static(id="rebalance-note")
            yield Static(id="rebalance-totals")
        yield Footer()

    @override
    def load(self) -> AnnualRebalance:
        return services.load_annual_rebalance(refresh=self.refresh_quotes)

    @override
    def clear_content(self) -> None:
        self.query_one(DataTable).clear()
        self._show_totals([])

    @override
    def render_report(self, report: AnnualRebalance) -> None:
        self.sub_title = f"rebalanceamento anual de {report.year} (tolerância {fmt.pct(report.threshold)})"
        table = self.query_one(DataTable)
        table.clear()  # mantem as colunas
        for item in report.items:
            table.add_row(
                cells.ticker(item.ticker),
                cells.text(OPERATIONS[item.recommendation]),
                cells.money(item.price),
                cells.exact(item.quantity),  # traco na renda fixa, que vai por valor
                _value(item),
                cells.money(item.income_tax),
                cells.pct(item.target_weight),
                cells.pct(item.current_weight),
                cells.pct(item.weight_after),
                key=item.ticker,
            )
        self._show_totals(
            [
                f"[dim]Vendas[/dim] {fmt.money(report.total_sold)}",
                f"[dim]IR (est.)[/dim] {fmt.money(report.total_tax)}",
                f"[dim]Para compras[/dim] {fmt.money(report.available)}",
                f"[dim]Compras[/dim] {fmt.money(report.total_bought)}",
                f"[dim]Taxa B3 (est.)[/dim] {fmt.money(report.estimated_fees)}",
                f"[dim]Sobra (caixa)[/dim] {fmt.shortfall(report.leftover)}",
            ],
            _provenance_markup(report),
        )
        self.show_note(fmt.attention(report.warnings))

    def _show_totals(self, pairs: list[str], footnote: str = "") -> None:
        totals = [Text.from_markup(pair) for pair in pairs]
        lines = [Text("   ").join(totals), Text.from_markup(footnote)]
        self.totals = "\n".join(line.plain for line in lines if line.plain)
        # Um par por celula: em 80 colunas a quebra cai entre um par e outro.
        content = Group(Columns(totals, padding=(0, 3)), lines[1]) if totals else Text()
        self.query_one("#rebalance-totals", Static).update(content)

    # --- registro -------------------------------------------------------

    def action_done(self) -> None:
        self._record()

    @work(thread=True, exclusive=True, group="rebalance-done")
    def _record(self) -> None:
        try:
            year = services.record_annual_rebalance()
        except HANDLED as exc:
            message = message_for(exc)
            self.app.call_from_thread(self.notify, message, title="erro", severity="error", timeout=10, markup=False)
            return
        self.app.call_from_thread(self.notify, recorded_message(year), title="pronto", markup=False)


def _value(item: RebalanceItem) -> Text:
    # Manter nao movimenta nada: traco, e nao um 0,00 que parece uma ordem.
    return cells.right(fmt.DASH) if item.recommendation is Recommendation.HOLD else cells.money(item.value)


def _provenance_markup(report: AnnualRebalance) -> str:
    provenance = price_provenance((item.price_source, item.as_of) for item in report.items)
    parts = []
    if provenance.sources:
        parts.append(f"[dim]Fonte(s)[/dim] {', '.join(provenance.sources)}")
    if provenance.latest is not None:
        parts.append(f"[dim]Cotação[/dim] {provenance.latest:%Y-%m-%d %H:%M}")
    return "   ".join(parts)
