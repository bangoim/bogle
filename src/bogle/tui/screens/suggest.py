"""Contribution screen: how to split an aporte (issue #76).

Same split as ``bogle suggest`` — needs measured against the future patrimony,
whole shares for variable income, no selling — and the same side effect: asking
for a suggestion *is* the cycle's evaluation (issue #24), so it stamps
``last_rebalance_date`` and the overdue reminder stops nagging.

The amount is the one thing the screen cannot guess, so it opens on the field
with the focus and nothing is fetched until there is a value.
"""

from __future__ import annotations

from decimal import Decimal
from typing import override

from rich.markup import escape
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Footer, Header, Input, Static

from bogle import format as fmt
from bogle.cli.parsing import parse_decimal
from bogle.rebalancing import AporteSuggestion
from bogle.tui import cells, services
from bogle.tui.screens.data import DataScreen
from bogle.tui.validators import DecimalField
from bogle.tui.widgets.form import Field

_COLUMNS = ("Ticker", "Preco", "Valor sugerido", "Qtde papeis", "Custo efetivo", "Peso apos aporte")

_HINT = "[dim]Informe o valor do aporte e pressione Enter.[/dim]"


class SuggestScreen(DataScreen[AporteSuggestion]):
    SUB_TITLE = "aporte"
    # Diferente das outras telas, o foco abre no campo: sem um valor nao ha nada
    # para mostrar, e digitar e a primeira coisa a fazer.
    AUTO_FOCUS = "#amount Input"
    LOADING = "#allocation"
    NOTE = "#suggest-note"

    def __init__(self) -> None:
        super().__init__()
        self.amount: Decimal | None = None
        """The contribution asked for; ``None`` until the field is submitted."""
        self.totals = ""
        """Plain text of the totals line (read by the tests)."""

    @override
    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="suggest"):
            yield Field(
                "Valor do aporte",
                id="amount",
                placeholder="ex: 1500 (Enter calcula)",
                validators=[DecimalField("Valor do aporte", positive=True)],
            )
            table = DataTable(id="allocation", cursor_type="row", zebra_stripes=True)
            table.add_columns(*_COLUMNS)
            yield table
            yield Static(id="suggest-totals")
            yield Static(id="suggest-note")
        yield Footer()

    # --- entrada --------------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        field = self.query_one("#amount", Field)
        if field.check() is not None:
            return
        self.amount = parse_decimal(field.value, "Valor do aporte")
        self.sub_title = f"aporte - {fmt.money(self.amount)}"
        self.fetch()

    # --- carga ----------------------------------------------------------

    @override
    def fetch(self) -> None:
        # Sem valor nao ha o que calcular: a tela abre explicando em vez de
        # chamar o servico com nada.
        if self.amount is None:
            self.show_note(_HINT)
            return
        super().fetch()

    @override
    def load(self) -> AporteSuggestion:
        assert self.amount is not None  # fetch() so chega aqui com valor
        return services.load_suggestion(self.amount)

    @override
    def clear_content(self) -> None:
        self.query_one(DataTable).clear()
        self._show_totals("")

    @override
    def render_report(self, report: AporteSuggestion) -> None:
        table = self.query_one(DataTable)
        table.clear()  # mantem as colunas
        for item in report.items:
            table.add_row(
                cells.ticker(item.ticker),
                cells.money(item.price),
                cells.money(item.allocation),
                # Renda fixa nao compra cotas inteiras: o valor exato e o custo.
                cells.exact(item.quantity) if item.quantity is not None else cells.right(fmt.DASH),
                cells.money(item.effective_cost),
                cells.pct(item.weight_after),
                key=item.ticker,
            )
        self._show_totals(
            f"[dim]Total alocado[/dim] {fmt.money(report.total_allocated)}"
            f"   [dim]Aporte[/dim] {fmt.money(report.amount)}"
            f"   [dim]Sobra (caixa)[/dim] {fmt.money(report.leftover)}"
        )
        self.show_note(_note_for(report))
        # Com a sugestao na tela o campo ja cumpriu o seu papel, e enquanto um
        # Input tem foco o textual desativa os atalhos de uma letra (r, h, ?).
        # So aqui, e nao no submit: um widget em estado de carga nao aceita foco.
        if self.focused is self.query_one("#amount", Field).input:
            table.focus()

    def _show_totals(self, markup: str) -> None:
        rendered = Text.from_markup(markup)
        self.totals = rendered.plain
        self.query_one("#suggest-totals", Static).update(rendered)


def _note_for(report: AporteSuggestion) -> str:
    lines = [f"[yellow]Atencao:[/yellow] {escape(warning)}" for warning in report.warnings]
    lines.append("[dim]Calcular uma sugestao conta como avaliacao do ciclo de rebalanceamento.[/dim]")
    return "\n".join(lines)
