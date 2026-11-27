"""Withdrawal screen: which tickers to sell in retirement (issue #80).

Same suggestion as ``bogle withdraw`` — the tickers the glide path is taking out
first, then whatever is above its target, then everyone by weight; whole shares
rounded up for variable income, exact values for fixed income — and, like it,
only a suggestion: nothing is written, the sale goes through Registrar > Venda.

The value is the gross. The table says what each sale pays in IR and leaves net,
and the totals compare the net with what was asked.

As in the Aporte screen, the value is the one thing the screen cannot guess, so
it opens on the field and nothing is fetched until there is one.
"""

from __future__ import annotations

from decimal import Decimal
from typing import override

from rich.columns import Columns
from rich.console import Group
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Footer, Header, Input, Static

from bogle import format as fmt
from bogle.cli.parsing import parse_decimal
from bogle.position import price_provenance
from bogle.rebalancing import WithdrawalSuggestion
from bogle.tui import cells, services
from bogle.tui.screens.data import DataScreen
from bogle.tui.validators import DecimalField
from bogle.tui.widgets.form import Field

_COLUMNS = (
    "Ticker",
    "Preço",
    "Qtde",
    "Bruto",
    "IR (est.)",
    "Líquido",
    "Target",
    "Peso atual",
    "Peso após",
)

_HINT = "[dim]Informe o valor bruto do saque e pressione Enter.[/dim]"


class WithdrawScreen(DataScreen[WithdrawalSuggestion]):
    SUB_TITLE = "saque"
    AUTO_FOCUS = "#withdraw-amount Input"
    LOADING = "#sales"
    NOTE = "#withdraw-note"
    LIVE_PRICES = True

    def __init__(self) -> None:
        super().__init__()
        self.amount: Decimal | None = None
        """The withdrawal asked for; ``None`` until the field is submitted."""
        self.totals = ""
        """Plain text of the totals line (read by the tests)."""

    @override
    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="withdraw"):
            yield Field(
                "Valor do saque (bruto)",
                id="withdraw-amount",
                placeholder="ex: 5000 (Enter calcula)",
                validators=[DecimalField("Valor do saque", positive=True)],
                money=True,
            )
            table = DataTable(id="sales", cursor_type="row", zebra_stripes=True)
            table.add_columns(*_COLUMNS)
            yield table
            # Os avisos antes dos totais, como no Aporte: dizem como ler os numeros.
            yield Static(id="withdraw-note")
            yield Static(id="withdraw-totals")
        yield Footer()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        field = self.query_one("#withdraw-amount", Field)
        if field.check() is not None:
            return
        self.amount = parse_decimal(field.value, "Valor do saque")
        self.fetch()

    @override
    def fetch(self, *, refresh: bool = False) -> None:
        if self.amount is None:
            self.show_note(_HINT)
            return
        super().fetch(refresh=refresh)

    @override
    def load(self) -> WithdrawalSuggestion:
        assert self.amount is not None  # fetch() so chega aqui com valor
        return services.load_withdrawal(self.amount, refresh=self.refresh_quotes)

    @override
    def clear_content(self) -> None:
        self.query_one(DataTable).clear()
        self._show_totals([])

    @override
    def render_report(self, report: WithdrawalSuggestion) -> None:
        table = self.query_one(DataTable)
        table.clear()  # mantem as colunas
        for item in report.items:
            table.add_row(
                cells.ticker(item.ticker),
                cells.money(item.price),
                cells.exact(item.quantity),  # traco na renda fixa, que sai por valor
                cells.money(item.gross),
                cells.money(item.income_tax),
                cells.money(item.net),
                cells.pct(item.target_weight),
                cells.pct(item.current_weight),
                cells.pct(item.weight_after),
                key=item.ticker,
            )
        # O saque fica fora: ele ja esta no campo.
        self._show_totals(
            [
                f"[dim]Bruto[/dim] {fmt.money(report.total_gross)}",
                f"[dim]IR (est.)[/dim] {fmt.money(report.total_tax)}",
                f"[dim]Líquido[/dim] {fmt.money(report.total_net)}",
                f"[dim]Resíduo (cotas inteiras)[/dim] {fmt.shortfall(report.residual)}",
                f"[dim]Líquido vs saque[/dim] {fmt.signed(report.net_vs_amount, percent=False)}",
            ],
            _provenance_markup(report),
        )
        self.show_note(fmt.attention(report.warnings))
        # Com o resultado na tela o campo cumpriu o seu papel, e enquanto um
        # Input tem foco os atalhos de uma letra (r, h, ?) ficam desligados.
        if self.focused is self.query_one("#withdraw-amount", Field).input:
            table.focus()

    def _show_totals(self, pairs: list[str], footnote: str = "") -> None:
        totals = [Text.from_markup(pair) for pair in pairs]
        lines = [Text("   ").join(totals), Text.from_markup(footnote)]
        self.totals = "\n".join(line.plain for line in lines if line.plain)
        # Um par por celula: em 80 colunas a quebra cai entre um par e outro.
        content = Group(Columns(totals, padding=(0, 3)), lines[1]) if totals else Text()
        self.query_one("#withdraw-totals", Static).update(content)


def _provenance_markup(report: WithdrawalSuggestion) -> str:
    provenance = price_provenance((item.price_source, item.as_of) for item in report.items)
    parts = []
    if provenance.sources:
        parts.append(f"[dim]Fonte(s)[/dim] {', '.join(provenance.sources)}")
    if provenance.latest is not None:
        parts.append(f"[dim]Cotação[/dim] {provenance.latest:%Y-%m-%d %H:%M}")
    return "   ".join(parts)
