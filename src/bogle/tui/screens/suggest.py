"""Contribution screen: how to split an aporte (issue #76).

Same split as ``bogle suggest`` — needs measured against the future patrimony,
whole shares for variable income, no selling, and every asset with a target
weight in the running (including the ones never bought) — and the same side
effect: asking for a suggestion *is* the cycle's evaluation (issue #24), so it
stamps ``last_rebalance_date`` and the overdue reminder stops nagging.

The table shows the whole trip and not only its destination: current weight,
target, weight after the contribution, and the drift still left. A final weight
on its own says nothing about whether the money went where it was needed.

The amount is the one thing the screen cannot guess, so it opens on the field
with the focus and nothing is fetched until there is a value.

``p`` sets the price of the highlighted ticker, for a limit order: the quote says
what the paper costs now, the price you type says what you are willing to pay,
and the shares and the effective cost are recomputed on it (the split itself is
not — see :func:`~bogle.rebalancing.suggest_allocation`). Prices typed here live
as long as the screen does: it is an order being planned, not portfolio data.

A target the provider cannot quote (a fund on its first day, say) still gets a
row, at the bottom and marked "sem cotacao": it is the same key that brings it
into the split, and a ticker that only shows up in a warning has no row for ``p``
to act on.
"""

from __future__ import annotations

from decimal import Decimal
from typing import ClassVar, override

from rich.markup import escape
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.widgets import DataTable, Footer, Header, Input, Static

from bogle import format as fmt
from bogle.cli.parsing import parse_decimal
from bogle.domain.assets import VARIABLE_INCOME_TYPES
from bogle.domain.errors import ValidationError
from bogle.position import local_time, price_provenance
from bogle.rebalancing import FEE_BASIS, AporteSuggestion, TickerSuggestion, UnquotedTarget
from bogle.tui import cells, services
from bogle.tui.screens.data import DataScreen
from bogle.tui.screens.modals import EditModal
from bogle.tui.validators import DecimalField
from bogle.tui.widgets.form import Field

_COLUMNS = (
    "Ticker",
    "Preco",
    "Valor sugerido",
    "Qtde papeis",
    "Custo efetivo",
    # Onde o ticker esta, onde ele deveria estar, onde ele fica depois deste
    # aporte e o que ainda falta: sozinho, o peso final nao explica nada.
    "Peso atual",
    "Target",
    "Peso apos",
    "Drift apos",
)

_HINT = "[dim]Informe o valor do aporte e pressione Enter.[/dim]"

_MANUAL_MARK = "*"
"""Marca o preco que veio do usuario: sem ela a coluna mistura os dois."""

_LEGEND = f"p define o preco de um ticker ({_MANUAL_MARK} marca os informados); em branco volta ao de mercado."

_UNQUOTED = "sem cotacao"

type _Row = TickerSuggestion | UnquotedTarget
"""Uma linha da tabela: um ticker do aporte, ou um target que ficou de fora."""


