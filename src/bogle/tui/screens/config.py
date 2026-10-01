"""Settings screen: read and change the user settings (issue #76).

The rows of ``bogle config list``, with ``e`` (or Enter) editing a value in place
and ``d`` reverting it to the default. Values are parsed by the same
``settings.set_setting`` the command uses, so an invalid one is refused with the
same message — here as a toast, with the table left alone.

Three of the keys are read once, at startup: theme, decimal separator and privacy
mode. Editing one of those posts :class:`ConfigScreen.PreferenceChanged`, which
the App applies immediately — a change that only showed up in the next session
would read as not having worked. It travels as a message instead of a call so the
screen does not have to import the App that mounts it.
"""

from __future__ import annotations

from typing import Any, ClassVar, override

from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import DataTable, Footer, Header, Static

from bogle.settings import SettingEntry, format_value
from bogle.tui import cells, services
from bogle.tui.errors import HANDLED, message_for
from bogle.tui.screens.data import DataScreen
from bogle.tui.screens.modals import EditModal

_COLUMNS = ("Chave", "Valor", "Tipo", "Atualizado em", "Descricao")

_LEGEND = "e edita, d volta ao default. Tema, separador decimal e privacidade valem na hora."


class ConfigScreen(DataScreen[list[SettingEntry]]):
    SUB_TITLE = "config"
    AUTO_FOCUS = "#settings"
    LOADING = "#settings"
    NOTE = "#config-note"
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("e", "edit", "Editar"),
        Binding("d", "reset", "Reverter"),
    ]

    class PreferenceChanged(Message):
        """A setting the interface reads at startup changed; apply it now."""

        def __init__(self, key: str, value: Any) -> None:
            super().__init__()
            self.key = key
            self.value = value

    @override
    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="config"):
            table = DataTable(id="settings", cursor_type="row", zebra_stripes=True)
            table.add_columns(*_COLUMNS)
            yield table
            yield Static(id="config-note")
        yield Footer()

    # --- selecao --------------------------------------------------------

    @property
    def selected(self) -> SettingEntry | None:
        entries = self.report
        table = self.query_one(DataTable)
        if not entries or table.cursor_row < 0 or table.cursor_row >= len(entries):
            return None
        return entries[table.cursor_row]

    # --- acoes ----------------------------------------------------------

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        self.action_edit()

    def action_edit(self) -> None:
        entry = self.selected
        if entry is None:
            return
        self.app.push_screen(
            EditModal(
                f"Editar {entry.key}",
                f"{entry.description}\nTipo: {entry.type_name}. Atual: {format_value(entry.value)}",
                # Chave nunca definida abre em branco: "(nao definido)" e como a
                # ausencia e mostrada, nao um valor que o parser aceite de volta.
                value="" if entry.value is None else format_value(entry.value),
                placeholder=_placeholder(entry),
            ),
            lambda raw: self._on_edited(entry.key, raw),
        )

    def action_reset(self) -> None:
        entry = self.selected
        if entry is None:
            return
        if entry.is_default:
            self.notify(f"{entry.key} ja esta no default.", markup=False)
            return
        self._reset(entry.key)

    # --- carga ----------------------------------------------------------

    @override
    def load(self) -> list[SettingEntry]:
        return services.load_settings()

    @override
    def clear_content(self) -> None:
        self.query_one(DataTable).clear()

    @override
    def render_report(self, report: list[SettingEntry]) -> None:
        table = self.query_one(DataTable)
        table.clear()  # mantem as colunas
        for entry in report:
            table.add_row(
                cells.ticker(entry.key),
                cells.right(format_value(entry.value)),
                cells.text(entry.type_name),
                cells.text("(default)" if entry.is_default else f"{entry.updated_at:%Y-%m-%d %H:%M}"),
                cells.text(entry.description),
                key=entry.key,
            )
        self.show_note(f"[dim]{_LEGEND}[/dim]")

    # --- escrita --------------------------------------------------------

    def _on_edited(self, key: str, raw: str | None) -> None:
        if raw is not None:
            self._save(key, raw)

    @work(thread=True, exclusive=True, group="settings")
    def _save(self, key: str, raw: str) -> None:
        try:
            value = services.save_setting(key, raw)
        except HANDLED as exc:
            self.app.call_from_thread(self._write_failed, message_for(exc))
            return
        self.app.call_from_thread(self._written, f"{key} = {format_value(value)}", key, value)

    @work(thread=True, exclusive=True, group="settings")
    def _reset(self, key: str) -> None:
        try:
            value = services.reset_setting(key)
        except HANDLED as exc:
            self.app.call_from_thread(self._write_failed, message_for(exc))
            return
        self.app.call_from_thread(self._written, f"{key} voltou ao default ({format_value(value)}).", key, value)

    def _written(self, summary: str, key: str, value: Any) -> None:
        self.notify(summary, markup=False)
        self.post_message(self.PreferenceChanged(key, value))
        self.fetch()

    def _write_failed(self, message: str) -> None:
        # A tabela continua valida: so a escrita falhou (valor invalido, banco
        # fora). As linhas ficam, e o toast diz o que houve.
        self.notify(message, title="erro", severity="error", timeout=10, markup=False)


def _placeholder(entry: SettingEntry) -> str:
    if entry.type_name == "list[str]":
        return "separados por virgula (ex: CDI,IBOV)"
    if entry.type_name == "date":
        return "YYYY-MM-DD"
    if entry.type_name == "bool":
        return "true ou false"
    return escape(entry.description)
