"""Importar: the B3's Negociacao statement into the ledger (issue #34).

``bogle import`` on a form. Enter on a field (or "Pré-visualizar") reads the file
and plans it against the ledger in a worker — the command's ``--dry-run`` — and
draws what would happen per ticker, with the same report the command prints. Only
then does "Importar" write, after the confirmation every write screen asks for.

What is written is exactly the plan on the screen: a path or a fee changed after
the preview has to be previewed again before it can be imported, and the service
refuses a plan the ledger has moved away from since (see
:func:`~bogle.imports.plan.apply_import`).
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import ClassVar, override

from rich.markup import escape
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Checkbox, DataTable, Footer, Header, Input, Static
from textual.worker import get_current_worker

from bogle import format as fmt
from bogle.cli.parsing import parse_fee_rate
from bogle.error_messages import HANDLED, message_for
from bogle.imports.plan import ImportPlan, report_lines, summary, ticker_outcomes
from bogle.tui import cells, services
from bogle.tui.navigation import back_to_home
from bogle.tui.screens.modals import GO_HOME, NextStepModal
from bogle.tui.screens.write import Entry, WriteScreen
from bogle.tui.validators import DecimalField, TextField
from bogle.tui.widgets.form import ControlRow, Field

COLUMNS = ("Ticker", "Categoria", "Novas", "Duplicadas", "Detalhe")

REPLACE_HINT = "marque Substituir divergentes para trocar pelos lançamentos da B3"
"""The report's hint for a day whose value differs: the checkbox, not the flag."""

Typed = tuple[str, str, bool]
"""Path, fee and "Substituir divergentes" as they were when a preview was asked."""

_ESCAPED = re.compile(r"\\(.)")


def typed_path(text: str) -> Path:
    """The path as typed or dropped from the Finder: quotes and ``\\`` escapes off, ``~`` expanded.

    A file dragged into the terminal arrives as ``'/Users/x/My File.xlsx'`` or
    ``/Users/x/My\\ File.xlsx``, depending on the terminal; both are the same file.
    """
    text = text.strip()
    quoted = len(text) >= 2 and text[0] == text[-1] and text[0] in "'\""
    return Path(text[1:-1] if quoted else _ESCAPED.sub(r"\1", text)).expanduser()