class SuggestScreen(DataScreen[AporteSuggestion]):
    SUB_TITLE = "aporte"
    # Diferente das outras telas, o foco abre no campo: sem um valor nao ha nada
    # para mostrar, e digitar e a primeira coisa a fazer.
    AUTO_FOCUS = "#amount Input"
    LOADING = "#allocation"
    NOTE = "#suggest-note"
    LIVE_PRICES = True
    BINDINGS: ClassVar[list[BindingType]] = [Binding("p", "set_price", "Preco")]

    def __init__(self) -> None:
        super().__init__()
        self.amount: Decimal | None = None
        """The contribution asked for; ``None`` until the field is submitted."""
        self.prices: dict[str, Decimal] = {}
        """Prices the user typed, per ticker — empty until ``p`` is used."""
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
        self.sub_title = _subtitle(self.amount)
        self.fetch()

    # --- preco informado -------------------------------------------------

    @property
    def selected(self) -> _Row | None:
        report = self.report
        if report is None:
            return None
        rows = _rows(report)
        table = self.query_one(DataTable)
        if table.cursor_row < 0 or table.cursor_row >= len(rows):
            return None
        return rows[table.cursor_row]

    def action_set_price(self) -> None:
        item = self.selected
        if item is None:
            return
        if isinstance(item, UnquotedTarget) and item.asset_type not in VARIABLE_INCOME_TYPES:
            # Aqui o preco nao e o problema, e informar um nao traria nada de volta.
            self.notify(
                f"{item.ticker} e renda fixa: um preco informado nao se aplica (entra por valor, nao por cota). "
                "Confira o ticker no cadastro do ativo.",
                severity="warning",
                markup=False,
            )
            return
        if item.asset_type not in VARIABLE_INCOME_TYPES:
            # Renda fixa entra por valor exato: nao ha cota para o preco converter.
            self.notify(
                f"{item.ticker} e renda fixa: entra por valor, nao por cota, e nao tem preco a definir.",
                severity="warning",
                markup=False,
            )
            return
        current = self.prices.get(item.ticker)
        self.app.push_screen(
            EditModal(
                f"Preco de {item.ticker}",
                _price_body(item),
                value=_price_text(current) if current is not None else "",
                placeholder="ex: 114,86",
            ),
            lambda raw: self._on_price(item.ticker, raw),
        )

    def _on_price(self, ticker: str, raw: str | None) -> None:
        if raw is None:  # Esc
            return
        if not raw.strip():
            self.prices.pop(ticker, None)
            self.fetch()
            return
        field = f"Preco de {ticker}"
        try:
            price = parse_decimal(raw, field)
            if price <= 0:
                raise ValidationError(f"{field} deve ser maior que zero, recebido {raw.strip()}.")
        except ValidationError as exc:
            # A tabela continua valida — so o que foi digitado nao serve. Recusar
            # aqui, e nao no motor, e o que evita que um dedo errado limpe a tela.
            self.notify(str(exc), title="erro", severity="error", timeout=10, markup=False)
            return
        self.prices[ticker] = price
        self.fetch()

    # --- carga ----------------------------------------------------------

    @override
    def fetch(self, *, refresh: bool = False) -> None:
        # Sem valor nao ha o que calcular: a tela abre explicando em vez de
        # chamar o servico com nada.
        if self.amount is None:
            self.show_note(_HINT)
            return
        super().fetch(refresh=refresh)

    @override
    def load(self) -> AporteSuggestion:
        assert self.amount is not None  # fetch() so chega aqui com valor
        return services.load_suggestion(self.amount, prices=self.prices, refresh=self.refresh_quotes)

    @override
    def clear_content(self) -> None:
        self.query_one(DataTable).clear()
        self._show_totals("")

    @override
    def render_report(self, report: AporteSuggestion) -> None:
        # O subtitulo tambem carrega um valor, entao ele e refeito no redraw: sem
        # isso, ligar a privacidade mascarava a tabela e deixava o aporte no
        # cabecalho, que e onde ele estava mais visivel.
        self.sub_title = _subtitle(report.amount)
        table = self.query_one(DataTable)
        table.clear()  # mantem as colunas
        for item in report.items:
            table.add_row(
                cells.ticker(item.ticker),
                _price_cell(item),
                cells.money(item.allocation),
                # Renda fixa nao compra cotas inteiras: o valor exato e o custo.
                cells.exact(item.quantity) if item.quantity is not None else cells.right(fmt.DASH),
                cells.money(item.effective_cost),
                cells.pct(item.current_weight),
                cells.pct(item.target_weight),
                cells.pct(item.weight_after),
                # Mesma convencao (e mesma cor) do Drift da tela de Posicao.
                cells.signed(item.drift_after, percent=True),
                key=item.ticker,
            )
        for target in report.unquoted:
            # No fim, e com os mesmos pesos das outras: o drift aberto e o tamanho
            # do que ficou de fora, e a linha e onde o p informa o preco.
            table.add_row(
                cells.ticker(target.ticker),
                Text(_UNQUOTED, style="yellow", justify="right"),
                cells.right(fmt.DASH),
                cells.right(fmt.DASH),
                cells.right(fmt.DASH),
                cells.pct(target.current_weight),
                cells.pct(target.target_weight),
                cells.pct(target.weight_after),
                cells.signed(target.drift_after, percent=True),
                key=target.ticker,
            )
        # Duas linhas, e nao uma: o que as compras custam, e o que isso deixa do
        # aporte. Numa so, em 80 colunas, a quebra cairia no meio de um par.
        self._show_totals(
            f"[dim]Total alocado[/dim] {fmt.money(report.total_allocated)}"
            f"   [dim]Taxa B3 (est.)[/dim] {fmt.money(report.estimated_fees)}"
            f"   [dim]Total com taxa[/dim] {fmt.money(report.total_with_fees)}"
            f"\n[dim]Aporte[/dim] {fmt.money(report.amount)}"
            f"   [dim]Sobra (caixa)[/dim] {fmt.shortfall(report.leftover)}"
            f"{_provenance_markup(report)}"
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


def _rows(report: AporteSuggestion) -> list[_Row]:
    """The table's rows, in its order: what the contribution buys, then what it left out."""
    return [*report.items, *report.unquoted]


def _subtitle(amount: Decimal) -> str:
    return f"aporte - {fmt.money(amount)}"


def _price_text(price: Decimal) -> str:
    """The price as the modal takes it back: plain digits, never masked."""
    return format(price.normalize(), "f")


def _price_cell(item: TickerSuggestion) -> Text:
    """The price used, marked when it is the user's and not the provider's."""
    if not item.is_manual_price:
        return cells.money(item.price)
    return cells.right(f"{fmt.money(item.price)} {_MANUAL_MARK}")


def _price_body(item: _Row) -> str:
    """What the price modal says about the market, and what an empty field does."""
    if isinstance(item, UnquotedTarget) or item.quoted_price is None:
        # Sem cotacao, "voltar ao de mercado" e voltar a ficar de fora.
        return "Sem cotacao do provedor: com um preco, o ticker entra no aporte.\nEm branco fica fora dele."
    return f"Mercado: {_quote_of(item)}\nEm branco volta a usar a cotacao."


def _quote_of(item: TickerSuggestion) -> str:
    """``115.48 (14:07)`` — the quote and when it was made, for the modal.

    The time is the point: a limit order is decided against the current price, and
    the number on the table can be minutes old (a delayed free plan plus a
    five-minute cache).
    """
    quote = fmt.money(item.quoted_price)
    return quote if item.as_of is None else f"{quote} ({local_time(item.as_of):%H:%M})"


def _provenance_markup(report: AporteSuggestion) -> str:
    provenance = price_provenance((item.price_source, item.as_of) for item in report.items)
    parts = []
    if provenance.sources:
        parts.append(f"[dim]Fonte(s) de preco[/dim] {', '.join(provenance.sources)}")
    if provenance.latest is not None:
        parts.append(f"[dim]Cotacao mais recente[/dim] {provenance.latest:%Y-%m-%d %H:%M}")
    return f"\n{'   '.join(parts)}" if parts else ""


def _note_for(report: AporteSuggestion) -> str:
    lines = [f"[yellow]Atencao:[/yellow] {escape(warning)}" for warning in report.warnings]
    if report.estimated_fees > 0:
        lines.append(f"[dim]{escape(FEE_BASIS)}[/dim]")
    lines.append(f"[dim]{_LEGEND}[/dim]")
    lines.append("[dim]Calcular uma sugestao conta como avaliacao do ciclo de rebalanceamento.[/dim]")
    return "\n".join(lines)
