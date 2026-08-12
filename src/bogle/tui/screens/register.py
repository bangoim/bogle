"""Recording screens: buy, sell and income (issue #74).

The pain that motivated the whole interface: recording an operation without
memorizing flags, and fixing a typo *before* it becomes a row in the database.
Every field is visible at once, validated as it is typed, and a summary modal
shows what will be written. Nothing here re-implements the ledger — it all goes
through the same ``TransactionRepository`` the CLI uses.

After a successful write the screen asks what comes next: another entry of the
same kind (the common case, several tickers on the same day) or back to Home.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar, override
from zoneinfo import ZoneInfo

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.suggester import SuggestFromList
from textual.widgets import Button, Footer, Header, Input, Label, Select

from bogle import format as fmt
from bogle.cli.parsing import parse_date, parse_decimal
from bogle.db import DEFAULT_TIMEZONE
from bogle.domain.transactions import Transaction, TransactionType
from bogle.tui import services
from bogle.tui.errors import HANDLED, message_for
from bogle.tui.navigation import back_to_home
from bogle.tui.screens.modals import GO_HOME, ConfirmModal, NextStepModal
from bogle.tui.validators import DateField, DecimalField, KnownTicker
from bogle.tui.widgets.form import Field
from bogle.tui.widgets.menu import Menu, MenuItem, menu_bindings

Entry = dict[str, Any]
"""The validated values, already shaped as the service call's keyword arguments."""


def _today() -> str:
    """Today in America/Sao_Paulo — the same default ``bogle buy`` uses.

    The machine's timezone would disagree with the CLI (and with the ledger) for
    anyone running from a different one.
    """
    return datetime.now(tz=ZoneInfo(DEFAULT_TIMEZONE)).date().isoformat()


_INCOME_LABELS = {
    TransactionType.DIVIDEND: "Dividendo",
    TransactionType.JCP: "JCP",
    TransactionType.RENDIMENTO: "Rendimento (FII)",
    TransactionType.INTEREST: "Juros (renda fixa)",
}

MENU_ITEMS = (
    MenuItem("1", "buy", "Compra", "quantidade, preco, taxas e data"),
    MenuItem("2", "sell", "Venda", "igual a compra, com IR retido"),
    MenuItem("3", "income", "Provento", "dividendo, JCP, rendimento ou juros"),
)


class RegisterScreen(Screen[None]):
    """Which kind of entry to record."""

    SUB_TITLE = "registrar"
    AUTO_FOCUS = "#register-menu"
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

    def on_mount(self) -> None:
        self.query_one(Menu).border_title = "Registrar"

    def action_open(self, item_id: str) -> None:
        if item_id == "income":
            self.app.push_screen(IncomeFormScreen())
            return
        kind = TransactionType.BUY if item_id == "buy" else TransactionType.SELL
        self.app.push_screen(TradeFormScreen(kind=kind))

    def on_option_list_option_selected(self, event: Menu.OptionSelected) -> None:
        if event.option.id is not None:
            self.action_open(event.option.id)


