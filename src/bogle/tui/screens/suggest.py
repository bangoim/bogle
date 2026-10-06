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

``q`` pins the purchase of the highlighted ticker — whole shares for variable
income, the value for fixed income — and that one does move the split: the rest
of the amount goes to the other tickers, so the contribution keeps adding up. Zero
takes the ticker out of this contribution. Same lifetime as the prices.

A target the provider cannot quote (a fund on its first day, say) still gets a
row, at the bottom and marked "sem cotacao": it is the same key that brings it
into the split, and a ticker that only shows up in a warning has no row for ``p``
to act on.
"""

from __future__ import annotations

from decimal import Decimal
from typing import ClassVar, override

from rich.columns import Columns
from rich.console import Group
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.widgets import DataTable, Footer, Header, Input, Static
from textual.widgets.data_table import CellDoesNotExist

from bogle import format as fmt
from bogle.cli.parsing import parse_decimal
from bogle.domain.assets import VARIABLE_INCOME_TYPES
from bogle.domain.errors import ValidationError
from bogle.position import local_time, price_provenance
from bogle.rebalancing import AporteSuggestion, TickerSuggestion, UnquotedTarget, check_pinned_purchase
from bogle.tui import cells, services
from bogle.tui.screens.data import DataScreen
from bogle.tui.screens.modals import EditModal
from bogle.tui.validators import DecimalField
from bogle.tui.widgets.form import Field

_COLUMNS = (
    "Ticker",
    "Preço",
    "Valor",
    "Qtde",
    "Custo",
    # Onde o ticker deveria estar, onde ele esta, onde ele fica depois deste
    # aporte e o que ainda falta: sozinho, o peso final nao explica nada.
    "Target",
    "Peso atual",
    "Peso após",
    "Drift após",
)

_HINT = "[dim]Informe o valor do aporte e pressione Enter.[/dim]"

_MANUAL_MARK = "*"
"""Marca o que veio do usuario — o preco, a compra fixada: sem ela a coluna
mistura o que voce informou com o que veio do provedor ou da divisao."""

_UNQUOTED = "sem cotação"

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
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("p", "set_price", "Preço"),
        Binding("q", "set_purchase", "Qtde"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.amount: Decimal | None = None
        """The contribution asked for; ``None`` until the field is submitted."""
        self.prices: dict[str, Decimal] = {}
        """Prices the user typed, per ticker — empty until ``p`` is used."""
        self.quantities: dict[str, Decimal] = {}
        """Whole shares pinned with ``q``, per variable-income ticker."""
        self.values: dict[str, Decimal] = {}
        """Values pinned with ``q``, per fixed-income ticker."""
        self.totals = ""
        """Plain text of the totals line (read by the tests)."""

    @override
    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="suggest"):
            yield Field(
                "Disponível para aporte",
                id="amount",
                placeholder="ex: 1500 (Enter calcula)",
                validators=[DecimalField("Valor disponível", positive=True)],
            )
            table = DataTable(id="allocation", cursor_type="row", zebra_stripes=True)
            table.add_columns(*_COLUMNS)
            yield table
            # Os avisos antes dos totais: eles dizem como ler os numeros de baixo.
            yield Static(id="suggest-note")
            yield Static(id="suggest-totals")
        yield Footer()

    # --- entrada --------------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        field = self.query_one("#amount", Field)
        if field.check() is not None:
            return
        self.amount = parse_decimal(field.value, "Valor disponível")
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
                f"{item.ticker} é renda fixa: um preço informado não se aplica (entra por valor, não por cota). "
                "Confira o ticker no cadastro do ativo.",
                severity="warning",
                markup=False,
            )
            return
        if item.asset_type not in VARIABLE_INCOME_TYPES:
            # Renda fixa entra por valor exato: nao ha cota para o preco converter.
            self.notify(
                f"{item.ticker} é renda fixa: entra por valor, não por cota, e não tem preço a definir.",
                severity="warning",
                markup=False,
            )
            return
        current = self.prices.get(item.ticker)
        self.app.push_screen(
            EditModal(
                f"Preço de {item.ticker}",
                _price_body(item),
                value=_as_typed(current),
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
        field = f"Preço de {ticker}"
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

    # --- compra fixada ---------------------------------------------------

    def action_set_purchase(self) -> None:
        item = self.selected
        if item is None:
            return
        variable = item.asset_type in VARIABLE_INCOME_TYPES
        if isinstance(item, UnquotedTarget):
            # Sem preco nao ha o que as cotas custem; e a renda fixa sem cotacao e
            # um cadastro a conferir, como no p.
            self.notify(
                f"{item.ticker} está sem cotação: informe o preço (p) antes da quantidade."
                if variable
                else f"{item.ticker} está sem cotação: confira o ticker no cadastro do ativo.",
                severity="warning",
                markup=False,
            )
            return
        pinned = self.quantities if variable else self.values
        self.app.push_screen(
            EditModal(
                f"{'Quantidade' if variable else 'Valor'} de {item.ticker}",
                f"{'Cotas inteiras' if variable else 'Valor em reais'}; o resto do aporte vai para os outros.\n"
                "0 tira o ticker do aporte, em branco volta a sugestão.",
                value=_as_typed(pinned.get(item.ticker)),
                placeholder="ex: 10" if variable else "ex: 500,00",
            ),
            lambda raw: self._on_purchase(item, raw),
        )

    def _on_purchase(self, item: TickerSuggestion, raw: str | None) -> None:
        if raw is None:  # Esc
            return
        variable = item.asset_type in VARIABLE_INCOME_TYPES
        pinned = self.quantities if variable else self.values
        if not raw.strip():
            pinned.pop(item.ticker, None)
            self.fetch()
            return
        try:
            number = parse_decimal(raw, f"{'Quantidade' if variable else 'Valor'} de {item.ticker}")
            check_pinned_purchase(item.ticker, item.asset_type, number)
        except ValidationError as exc:
            # Como no preco: recusado aqui, a tabela que estava a vista fica.
            self.notify(str(exc), title="erro", severity="error", timeout=10, markup=False)
            return
        pinned[item.ticker] = number
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
        return services.load_suggestion(
            self.amount,
            prices=self.prices,
            quantities=self.quantities,
            values=self.values,
            refresh=self.refresh_quotes,
        )

    @override
    def clear_content(self) -> None:
        self.query_one(DataTable).clear()
        self._show_totals([])

    @override
    def render_report(self, report: AporteSuggestion) -> None:
        table = self.query_one(DataTable)
        # Um p ou um q reordena as linhas (elas vao pelo custo): o cursor segue o
        # ticker, e nao a posicao, para a proxima tecla cair onde se esta olhando.
        cursor = _cursor_key(table)
        table.clear()  # mantem as colunas
        for item in report.items:
            table.add_row(
                cells.ticker(item.ticker),
                _price_cell(item),
                cells.money(item.allocation),
                _quantity_cell(item),
                _cost_cell(item),
                cells.pct(item.target_weight),
                cells.pct(item.current_weight),
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
                cells.pct(target.target_weight),
                cells.pct(target.current_weight),
                cells.pct(target.weight_after),
                cells.signed(target.drift_after, percent=True),
                key=target.ticker,
            )
        if cursor is not None and cursor in table.rows:
            table.move_cursor(row=table.get_row_index(cursor))
        # O aporte fica fora: ele ja esta no campo, e o que importa aqui e o que
        # as compras custam e o que isso deixa em caixa.
        self._show_totals(
            [
                f"[dim]Alocado[/dim] {fmt.money(report.total_allocated)}",
                f"[dim]Taxa B3 (est.)[/dim] {fmt.money(report.estimated_fees)}",
                f"[dim]Custo[/dim] {fmt.money(report.total_with_fees)}",
                f"[dim]Sobra (caixa)[/dim] {fmt.shortfall(report.leftover)}",
            ],
            _provenance_markup(report),
        )
        self.show_note(fmt.attention(report.warnings))
        # Com a sugestao na tela o campo ja cumpriu o seu papel, e enquanto um
        # Input tem foco o textual desativa os atalhos de uma letra (r, h, ?).
        # So aqui, e nao no submit: um widget em estado de carga nao aceita foco.
        if self.focused is self.query_one("#amount", Field).input:
            table.focus()

    def _show_totals(self, pairs: list[str], footnote: str = "") -> None:
        totals = [Text.from_markup(pair) for pair in pairs]
        lines = [Text("   ").join(totals), Text.from_markup(footnote)]
        self.totals = "\n".join(line.plain for line in lines if line.plain)
        # Um par por celula: todos numa linha quando cabem, e em 80 colunas a
        # quebra cai entre um par e outro, nunca entre o rotulo e o valor.
        content = Group(Columns(totals, padding=(0, 3)), lines[1]) if totals else Text()
        self.query_one("#suggest-totals", Static).update(content)


def _rows(report: AporteSuggestion) -> list[_Row]:
    """The table's rows, in its order: what the contribution buys, then what it left out."""
    return [*report.items, *report.unquoted]


