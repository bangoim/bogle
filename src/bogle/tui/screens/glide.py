"""Trajetoria screen: the targets by age (issue #79).

The same table as ``bogle glide show`` — one row per year, from the current one
to the last point, with the points marked next to the age — plus ``set``,
``remove`` and ``clear`` as a form and two confirmations. The rules, the writes
and the table all come from :mod:`bogle.glide_path`, which the command calls too.

Points behind the current age have no row (the table starts at this year), so
removing one that is not on screen asks for the age instead of a selection.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import ClassVar, override

from rich.markup import escape
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Footer, Header, Input, Static

from bogle import format as fmt
from bogle.cli.parsing import parse_weight
from bogle.domain.errors import ValidationError
from bogle.error_messages import HANDLED, message_for
from bogle.glide_path import (
    MAX_AGE,
    GlideEdit,
    GlideSchedule,
    application_notice,
    check_point,
    point_summary,
    step_notice,
)
from bogle.tui import services
from bogle.tui.screens.data import DataScreen
from bogle.tui.screens.modals import ConfirmModal, EditModal
from bogle.tui.screens.write import Entry, WriteScreen
from bogle.tui.validators import DecimalField, IntegerField
from bogle.tui.widgets.form import Field

ANCHOR = "•"
"""Marks the ages that are points, next to the age (as ``bogle glide show``)."""

NO_POINTS = "Trajetória sem pontos: 'a' cria o primeiro."
NO_BIRTH_YEAR = "Sem ano de nascimento: defina birth_year na Config."
INACTIVE = "Trajetória inativa até definir birth_year na Config."
EMPTIED = "Trajetória vazia: os alvos ficam como estão."


class GlidePathScreen(DataScreen[services.GlideState]):
    SUB_TITLE = "trajetória"
    AUTO_FOCUS = "#glide"
    LOADING = "#glide"
    NOTE = "#glide-note"
    BINDINGS: ClassVar[list[BindingType]] = [
        # Fora do rodape (mas na ajuda): a tela recarrega sozinha depois de cada
        # escrita e nao tem cotacao, e com ele as quatro acoes nao cabiam em 80.
        Binding("r", "reload", "Atualizar", show=False),
        Binding("a", "add", "Novo"),
        Binding("u", "update", "Alterar"),
        Binding("d", "remove", "Remover"),
        Binding("l", "clear", "Limpar"),
    ]

    @override
    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="glide-screen"):
            yield DataTable(id="glide", cursor_type="row", zebra_stripes=True)
            yield Static(id="glide-note")
        yield Footer()

    # --- selecao --------------------------------------------------------

    @property
    def points(self) -> dict[int, dict[str, Decimal]]:
        return self.report.view.points if self.report is not None else {}

    @property
    def schedule(self) -> GlideSchedule | None:
        return self.report.view.schedule if self.report is not None else None

    @property
    def selected_age(self) -> int | None:
        """The age of the row under the cursor; ``None`` without a table."""
        table = self.query_one(DataTable)
        if self.schedule is None or not table.is_valid_coordinate(table.cursor_coordinate):
            return None
        key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        return int(key) if key is not None else None

    def _selected_targets(self) -> tuple[int | None, dict[str, Decimal]]:
        age = self.selected_age
        schedule = self.schedule
        if age is None or schedule is None:
            return None, {}
        row = next(row for row in schedule.years if row.age == age)
        # O ponto como foi gravado, quando a idade e um; senao, a linha da tabela.
        return age, dict(self.points.get(age, row.targets))

    # --- acoes ----------------------------------------------------------

    def action_add(self) -> None:
        if self.report is None:
            return
        self.app.push_screen(GlidePointScreen(self.report.tickers, points=self.points), lambda _: self.fetch())

    def action_update(self) -> None:
        if self.report is None:
            return
        age, weights = self._selected_targets()
        if age is None:
            self.notify("Nenhuma linha selecionada.", severity="warning")
            return
        self.app.push_screen(
            GlidePointScreen(self.report.tickers, points=self.points, age=age, weights=weights),
            lambda _: self.fetch(),
        )

    def action_remove(self) -> None:
        points = self.points
        if not points:
            self.notify("Trajetória sem pontos.", severity="warning")
            return
        age = self.selected_age
        if age is not None and age in points:
            self._confirm_removal(age)
            return
        # Linha que nao e ponto, ou ponto antes da idade atual (sem linha): pede a idade.
        ages = ", ".join(str(each) for each in sorted(points))
        self.app.push_screen(
            EditModal("Remover ponto", f"Pontos: {ages}.", placeholder="idade"),
            self._on_age_typed,
        )

    def _on_age_typed(self, typed: str | None) -> None:
        if typed is None or not typed.strip():
            return
        try:
            age = int(typed.strip())
        except ValueError:
            self.notify(f"Idade deve ser um número inteiro, recebido {typed.strip()!r}.", severity="error")
            return
        if age not in self.points:
            self.notify(f"A trajetória não tem ponto aos {age} anos.", severity="error")
            return
        self._confirm_removal(age)

    def _confirm_removal(self, age: int) -> None:
        self.app.push_screen(
            ConfirmModal(
                f"Remover o ponto dos {age} anos?", point_summary(age, self.points[age]), confirm_label="Remover"
            ),
            lambda confirmed: self._on_removal_confirmed(age, confirmed),
        )

    def _on_removal_confirmed(self, age: int, confirmed: bool | None) -> None:
        if confirmed:
            self._remove(age)

    def action_clear(self) -> None:
        count = len(self.points)
        if count == 0:
            self.notify("Trajetória sem pontos.", severity="warning")
            return
        noun = "ponto" if count == 1 else "pontos"
        self.app.push_screen(
            ConfirmModal(
                f"Apagar {count} {noun} da trajetória?", "Os alvos atuais ficam como estão.", confirm_label="Apagar"
            ),
            self._on_clear_confirmed,
        )

    def _on_clear_confirmed(self, confirmed: bool | None) -> None:
        if confirmed:
            self._clear()

    # --- carga ----------------------------------------------------------

    @override
    def load(self) -> services.GlideState:
        return services.load_glide()

    @override
    def clear_content(self) -> None:
        self.query_one(DataTable).clear(columns=True)

    @override
    def render_report(self, report: services.GlideState) -> None:
        table = self.query_one(DataTable)
        table.clear(columns=True)
        view = report.view
        schedule = view.schedule
        lines = []
        if not view.points:
            lines.append(f"[yellow]{NO_POINTS}[/yellow]")
        if view.birth_year is None:
            lines.append(f"[yellow]{NO_BIRTH_YEAR}[/yellow]")
        if schedule is not None:
            table.add_columns("Ano", "Idade", *schedule.tickers)
            for index, row in enumerate(schedule.years):
                # A primeira linha e sempre o ano corrente.
                style = "bold" if index == 0 else ""
                age = f"{row.age} {ANCHOR}" if row.anchor else str(row.age)
                table.add_row(
                    Text(str(row.year), style=style),
                    Text(age, style=style),
                    *(Text(fmt.pct(row.targets[ticker]), style=style, justify="right") for ticker in schedule.tickers),
                    key=str(row.age),
                )
            if schedule.next_step is not None:
                lines.append(escape(step_notice(schedule.next_step)))
        self.show_note("\n".join(lines))

    # --- escrita --------------------------------------------------------

    @work(thread=True, exclusive=True, group="glide-write")
    def _remove(self, age: int) -> None:
        try:
            edit = services.remove_glide_point(age)
        except HANDLED as exc:
            self.app.call_from_thread(self._write_failed, message_for(exc))
            return
        self.app.call_from_thread(self._removed, age, edit)

    @work(thread=True, exclusive=True, group="glide-write")
    def _clear(self) -> None:
        try:
            count = services.clear_glide_path()
        except HANDLED as exc:
            self.app.call_from_thread(self._write_failed, message_for(exc))
            return
        self.app.call_from_thread(self._cleared, count)

    def _write_failed(self, message: str) -> None:
        self.notify(message, title="erro", severity="error", timeout=10, markup=False)
        self.fetch()

    def _removed(self, age: int, edit: GlideEdit) -> None:
        self.notify(f"ponto dos {age} anos removido.", markup=False)
        announce(self, edit)
        if edit.points == 0:
            self.notify(EMPTIED, markup=False)
        self.fetch()

    def _cleared(self, count: int) -> None:
        noun = "ponto" if count == 1 else "pontos"
        self.notify(f"trajetória apagada ({count} {noun}); os alvos ficam como estão.", markup=False)
        self.fetch()


def announce(screen: DataScreen[services.GlideState] | WriteScreen[GlideEdit], edit: GlideEdit) -> None:
    """The notice of the targets written again, the same as on 01/01."""
    notice = application_notice(edit.application)
    if notice is not None:
        screen.notify(notice, title="trajetória", timeout=10, markup=False)


class GlidePointScreen(WriteScreen[GlideEdit]):
    """Create or replace the point of one age: ``bogle glide set``."""

    SUB_TITLE = "ponto da trajetória"
    AUTO_FOCUS = "#age Input"
    CONFIRM_TITLE = "Confirmar ponto"
    CONFIRM_LABEL = "Gravar"
    WRITING_MESSAGE = "gravando o ponto; um instante."

    def __init__(
        self,
        tickers: tuple[str, ...],
        *,
        points: Mapping[int, Mapping[str, Decimal]],
        age: int | None = None,
        weights: Mapping[str, Decimal] | None = None,
    ) -> None:
        super().__init__()
        self.tickers = tickers
        self.points = points
        self.age = age
        self.weights = dict(weights or {})
        self._written: Entry = {}
        """What the last write sent, for the toast after it."""

    @override
    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="glide-form"):
            yield Field(
                "Idade",
                id="age",
                value="" if self.age is None else str(self.age),
                placeholder=f"0 a {MAX_AGE}",
                validators=[IntegerField("Idade", minimum=0, maximum=MAX_AGE, feminine=True)],
            )
            for index, ticker in enumerate(self.tickers):
                weight = self.weights.get(ticker)
                yield Field(
                    ticker,
                    id=f"w{index}",
                    value=_weight_text(weight) if weight else "",
                    placeholder=f"0{fmt.separators().decimal}00",
                    validators=[DecimalField(ticker, allow_blank=True, parse=_parse_point_weight)],
                )
            yield Static(id="glide-sum")
            with Horizontal(id="form-buttons"):
                yield Button("Gravar", id="submit", variant="primary")
                yield Button("Voltar", id="back")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#glide-form").border_title = "Ponto"
        self._show_sum()
        if not self.tickers:
            self.notify("Nenhum ativo cadastrado.", severity="warning")
        elif self.age is not None:
            # Editando uma linha a idade ja veio dela: o que se muda sao os pesos.
            self.field("w0").input.focus()

    def on_input_changed(self, _event: Input.Changed) -> None:
        self._show_sum()

    def _show_sum(self) -> None:
        """The sum of the weights typed so far, green once it is exactly 1."""
        total = sum(
            (_typed_weight(self.field(f"w{index}").value) for index in range(len(self.tickers))),
            start=Decimal("0"),
        )
        shown = self.query_one("#glide-sum", Static)
        shown.update(f"Soma {_weight_display(total)}")
        shown.set_class(total == 1, "complete")

    @override
    def collect(self) -> Entry | None:
        if not self.check_fields():
            return None
        age = int(self.field("age").value)
        weights: dict[str, Decimal] = {}
        for index, ticker in enumerate(self.tickers):
            text = self.field(f"w{index}").value
            if text:
                weights[ticker] = _parse_point_weight(text, ticker)
        try:
            anchor = check_point(age, weights)
        except ValidationError as exc:
            self.notify(str(exc), title="erro", severity="error", timeout=10, markup=False)
            return None
        return {"age": age, "weights": anchor}

    @override
    def describe(self, entry: Entry) -> str:
        summary = point_summary(entry["age"], entry["weights"])
        if entry["age"] in self.points:
            return f"{summary}\nSubstitui o ponto atual."
        return summary

    @override
    def write(self, entry: Entry) -> GlideEdit:
        self._written = entry
        return services.save_glide_point(entry["age"], entry["weights"])

    @override
    def written(self, edit: GlideEdit) -> None:
        entry = self._written
        self.notify(f"ponto gravado: {point_summary(entry['age'], entry['weights'])}.", title="pronto", markup=False)
        announce(self, edit)
        if not edit.active:
            self.notify(INACTIVE, severity="warning", timeout=10, markup=False)
        self.dismiss()


def _parse_point_weight(value: str, option: str) -> Decimal:
    """A weight of a point: a fraction in ``[0, 1]``, as ``bogle update`` reads it."""
    return parse_weight(value, option, allow_zero=True)


def _typed_weight(text: str) -> Decimal:
    """What a weight field adds to the sum: 0 while blank or not yet a weight."""
    if not text:
        return Decimal("0")
    try:
        return _parse_point_weight(text, "peso")
    except ValidationError:
        return Decimal("0")


def _weight_display(weight: Decimal) -> str:
    """A sum of weights with two places at least, in the user's separator."""
    whole, _, places = f"{weight:.4f}".partition(".")
    return f"{whole}{fmt.separators().decimal}{places.rstrip('0').ljust(2, '0')}"


def _weight_text(weight: Decimal) -> str:
    """The weight as the field takes it back: a plain fraction, no percent sign."""
    return format(weight.normalize(), "f")
