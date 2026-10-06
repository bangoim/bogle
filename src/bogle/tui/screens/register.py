"""Recording screens: buy, sell and income (issue #74).

The pain that motivated the whole interface: recording an operation without
memorizing flags, and fixing a typo *before* it becomes a row in the database.
Every field is visible at once, validated as it is typed, and a summary modal
shows what will be written. Nothing here re-implements the ledger — it all goes
through the same ``TransactionRepository`` the CLI uses.

After a successful write the screen asks what comes next: another entry of the
same kind (the common case, several tickers on the same day) or back to Home.

A buy is typed from nothing — any registered ticker can be bought, including one
that has never been bought before. A sale is not: it can only come out of a
position that exists, so it starts from the list of them
(:class:`SellPickerScreen`). The ticker is chosen instead of typed and then
checked against a list the user cannot see, and the choice carries what bounds
the form: how many shares there are to sell, which is both the ceiling on the
quantity and what "Vender tudo" fills in.

A sale that empties the position also clears the asset's target weight (see
:mod:`bogle.closeout`), and the form says so before anything else, with a button
that puts the target back — an intention the app changed on its own is exactly
what has to be shown and be undoable in one keystroke.
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar, override
from zoneinfo import ZoneInfo

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.suggester import SuggestFromList
from textual.widgets import Button, Checkbox, DataTable, Footer, Header, Label, Select, Static

from bogle import format as fmt
from bogle.cli.parsing import parse_date, parse_decimal
from bogle.closeout import ClearedTarget, cleared_notice
from bogle.db import DEFAULT_TIMEZONE
from bogle.domain.transactions import Transaction, TransactionType
from bogle.position import Position
from bogle.tui import cells, services
from bogle.tui.errors import HANDLED, message_for
from bogle.tui.navigation import back_to_home
from bogle.tui.screens.data import DataScreen
from bogle.tui.screens.menu import Entries, MenuScreen, items_of
from bogle.tui.screens.modals import GO_HOME, ClearedTargetModal, NextStepModal
from bogle.tui.screens.write import Entry, WriteScreen
from bogle.tui.validators import DateField, DecimalField, HeldShares, KnownTicker
from bogle.tui.widgets.form import ControlRow, Field
from bogle.tui.widgets.menu import Menu, MenuItem, menu_bindings


def _position_line(position: Position) -> Text:
    """What is being sold, in the same shape the Position screen states totals.

    Assembled instead of marked up: it is the one line on the form made of
    numbers the privacy mode masks, and a mask is not something to run a markup
    parser over.
    """
    fields = [
        ("Posição", f"{fmt.exact(position.quantity)} cotas"),
        ("Preço médio", fmt.money(position.average_price)),
    ]
    if position.price is not None:
        fields.append(("Cotação", fmt.money(position.price)))
    parts: list[str | tuple[str, str]] = []
    for label, value in fields:
        if parts:
            parts.append("   ")
        parts.extend([(label, "dim"), " ", value])
    return Text.assemble(*parts)


def _today() -> str:
    """Today in America/Sao_Paulo — the same default ``bogle buy`` uses.

    The machine's timezone would disagree with the CLI (and with the ledger) for
    anyone running from a different one.
    """
    return datetime.now(tz=ZoneInfo(DEFAULT_TIMEZONE)).date().isoformat()


_PICKER_COLUMNS = ("Ticker", "Tipo", "Qtd", "Preço médio", "Cotação", "Montante")

_PICKER_LEGEND = "enter (ou s) abre a venda da posição selecionada."

_NO_POSITIONS = "[yellow]Nenhuma posição aberta: só há o que vender depois de uma compra.[/yellow]"

_INCOME_LABELS = {
    TransactionType.DIVIDEND: "Dividendo",
    TransactionType.JCP: "JCP",
    TransactionType.RENDIMENTO: "Rendimento (FII)",
    TransactionType.INTEREST: "Juros (renda fixa)",
}

# Fabricas como lambda, e nao a classe direta: os formularios sao definidos
# abaixo, e uma lambda so procura o nome quando o item e escolhido.
_ENTRIES: Entries = (
    (
        MenuItem("1", "buy", "Compra", "quantidade, preço, taxas e data"),
        lambda: TradeFormScreen(kind=TransactionType.BUY),
    ),
    (
        MenuItem("2", "sell", "Venda", "posição aberta, inteira ou em parte"),
        lambda: SellPickerScreen(),
    ),
    (MenuItem("3", "income", "Provento", "dividendo, JCP, rendimento ou juros"), lambda: IncomeFormScreen()),
)

MENU_ITEMS = items_of(_ENTRIES)


class RegisterScreen(MenuScreen):
    """Which kind of entry to record."""

    SUB_TITLE = "registrar"
    AUTO_FOCUS = "#register-menu"
    ENTRIES = _ENTRIES
    MENU_TITLE = "Registrar"
    MENU_FRAME = "#register-menu"
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "app.pop_screen", "Voltar"),
        *menu_bindings(MENU_ITEMS),
    ]

    @override
    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="register"):
            yield Menu(MENU_ITEMS, id="register-menu")
        yield Footer()


class SellPickerScreen(DataScreen[list[Position]]):
    """Which position the sale comes out of.

    Everything a sale needs to be bounded is already known before the first
    keystroke — which tickers are held, and how many shares of each — so asking
    for the ticker as free text was asking the user to recall a list the app has.
    Worse, the form validated it against the *registered* assets, which include
    the ones never bought and the ones already sold out.

    The quote sits next to the average price on purpose: it is not what gets
    recorded (the sale is written at the price actually executed), but it is what
    the decision is made against, and it saves a trip to the Position screen.
    """

    SUB_TITLE = "venda - escolher a posição"
    AUTO_FOCUS = "#sell-positions"
    LOADING = "#sell-positions"
    NOTE = "#sell-note"
    LIVE_PRICES = True
    BINDINGS: ClassVar[list[BindingType]] = [Binding("s", "sell", "Vender")]

    @override
    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="sell-picker"):
            table = DataTable(id="sell-positions", cursor_type="row", zebra_stripes=True)
            table.add_columns(*_PICKER_COLUMNS)
            yield table
            yield Static(id="sell-note")
        yield Footer()

    # --- selecao --------------------------------------------------------

    @property
    def selected(self) -> Position | None:
        positions = self.report
        table = self.query_one(DataTable)
        if not positions or table.cursor_row < 0 or table.cursor_row >= len(positions):
            return None
        return positions[table.cursor_row]

    # --- acoes ----------------------------------------------------------

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        self.action_sell()

    def action_sell(self) -> None:
        position = self.selected
        if position is None:
            return
        # Recarrega ao voltar: a venda que acabou de ser gravada mudou a
        # quantidade desta linha (ou tirou a linha da lista).
        self.app.push_screen(TradeFormScreen(kind=TransactionType.SELL, position=position), lambda _: self.fetch())

    # --- carga ----------------------------------------------------------

    @override
    def load(self) -> list[Position]:
        return services.list_open_positions(refresh=self.refresh_quotes)

    @override
    def clear_content(self) -> None:
        self.query_one(DataTable).clear()

    @override
    def render_report(self, report: list[Position]) -> None:
        table = self.query_one(DataTable)
        table.clear()  # mantem as colunas
        for position in report:
            table.add_row(
                cells.ticker(position.ticker),
                cells.text(position.asset_type.value),
                cells.exact(position.quantity),
                cells.money(position.average_price),
                cells.money(position.price),
                cells.money(position.market_value),
                key=position.ticker,
            )
        self.show_note(_NO_POSITIONS if not report else _PICKER_LEGEND)


class FormScreen(WriteScreen[Transaction]):
    """The three ledger forms: validate, confirm, write, ask what comes next."""

    # Os dois seletores, e nao so o ticker: a venda nao tem campo de ticker (ele
    # foi escolhido na lista), e o `query` devolve na ordem do DOM — o ticker
    # ganha onde existe, a quantidade onde ele nao existe.
    AUTO_FOCUS = "#ticker Input, #shares Input"
    CONFIRM_TITLE = "Confirmar lançamento"
    CONFIRM_LABEL = "Registrar"
    WRITING_MESSAGE = "gravando o lançamento; um instante."

    def __init__(self) -> None:
        super().__init__()
        self.tickers = KnownTicker()
        self.recorded: Transaction | None = None
        """Last transaction written from this screen (also read by the tests)."""

    @override
    def written(self, transaction: Transaction) -> None:
        self.recorded = transaction
        summary = (
            f"transação {transaction.id} registrada: "
            f"{transaction.transaction_type} {transaction.ticker} em {transaction.date:%Y-%m-%d}."
        )
        self.notify(summary, title="pronto", markup=False)
        self.app.push_screen(NextStepModal(summary), self._next_step)

    def _next_step(self, choice: str | None) -> None:
        if choice == GO_HOME or choice is None:
            back_to_home(self.app)
            return
        self.clear()

    def clear(self) -> None:
        """Empty the form for another entry of the same kind."""
        fields = list(self.query(Field))
        for field in fields:
            field.reset()
        if fields:
            fields[0].input.focus()

    # --- autocomplete ---------------------------------------------------

    @work(thread=True, group="tickers")
    def load_tickers(self) -> None:
        try:
            tickers = services.list_tickers()
        except HANDLED:
            return  # sem autocomplete; o repositorio ainda valida o ticker
        self.app.call_from_thread(self._apply_tickers, tickers)

    def _apply_tickers(self, tickers: list[str]) -> None:
        self.tickers.learn(tickers)
        self.field("ticker").input.suggester = SuggestFromList(tickers, case_sensitive=False)


class TradeFormScreen(FormScreen):
    """Buy and sell: the same fields, plus what only a sale has.

    A sale arrives with its :class:`~bogle.position.Position` already chosen by
    :class:`SellPickerScreen`, which is what lets the form drop the ticker field,
    put a ceiling on the quantity and offer to fill it with the whole position. A
    buy arrives with nothing: any registered ticker can be bought, so it is typed.

    The two go together — a sale without a position and a buy with one are both
    states this screen has no shape for, and the constructor says so rather than
    quietly rendering the wrong form.
    """

    def __init__(self, *, kind: TransactionType, position: Position | None = None) -> None:
        super().__init__()
        self.kind = kind
        self.is_sale = kind is TransactionType.SELL
        if self.is_sale != (position is not None):
            raise ValueError("a venda sai de uma posição escolhida, e a compra não tem posição de partida.")
        self.position = position
        """A posicao sendo vendida; ``None`` na compra."""
        self.position_line = ""
        """Plain text of the position summary above the fields (read by the tests)."""
        self.sub_title = "registrar venda" if self.is_sale else "registrar compra"
        self.cleared: ClearedTarget | None = None
        """Target zerado pela ultima venda gravada, ate o dialogo resolve-lo."""

    @override
    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="form"):
            if self.position is None:
                yield Field(
                    "Ticker",
                    id="ticker",
                    placeholder="ativo cadastrado (ex: AUVP11)",
                    validators=[self.tickers],
                )
            else:
                # A posicao fica acima dos campos, e nao so no titulo da moldura:
                # a quantidade que se pode vender e o preco medio sao os numeros
                # contra os quais os proximos dois campos sao preenchidos.
                yield Static(id="sell-position")
            yield Field(
                "Quantidade",
                id="shares",
                placeholder="cotas negociadas",
                validators=[self._shares_validator()],
            )
            if self.position is not None:
                yield ControlRow("Vender tudo", Checkbox(id="sell-all", compact=True), id="sell-all-row")
            yield Field(
                "Preço unitário",
                id="price",
                placeholder="preço por cota",
                validators=[DecimalField("Preço unitário", positive=True)],
            )
            yield Field(
                "Taxas",
                id="fees",
                value="0",
                placeholder="corretagem e emolumentos",
                validators=[DecimalField("Taxas", feminine=True, plural=True)],
            )
            if self.is_sale:
                yield Field(
                    "IR retido na fonte",
                    id="tax",
                    value="0",
                    placeholder="dedo-duro de 0,005%",
                    validators=[DecimalField("IR retido")],
                )
            yield Field(
                "Data",
                id="date",
                value=_today(),
                placeholder="YYYY-MM-DD",
                validators=[DateField("Data", feminine=True)],
            )
            with Horizontal(id="form-buttons"):
                yield Button("Registrar", id="submit", variant="primary")
                yield Button("Voltar", id="back")
        yield Footer()

    def _shares_validator(self) -> DecimalField | HeldShares:
        """A quantidade da venda tem teto; a da compra, nao."""
        if self.position is None:
            return DecimalField("Quantidade", positive=True, feminine=True)
        return HeldShares(self.position.ticker, self.position.quantity)

    def on_mount(self) -> None:
        position = self.position
        self.query_one("#form").border_title = "Compra" if position is None else f"Venda - {position.ticker}"
        if position is None:
            self.load_tickers()  # so a compra digita o ticker
            return
        self.render_amounts()

    def render_amounts(self) -> None:
        """Redraw the position line after the privacy toggle (see ``BogleApp``)."""
        if self.position is None:
            return
        line = _position_line(self.position)
        self.position_line = line.plain
        self.query_one("#sell-position", Static).update(line)

    # --- vender tudo ----------------------------------------------------

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id != "sell-all" or self.position is None:
            return
        # Valor canonico, e nao `fmt.exact`: este e o texto que volta pelo parser
        # (e que o modo privacidade mascararia, gravando uma venda de "••••••").
        self.field("shares").set_locked(event.value, value=fmt.exact_or_none(self.position.quantity) or "")

    @override
    def collect(self) -> Entry | None:
        if not self.check_fields():
            return None
        entry: Entry = {
            "ticker": self.position.ticker if self.position is not None else self.field("ticker").value.upper(),
            "when": parse_date(self.field("date").value, "Data"),
            "shares": parse_decimal(self.field("shares").value, "Quantidade"),
            "unit_price": parse_decimal(self.field("price").value, "Preço unitário"),
            "fees": parse_decimal(self.field("fees").value, "Taxas"),
        }
        if self.is_sale:
            entry["tax_withheld"] = parse_decimal(self.field("tax").value, "IR retido")
        return entry

    @override
    def describe(self, entry: Entry) -> str:
        shares, price, fees = entry["shares"], entry["unit_price"], entry["fees"]
        gross = shares * price
        head = (
            f"{'Venda' if self.is_sale else 'Compra'}: {fmt.exact(shares)} x {entry['ticker']} "
            f"@ {fmt.money(price)} em {entry['when']:%Y-%m-%d}"
        )
        if self.is_sale:
            return (
                f"{head}\nTaxas {fmt.money(fees)}, IR retido {fmt.money(entry['tax_withheld'])}"
                f"\nProduto bruto da venda: {fmt.money(gross)}"
            )
        return f"{head}\nTaxas {fmt.money(fees)}\nCusto total: {fmt.money(gross + fees)}"

    @override
    def write(self, entry: Entry) -> Transaction:
        if not self.is_sale:
            return services.record_buy(**entry)
        outcome = services.record_sell(**entry)
        # Guardado aqui e lido em written(): a venda e o que o formulario gravou,
        # e o target zerado e uma consequencia dela que a tela tem de contar.
        self.cleared = outcome.cleared
        return outcome.transaction

    @override
    def written(self, transaction: Transaction) -> None:
        cleared, self.cleared = self.cleared, None
        if cleared is None:
            super().written(transaction)
            return
        # O dialogo do target vem antes do "o que fazer agora": e consequencia da
        # venda, e perguntar para onde ir antes de contar o que mudou esconderia
        # a mudanca atras de uma navegacao.
        self.app.push_screen(
            ClearedTargetModal(cleared_notice(cleared)),
            lambda revert: self._resolve_cleared(cleared, transaction, revert=bool(revert)),
        )

    def _resolve_cleared(self, cleared: ClearedTarget, transaction: Transaction, *, revert: bool) -> None:
        if revert:
            self._restore_target(cleared)
        super().written(transaction)

    @work(thread=True, group="restore")
    def _restore_target(self, cleared: ClearedTarget) -> None:
        try:
            services.update_asset(ticker=cleared.ticker, target_weight=cleared.previous_target)
        except HANDLED as exc:
            self.app.call_from_thread(self._restore_failed, message_for(exc))
            return
        self.app.call_from_thread(
            self.notify,
            f"target de {cleared.ticker} de volta em {fmt.pct(cleared.previous_target)}.",
            title="revertido",
            markup=False,
        )

    def _restore_failed(self, message: str) -> None:
        self.notify(message, title="erro", severity="error", timeout=10, markup=False)

    @override
    def clear(self) -> None:
        """ "Novo lancamento", which for a sale means another *position*.

        The ticker of this form was chosen on the way in and cannot be retyped,
        so emptying the fields would offer a second sale of the same position —
        with a quantity that the first sale just changed. Back to the list, which
        reloads as it comes into view.
        """
        if self.position is None:
            super().clear()
            return
        self.dismiss()


class IncomeFormScreen(FormScreen):
    """Income: the type drives whether withheld tax applies."""

    SUB_TITLE = "registrar provento"

    @override
    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="form"):
            yield Field(
                "Ticker",
                id="ticker",
                placeholder="ativo cadastrado (ex: MXRF11)",
                validators=[self.tickers],
            )
            with Vertical(classes="field"), Horizontal(classes="field-row"):
                yield Label("Tipo", classes="field-label")
                yield Select(
                    [(label, kind) for kind, label in _INCOME_LABELS.items()],
                    value=TransactionType.DIVIDEND,
                    allow_blank=False,
                    compact=True,
                    id="income-type",
                )
            yield Field(
                "Valor bruto",
                id="amount",
                placeholder="valor recebido, antes do IR",
                validators=[DecimalField("Valor bruto", positive=True)],
            )
            yield Field(
                "IR retido na fonte",
                id="tax",
                placeholder="opcional",
                validators=[DecimalField("IR retido", allow_blank=True)],
            )
            yield Field(
                "Data",
                id="date",
                value=_today(),
                placeholder="YYYY-MM-DD",
                validators=[DateField("Data", feminine=True)],
            )
            with Horizontal(id="form-buttons"):
                yield Button("Registrar", id="submit", variant="primary")
                yield Button("Voltar", id="back")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#form").border_title = "Provento"
        self.apply_income_type(self.income_type)
        self.load_tickers()

    @property
    def income_type(self) -> TransactionType:
        return self.query_one("#income-type", Select).value  # type: ignore[return-value]

    def on_select_changed(self, event: Select.Changed) -> None:
        if isinstance(event.value, TransactionType):
            self.apply_income_type(event.value)

    def apply_income_type(self, income_type: TransactionType) -> None:
        """Mirror the CLI's rule on the field itself.

        JCP always has 15% withheld at source (required); FII income is exempt
        for individuals, so the field does not apply and is disabled.
        """
        tax = self.field("tax")
        required = income_type is TransactionType.JCP
        # O validador troca *antes* de habilitar ou desabilitar: o textual valida
        # o Input por conta propria e pinta a borda, entao um validador do tipo
        # anterior deixaria a marca de erro num campo que nem se aplica.
        tax.input.validators = [
            DecimalField(
                "IR retido",
                allow_blank=not required,
                blank_message="IR retido é obrigatório para JCP (15% retido na fonte).",
            )
        ]
        if income_type is TransactionType.RENDIMENTO:
            tax.set_enabled(False, placeholder="não se aplica a RENDIMENTO (isento para PF)")
            return
        tax.set_enabled(True, placeholder="obrigatório para JCP" if required else "opcional")

    @override
    def clear(self) -> None:
        super().clear()
        self.apply_income_type(self.income_type)

    @override
    def collect(self) -> Entry | None:
        if not self.check_fields():
            return None
        tax = self.field("tax")
        return {
            "ticker": self.field("ticker").value.upper(),
            "income_type": self.income_type,
            "when": parse_date(self.field("date").value, "Data"),
            "amount": parse_decimal(self.field("amount").value, "Valor bruto"),
            "tax_withheld": parse_decimal(tax.value, "IR retido") if tax.enabled and tax.value else None,
        }

    @override
    def describe(self, entry: Entry) -> str:
        label = _INCOME_LABELS[entry["income_type"]]
        withheld = entry["tax_withheld"]
        lines = [
            f"{label}: {entry['ticker']} em {entry['when']:%Y-%m-%d}",
            f"Valor bruto: {fmt.money(entry['amount'])}",
        ]
        if withheld is not None:
            lines.append(f"IR retido: {fmt.money(withheld)}")
            lines.append(f"Líquido: {fmt.money(entry['amount'] - withheld)}")
        return "\n".join(lines)

    @override
    def write(self, entry: Entry) -> Transaction:
        return services.record_income(**entry)