def _cursor_key(table: DataTable[object]) -> str | None:
    """The ticker under the cursor, or ``None`` on an empty table."""
    try:
        return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
    except CellDoesNotExist:
        return None


def _as_typed(number: Decimal | None) -> str:
    """A number as the modal takes it back: plain digits, never masked ("" when unset)."""
    return "" if number is None else format(number.normalize(), "f")


def _price_cell(item: TickerSuggestion) -> Text:
    """The price used, marked when it is the user's and not the provider's."""
    if not item.is_manual_price:
        return cells.money(item.price)
    return cells.right(f"{fmt.money(item.price)} {_MANUAL_MARK}")


def _quantity_cell(item: TickerSuggestion) -> Text:
    """Whole shares, marked when pinned; a dash for fixed income, which buys a value."""
    if item.quantity is None:
        return cells.right(fmt.DASH)
    if not item.is_pinned:
        return cells.exact(item.quantity)
    return cells.right(f"{fmt.exact(item.quantity)} {_MANUAL_MARK}")


def _cost_cell(item: TickerSuggestion) -> Text:
    """The cost, marked when it is the value pinned for a fixed-income ticker."""
    if not item.is_pinned or item.quantity is not None:
        return cells.money(item.effective_cost)
    return cells.right(f"{fmt.money(item.effective_cost)} {_MANUAL_MARK}")


def _price_body(item: _Row) -> str:
    """What the price modal says about the market, and what an empty field does."""
    if isinstance(item, UnquotedTarget) or item.quoted_price is None:
        # Sem cotacao, "voltar ao de mercado" e voltar a ficar de fora.
        return "Sem cotação do provedor: com um preço, o ticker entra no aporte.\nEm branco fica fora dele."
    return f"Mercado: {_quote_of(item)}\nEm branco volta a usar a cotação."


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
        parts.append(f"[dim]Fonte(s)[/dim] {', '.join(provenance.sources)}")
    if provenance.latest is not None:
        parts.append(f"[dim]Cotação[/dim] {provenance.latest:%Y-%m-%d %H:%M}")
    return "   ".join(parts)
