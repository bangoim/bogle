"""Transactions screen: the ledger, filtered and prunable (issue #74).

Covers ``bogle transactions`` and ``bogle transaction remove`` with the same
columns. Two differences the interface can afford: the filter matches any part
of the ticker as you type (the command takes an exact one), and removing asks
for confirmation — an ID is easy to mistype, and a deleted row is gone.
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
from textual.widgets import DataTable, Footer, Header, Input, Static
from textual.worker import get_current_worker

from bogle import format as fmt
from bogle.domain.transactions import Transaction, TransactionType
from bogle.tui import cells, services
from bogle.tui.errors import HANDLED, message_for
from bogle.tui.screens.modals import ConfirmModal

_COLUMNS = ("ID", "Data", "Tipo", "Ticker", "Qtd", "Preco", "Valor", "Fees", "IR")
_TRADES = (TransactionType.BUY, TransactionType.SELL)


class TransactionsScreen(Screen[None]):
    SUB_TITLE = "transacoes"
    # A tabela nasce com o foco: enquanto um Input tem foco, o textual desativa
    # os atalhos de uma letra (d/r/f) porque o campo consome as teclas.
    AUTO_FOCUS = "#ledger"
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "app.pop_screen", "Voltar"),
        Binding("r", "reload", "Atualizar"),
        Binding("d", "remove", "Remover"),
        Binding("f", "focus_filter", "Filtrar"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.transactions: list[Transaction] | None = None
        """Everything loaded, unfiltered; ``None`` before the first load and after
        a failure — an empty ledger and a ledger that could not be read are not
        the same thing, and the note has to keep saying which one it is."""
        self.shown: list[Transaction] = []
        """What the table currently lists, in display order."""
        self.note = ""
        """Plain text of the line under the table (read by the tests)."""

    @override
    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="transactions"):
            yield Input(placeholder="filtrar por ticker", id="filter")
            table = DataTable(id="ledger", cursor_type="row", zebra_stripes=True)
            table.add_columns(*_COLUMNS)
            yield table
            yield Static(id="ledger-note")
        yield Footer()

    def on_mount(self) -> None:
        self._load()

    # --- acoes ----------------------------------------------------------

    def render_amounts(self) -> None:
        """Redraw the rows after the privacy toggle."""
        self._refresh_rows(self.query_one("#filter", Input).value)

    def action_reload(self) -> None:
        self._load()

    def action_focus_filter(self) -> None:
        self.query_one("#filter", Input).focus()

    def action_remove(self) -> None:
        transaction = self.selected
        if transaction is None:
            self.notify("Nenhuma transacao selecionada.", severity="warning")
            return
        body = (
            f"{transaction.transaction_type} {transaction.ticker} em {transaction.date:%Y-%m-%d}, "
            f"valor {fmt.money(transaction.total_investment)}"
        )
        self.app.push_screen(
            ConfirmModal(f"Remover a transacao {transaction.id}?", body, confirm_label="Remover"),
            lambda confirmed: self._on_confirmed(transaction.id, confirmed),
        )

    @property
    def selected(self) -> Transaction | None:
        table = self.query_one(DataTable)
        if not self.shown or table.cursor_row < 0 or table.cursor_row >= len(self.shown):
            return None
        return self.shown[table.cursor_row]

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter":
            self._refresh_rows(event.value)

    # --- carga ----------------------------------------------------------

    def _load(self) -> None:
        self.query_one(DataTable).loading = True
        self._fetch()

    @work(thread=True, exclusive=True, group="ledger")
    def _fetch(self) -> None:
        worker = get_current_worker()
        try:
            transactions = services.load_transactions()
        except HANDLED as exc:
            if not worker.is_cancelled:
                self.app.call_from_thread(self._failed, message_for(exc))
            return
        if not worker.is_cancelled:
            self.app.call_from_thread(self._loaded, transactions)

    def _loaded(self, transactions: list[Transaction]) -> None:
        self.transactions = transactions
        self.query_one(DataTable).loading = False
        self._refresh_rows(self.query_one("#filter", Input).value)

    def _failed(self, message: str) -> None:
        table = self.query_one(DataTable)
        table.clear()
        table.loading = False
        # Tambem o ledger inteiro, e nao so o que estava filtrado: um redraw (o
        # toggle de privacidade, o filtro) reconstroi as linhas dele.
        self.transactions = None
        self.shown = []
        self._show_note(f"[red]{escape(message)}[/red]")
        self.notify(message, title="erro", severity="error", timeout=10, markup=False)

    def _refresh_rows(self, ticker_filter: str) -> None:
        if self.transactions is None:
            # Nada carregado (ou uma carga que falhou): redesenhar aqui apagaria a
            # mensagem que explica por que a tabela esta vazia.
            return
        needle = ticker_filter.strip().upper()
        self.shown = [t for t in self.transactions if needle in t.ticker.upper()]
        table = self.query_one(DataTable)
        table.clear()  # mantem as colunas
        for transaction in self.shown:
            is_trade = transaction.transaction_type in _TRADES
            table.add_row(
                cells.right(str(transaction.id)),
                cells.text(f"{transaction.date:%Y-%m-%d}"),
                cells.text(transaction.transaction_type),
                cells.ticker(transaction.ticker),
                cells.exact(transaction.shares) if is_trade else cells.right("-"),
                cells.exact(transaction.unit_price) if is_trade else cells.right("-"),
                cells.exact(transaction.total_investment),
                cells.exact(transaction.fees),
                cells.exact(transaction.tax_withheld),
                key=str(transaction.id),
            )
        self._show_note(self._summary(needle))

    def _summary(self, needle: str) -> str:
        if not self.transactions:
            return "[yellow]Nenhuma transacao registrada.[/yellow]"
        if not self.shown:
            return f"[yellow]Nenhuma transacao para '{escape(needle)}'.[/yellow]"
        if needle:
            return f"[dim]{len(self.shown)} de {len(self.transactions)} transacoes (filtro '{escape(needle)}')[/dim]"
        return f"[dim]{len(self.transactions)} transacoes[/dim]"

    def _show_note(self, markup: str) -> None:
        rendered = Text.from_markup(markup)
        self.note = rendered.plain
        self.query_one("#ledger-note", Static).update(rendered)

    # --- remocao --------------------------------------------------------

    def _on_confirmed(self, transaction_id: int, confirmed: bool | None) -> None:
        if confirmed:
            self._delete(transaction_id)

    @work(thread=True, exclusive=True, group="ledger-delete")
    def _delete(self, transaction_id: int) -> None:
        try:
            services.delete_transaction(transaction_id)
        except HANDLED as exc:
            self.app.call_from_thread(self._delete_failed, message_for(exc))
            return
        self.app.call_from_thread(self._deleted, transaction_id)

    def _delete_failed(self, message: str) -> None:
        # Diferente de uma falha de carga: as linhas continuam validas, entao a
        # tabela fica de pe. Recarrega para reconciliar (a linha pode ter sido
        # removida por outro processo) e explica no toast.
        self.notify(message, title="erro", severity="error", timeout=10, markup=False)
        self._load()

    def _deleted(self, transaction_id: int) -> None:
        self.notify(f"transacao {transaction_id} removida.", markup=False)
        self._load()
