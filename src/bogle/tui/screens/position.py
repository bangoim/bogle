"""Position screen: the live portfolio (issue #73).

Same columns and same numbers as ``bogle position`` — both call
:func:`~bogle.reports.snapshot.compute_snapshot` — with the table made
navigable. Prices come from the network, so the load runs in a worker thread and
the table shows its loading state meanwhile; ``p`` switches to the base-data-only
view (the equivalent of ``--no-prices``) and ``r`` refetches.
"""

from __future__ import annotations

from typing import ClassVar, override

from rich.markup import escape
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static
from textual.worker import get_current_worker

from bogle import format as fmt
from bogle.reports.snapshot import PortfolioSnapshot
from bogle.tui import cells, services
from bogle.tui.errors import HANDLED, message_for

_COLUMNS = (
    "Ticker",
    "Tipo",
    "Qtd",
    "Preco",
    "Valor",
    "Peso atual",
    "Target",
    "Drift",
    "PnL R$",
    "PnL %",
    "TWR",
)

_EMPTY = "Nenhuma posicao ativa."


class PositionScreen(Screen[None]):
    SUB_TITLE = "posicao"
    AUTO_FOCUS = "#positions"
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "app.pop_screen", "Voltar"),
        Binding("r", "reload", "Atualizar"),
        Binding("p", "toggle_prices", "Precos"),
    ]

    def __init__(self, *, with_prices: bool = True) -> None:
        super().__init__()
        self.with_prices = with_prices
        self.snapshot: PortfolioSnapshot | None = None
        """Last loaded snapshot; ``None`` until the worker finishes."""
        self.totals = ""
        """Plain text of the totals block (read by the tests)."""
        self.note = ""
        """Plain text of the note under the totals (read by the tests)."""

    @override
    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="position"):
            table = DataTable(id="positions", cursor_type="row", zebra_stripes=True)
            table.add_columns(*_COLUMNS)
            yield table
            yield Static(id="totals")
            yield Static(id="position-note")
        yield Footer()

    def on_mount(self) -> None:
        self._load()

    # --- acoes ----------------------------------------------------------

    def render_amounts(self) -> None:
        """Redraw the table and the totals after the privacy toggle."""
        if self.snapshot is not None:
            self._show(self.snapshot)

    def action_reload(self) -> None:
        self._load()

    def action_toggle_prices(self) -> None:
        self.with_prices = not self.with_prices
        self._load()

    # --- carga ----------------------------------------------------------

    def _load(self) -> None:
        # O modo fica no subtitulo (o cabecalho e mais visivel que um rodape).
        self.sub_title = "posicao - precos ao vivo" if self.with_prices else "posicao - sem precos"
        self.query_one(DataTable).loading = True
        self._fetch(self.with_prices)

    @work(thread=True, exclusive=True, group="position")
    def _fetch(self, with_prices: bool) -> None:
        worker = get_current_worker()
        try:
            snapshot = services.load_snapshot(with_prices=with_prices)
        except HANDLED as exc:
            if not worker.is_cancelled:
                self.app.call_from_thread(self._show_failure, message_for(exc))
            return
        # A thread nao para no meio: sem esse guard, uma carga lenta (com precos)
        # que termina depois de o usuario alternar para "sem precos" sobrescreveria
        # o resultado novo com dados velhos.
        if not worker.is_cancelled:
            self.app.call_from_thread(self._show, snapshot)

    def _show(self, snapshot: PortfolioSnapshot) -> None:
        self.snapshot = snapshot
        table = self.query_one(DataTable)
        table.clear()  # mantem as colunas
        for p in snapshot.summary.positions:
            table.add_row(
                cells.ticker(p.ticker),
                cells.text(p.asset_type.value),
                cells.exact(p.quantity),
                cells.money(p.price),
                cells.money(p.market_value),
                cells.pct(p.current_weight),
                cells.pct(p.target_weight),
                cells.signed(p.drift, percent=True),
                cells.signed(p.pnl, percent=False),
                cells.signed(p.pnl_percent, percent=True),
                cells.signed(p.twr, percent=True),
                key=p.ticker,
            )
        table.loading = False
        self._show_totals(_totals_markup(snapshot))
        self._show_note(_note_for(snapshot))

    def _show_failure(self, message: str) -> None:
        table = self.query_one(DataTable)
        table.clear()
        table.loading = False
        self._show_totals("")
        self._show_note(f"[red]{escape(message)}[/red]")
        self.notify(message, title="erro", severity="error", timeout=10, markup=False)

    def _show_totals(self, markup: str) -> None:
        rendered = Text.from_markup(markup)
        self.totals = rendered.plain
        self.query_one("#totals", Static).update(rendered)

    def _show_note(self, markup: str) -> None:
        rendered = Text.from_markup(markup)
        self.note = rendered.plain
        self.query_one("#position-note", Static).update(rendered)


def _totals_markup(snapshot: PortfolioSnapshot) -> str:
    summary = snapshot.summary
    priced = snapshot.has_prices
    value = fmt.money(summary.total_value) if priced else fmt.DASH
    pnl = fmt.signed(summary.total_pnl, percent=False) if priced else fmt.DASH
    pnl_percent = fmt.signed(summary.total_pnl_percent, percent=True) if priced else fmt.DASH
    lines = [
        f"[dim]Total investido[/dim] {fmt.money(summary.total_invested)}"
        f"   [dim]Patrimonio total[/dim] {value}"
        f"   [dim]Variacao[/dim] {pnl} ({pnl_percent})",
        f"[dim]Lucro do mes[/dim] {fmt.signed(snapshot.month_profit, percent=False)}"
        f"   [dim]Proventos (12m)[/dim] {fmt.signed(snapshot.income_12m, percent=False)}",
    ]
    sources = sorted({p.price_source for p in summary.positions if p.price_source})
    timestamps = [p.as_of for p in summary.positions if p.as_of is not None]
    provenance = []
    if sources:
        provenance.append(f"[dim]Fonte(s) de preco[/dim] {', '.join(sources)}")
    if timestamps:
        provenance.append(f"[dim]Cotacao mais recente[/dim] {max(timestamps):%Y-%m-%d %H:%M}")
    if provenance:
        lines.append("   ".join(provenance))
    return "\n".join(lines)


def _note_for(snapshot: PortfolioSnapshot) -> str:
    if not snapshot.summary.positions:
        return f"[yellow]{_EMPTY}[/yellow]"
    if snapshot.excluded:
        excluded = escape(", ".join(snapshot.excluded))
        return f"[yellow]Nota:[/yellow] lucro do mes nao considera {excluded} (sem historico de precos)."
    return ""