class ImportScreen(WriteScreen[ImportPlan]):
    """Preview a statement, then write what the preview showed."""

    SUB_TITLE = "importar extrato da B3"
    AUTO_FOCUS = "#path Input"
    CONFIRM_TITLE = "Confirmar importação"
    CONFIRM_LABEL = "Importar"
    WRITING_MESSAGE = "importando; um instante."

    NOT_PREVIEWED: ClassVar[str] = "pré-visualize o extrato antes de importar."
    CHANGED: ClassVar[str] = "o arquivo, a taxa ou a substituição mudou desde a prévia: pré-visualize de novo."
    NOTHING_NEW: ClassVar[str] = "nada novo para importar."

    def __init__(self) -> None:
        super().__init__()
        self.plan: ImportPlan | None = None
        """The last preview; ``None`` until one succeeds (and after one fails)."""
        self.previewed: Typed | None = None
        """Path, fee and the checkbox as they were when :attr:`plan` was made."""
        self.source = ""
        """File name of the plan being written, for the dialogs."""
        self.note = ""
        """Plain text of the report under the table (read by the tests)."""

    @override
    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="import"):
            yield Field(
                "Arquivo",
                id="path",
                placeholder="~/Downloads/negociacao.xlsx",
                validators=[TextField("Arquivo")],
            )
            yield Field(
                "Taxa",
                id="fee",
                placeholder="opcional, fração por operação (ex: 0.00032)",
                validators=[DecimalField("Taxa", allow_blank=True, parse=parse_fee_rate, feminine=True)],
            )
            yield ControlRow("Substituir divergentes", Checkbox(id="replace", compact=True), id="replace-row")
            with Horizontal(id="form-buttons"):
                yield Button("Pré-visualizar", id="preview")
                yield Button("Importar", id="submit", variant="primary", disabled=True)
                yield Button("Voltar", id="back")
            table = DataTable(id="import-preview", cursor_type="row", zebra_stripes=True)
            table.add_columns(*COLUMNS)
            yield table
            yield Static(id="import-note")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#import").border_title = "Importar"

    # --- previa ---------------------------------------------------------

    def _typed(self) -> Typed:
        return self.field("path").value, self.field("fee").value, self.query_one("#replace", Checkbox).value

    # O textual chama o handler de cada classe do MRO, nao so o mais derivado:
    # sem `prevent_default`, o do WriteScreen roda depois deste e grava (ou abre
    # a confirmacao uma segunda vez).

    @override
    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Enter num campo pre-visualiza; gravar e o botao Importar (ou ctrl+s),
        # depois de ver o que vai entrar.
        event.stop()
        event.prevent_default()
        self.action_preview()

    @override
    def on_button_pressed(self, event: Button.Pressed) -> None:
        # Importar e Voltar ficam com o handler do WriteScreen.
        if event.button.id == "preview":
            event.prevent_default()
            self.action_preview()

    def on_input_changed(self, _event: Input.Changed) -> None:
        self._sync_submit()

    def on_checkbox_changed(self, _event: Checkbox.Changed) -> None:
        self._sync_submit()

    def action_preview(self) -> None:
        if self.writing:
            self.notify(self.WRITING_MESSAGE, severity="warning")
            return
        if not self.check_fields():
            return
        typed = self._typed()
        path, fee, replace = typed
        rate = parse_fee_rate(fee, "Taxa") if fee else None
        self.query_one("#import-preview").loading = True
        self._preview(typed, typed_path(path), rate, replace)

    @work(thread=True, exclusive=True, group="preview")
    def _preview(self, typed: Typed, path: Path, rate: Decimal | None, replace: bool) -> None:
        worker = get_current_worker()
        try:
            plan = services.preview_import(path, fee_rate=rate, replace=replace)
        except HANDLED as exc:
            if not worker.is_cancelled:
                self.app.call_from_thread(self._preview_failed, message_for(exc))
            return
        if not worker.is_cancelled:
            self.app.call_from_thread(self._previewed, typed, plan)

    def _previewed(self, typed: Typed, plan: ImportPlan) -> None:
        self.plan = plan
        self.previewed = typed
        self.query_one("#import-preview").loading = False
        self.render_amounts()
        self._sync_submit()
        submit = self.query_one("#submit", Button)
        if not submit.disabled:
            submit.focus()

    def _preview_failed(self, message: str) -> None:
        self.plan = None
        self.previewed = None
        self.query_one("#import-preview").loading = False
        self.query_one(DataTable).clear()
        self._show_note(f"[red]{escape(message)}[/red]")
        self._sync_submit()
        self.notify(message, title="erro", severity="error", timeout=10, markup=False)

    def _sync_submit(self) -> None:
        """Importar only while the preview on screen is current and adds something."""
        ready = self.plan is not None and bool(self.plan.inserts) and self.previewed == self._typed()
        self.query_one("#submit", Button).disabled = not ready

    # --- desenho --------------------------------------------------------

    def render_amounts(self) -> None:
        """Draw the plan; again after the privacy toggle (see ``BogleApp``)."""
        plan = self.plan
        if plan is None:
            return
        table = self.query_one(DataTable)
        table.clear()
        for row in ticker_outcomes(plan):
            table.add_row(
                cells.ticker(row.ticker),
                cells.text(row.outcome.value),
                cells.right(fmt.count(row.new)),
                cells.right(fmt.count(row.duplicated)),
                cells.text(row.detail),
                key=row.ticker,
            )
        self._show_note(fmt.attention(report_lines(plan, replace_hint=REPLACE_HINT)))

    def _show_note(self, markup: str) -> None:
        rendered = Text.from_markup(markup)
        self.note = rendered.plain
        self.query_one("#import-note", Static).update(rendered)

    # --- gravacao -------------------------------------------------------

    @override
    def collect(self) -> Entry | None:
        plan = self.plan
        if plan is None:
            self.notify(self.NOT_PREVIEWED, severity="warning")
            return None
        typed = self._typed()
        if self.previewed != typed:
            self.notify(self.CHANGED, severity="warning")
            return None
        if not plan.inserts:
            self.notify(self.NOTHING_NEW, severity="warning")
            return None
        self.source = typed_path(typed[0]).name
        return {"plan": plan}

    @override
    def describe(self, entry: Entry) -> str:
        return summary(entry["plan"], self.source)

    @override
    def write(self, entry: Entry) -> ImportPlan:
        return services.run_import(entry["plan"])

    @override
    def written(self, plan: ImportPlan, /) -> None:
        done = summary(plan, self.source)
        n = len(plan.inserts)
        self.notify(
            f"{fmt.count(n)} {'operação importada' if n == 1 else 'operações importadas'}.",
            title="pronto",
            markup=False,
        )
        # A previa ja foi gravada: importar de novo pede outra previa (que daria
        # tudo duplicado), entao o botao Importar se apaga ate la.
        self.previewed = None
        self._sync_submit()
        self.app.push_screen(
            NextStepModal(done, title="Importação concluída", again_label="Importar outro"), self._next_step
        )

    def _next_step(self, choice: str | None) -> None:
        if choice == GO_HOME or choice is None:
            back_to_home(self.app)
            return
        self.clear()

    def clear(self) -> None:
        """Empty the screen for another file."""
        self.plan = None
        self.previewed = None
        self.source = ""
        for field in self.query(Field):
            field.reset()
        self.query_one("#replace", Checkbox).value = False
        self.query_one(DataTable).clear()
        self._show_note("")
        self._sync_submit()
        self.field("path").input.focus()