class FormScreen(Screen[None]):
    """Shared flow of the three forms: validate, confirm, write, ask what next."""

    AUTO_FOCUS = "#ticker Input"
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "app.pop_screen", "Voltar"),
        Binding("ctrl+s", "submit", "Registrar"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.tickers = KnownTicker()
        self.recorded: Transaction | None = None
        """Last transaction written from this screen (also read by the tests)."""

    # --- a implementar por cada formulario -------------------------------

    def collect(self) -> Entry | None:
        """Validated values, or ``None`` when the form is not ready."""
        raise NotImplementedError

    def describe(self, entry: Entry) -> str:
        """What the confirmation modal shows."""
        raise NotImplementedError

    def write(self, entry: Entry) -> Transaction:
        """Persist. Runs in a worker thread."""
        raise NotImplementedError

    # --- validacao ------------------------------------------------------

    def field(self, field_id: str) -> Field:
        return self.query_one(f"#{field_id}", Field)

    def check_fields(self) -> bool:
        """Validate every field, focusing the first one that fails."""
        failed = [field for field in self.query(Field) if field.check() is not None]
        if failed:
            failed[0].input.focus()
            return False
        return True

    # --- fluxo ----------------------------------------------------------

    def action_submit(self) -> None:
        entry = self.collect()
        if entry is None:
            return
        self.app.push_screen(
            ConfirmModal("Confirmar lancamento", self.describe(entry), confirm_label="Registrar"),
            lambda confirmed: self._on_confirmed(entry, confirmed),
        )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Enter em qualquer campo tenta gravar; o modal ainda pede confirmacao.
        event.stop()
        self.action_submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "submit":
            self.action_submit()
        elif event.button.id == "back":
            self.app.pop_screen()

    def _on_confirmed(self, entry: Entry, confirmed: bool | None) -> None:
        if confirmed:
            self._persist(entry)

    @work(thread=True, exclusive=True, group="record")
    def _persist(self, entry: Entry) -> None:
        try:
            transaction = self.write(entry)
        except HANDLED as exc:
            # Erro de validacao ou de banco mantem o usuario no formulario, com
            # os valores preenchidos, para corrigir — o ganho sobre a CLI.
            self.app.call_from_thread(self._failed, message_for(exc))
            return
        self.app.call_from_thread(self._succeeded, transaction)

    def _failed(self, message: str) -> None:
        self.notify(message, title="erro", severity="error", timeout=10, markup=False)

    def _succeeded(self, transaction: Transaction) -> None:
        self.recorded = transaction
        summary = (
            f"transacao {transaction.id} registrada: "
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
    """Buy and sell: the same fields, plus the withheld tax on a sale."""

    def __init__(self, *, kind: TransactionType) -> None:
        super().__init__()
        self.kind = kind
        self.is_sale = kind is TransactionType.SELL
        self.sub_title = "registrar venda" if self.is_sale else "registrar compra"

    @override
    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="form"):
            yield Field(
                "Ticker",
                id="ticker",
                placeholder="ativo cadastrado (ex: AUVP11)",
                validators=[self.tickers],
            )
            yield Field(
                "Quantidade",
                id="shares",
                placeholder="cotas negociadas",
                validators=[DecimalField("Quantidade", positive=True)],
            )
            yield Field(
                "Preco unitario",
                id="price",
                placeholder="preco por cota",
                validators=[DecimalField("Preco unitario", positive=True)],
            )
            yield Field(
                "Taxas",
                id="fees",
                value="0",
                placeholder="corretagem e emolumentos",
                validators=[DecimalField("Taxas")],
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
                validators=[DateField("Data")],
            )
            with Horizontal(id="form-buttons"):
                yield Button("Registrar", id="submit", variant="primary")
                yield Button("Voltar", id="back")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#form").border_title = "Venda" if self.is_sale else "Compra"
        self.load_tickers()

    @override
    def collect(self) -> Entry | None:
        if not self.check_fields():
            return None
        entry: Entry = {
            "ticker": self.field("ticker").value.upper(),
            "when": parse_date(self.field("date").value, "Data"),
            "shares": parse_decimal(self.field("shares").value, "Quantidade"),
            "unit_price": parse_decimal(self.field("price").value, "Preco unitario"),
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
        if self.is_sale:
            return services.record_sell(**entry)
        return services.record_buy(**entry)


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
                validators=[DateField("Data")],
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
        # O textual valida o Input por conta propria e marca `-invalid`, entao o
        # validador tem de acompanhar o tipo: sem isso, trocar de JCP para
        # RENDIMENTO deixaria a borda vermelha e o erro do tipo anterior num
        # campo que nem se aplica.
        if income_type is TransactionType.RENDIMENTO:
            tax.set_enabled(False, placeholder="nao se aplica a RENDIMENTO (isento para PF)")
            return
        tax.input.validators = [
            DecimalField(
                "IR retido",
                allow_blank=not required,
                blank_message="IR retido e obrigatorio para JCP (15% retido na fonte).",
            )
        ]
        tax.set_enabled(True, placeholder="obrigatorio para JCP" if required else "opcional")

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
            lines.append(f"Liquido: {fmt.money(entry['amount'] - withheld)}")
        return "\n".join(lines)

    @override
    def write(self, entry: Entry) -> Transaction:
        return services.record_income(**entry)
