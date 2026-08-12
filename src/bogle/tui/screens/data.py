"""Shared shape of a screen backed by one blocking load (issue #75).

Every report screen does the same four things: open showing a loading state,
fetch in a worker thread, draw what came back, and turn an expected failure into
a message beside the content plus a toast. That flow lives here once — including
the cancellation guard that keeps a slow load, already replaced by a newer one,
from overwriting the fresher view.

Subclasses provide the two halves that differ: :meth:`DataScreen.load` (in the
worker) and :meth:`DataScreen.render_report` (on the main thread). Because
``render_report`` draws from stored data, it doubles as the redraw the privacy
toggle needs — no refetch to hide a number.
"""

from __future__ import annotations

from typing import ClassVar, override

from rich.markup import escape
from rich.text import Text
from textual import work
from textual.binding import Binding, BindingType
from textual.screen import Screen
from textual.widgets import Static
from textual.worker import get_current_worker

from bogle.tui.errors import HANDLED, message_for


class DataScreen[R](Screen[None]):
    """A screen whose content is one report of type ``R``."""

    LOADING: ClassVar[str] = "#content"
    """Selector of the widget that shows the loading state while fetching."""

    NOTE: ClassVar[str] = "#note"
    """Selector of the ``Static`` carrying the message under the content."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "app.pop_screen", "Voltar"),
        Binding("r", "reload", "Atualizar"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.report: R | None = None
        """Last loaded report; ``None`` until the worker finishes (or after a failure)."""
        self.note = ""
        """Plain text of the message under the content (read by the tests)."""

    # --- a implementar por cada tela --------------------------------------

    def load(self) -> R:
        """Fetch the report. Runs in a worker thread."""
        raise NotImplementedError

    def render_report(self, report: R) -> None:
        """Draw ``report``. Runs on the main thread, and again on every redraw."""
        raise NotImplementedError

    def clear_content(self) -> None:
        """Drop what is on screen after a failure.

        Default is to keep it: a screen whose content is rebuilt from scratch on
        every render has nothing stale to clear. Tables override this — their
        rows would otherwise outlive the data they came from.
        """

    # --- fluxo -----------------------------------------------------------

    def on_mount(self) -> None:
        self.fetch()

    def action_reload(self) -> None:
        self.fetch()

    def fetch(self) -> None:
        """Start (or restart) the load."""
        self.show_loading(True)
        self._fetch()

    def render_amounts(self) -> None:
        """Redraw from what is loaded, after the privacy toggle (see ``BogleApp``)."""
        if self.report is not None:
            self.render_report(self.report)

    def show_loading(self, loading: bool) -> None:
        self.query_one(self.LOADING).loading = loading

    def show_note(self, markup: str) -> None:
        rendered = Text.from_markup(markup)
        self.note = rendered.plain
        self.query_one(self.NOTE, Static).update(rendered)

    @work(thread=True, exclusive=True, group="report")
    def _fetch(self) -> None:
        worker = get_current_worker()
        try:
            report = self.load()
        except HANDLED as exc:
            if not worker.is_cancelled:
                self.app.call_from_thread(self._failed, message_for(exc))
            return
        if not worker.is_cancelled:
            self.app.call_from_thread(self._loaded, report)

    def _loaded(self, report: R) -> None:
        self.report = report
        self.show_loading(False)
        self.render_report(report)

    def _failed(self, message: str) -> None:
        # Sem relatorio: um redraw (privacidade) nao deve ressuscitar o anterior,
        # que ja nao esta na tela.
        self.report = None
        self.show_loading(False)
        self.clear_content()
        self.show_note(f"[red]{escape(message)}[/red]")
        self.notify(message, title="erro", severity="error", timeout=10, markup=False)


class PeriodScreen[R](DataScreen[R]):
    """A :class:`DataScreen` over a window the user cycles with ``t``.

    Cycling instead of a dropdown for two reasons: it costs no screen width (the
    tables already need all of it at 80 columns) and it reads in the subtitle,
    where the Position screen already puts its mode.
    """

    PERIODS: ClassVar[tuple[str, ...]] = ()
    """The windows to cycle through; the first one is where the screen opens."""

    SUBJECT: ClassVar[str] = ""
    """Name of the report in the subtitle, before the window."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("t", "cycle_period", "Periodo")]

    def __init__(self) -> None:
        super().__init__()
        self.period = self.PERIODS[0]

    def subtitle(self) -> str:
        """What the header shows; overridden when the window needs qualifying."""
        return f"{self.SUBJECT} - {self.period}"

    @override
    def on_mount(self) -> None:
        # Aqui, e nao no __init__: o subtitulo pode depender de estado que a
        # subclasse ainda estava montando quando esta base rodou.
        self.sub_title = self.subtitle()
        super().on_mount()

    def action_cycle_period(self) -> None:
        self.period = self.PERIODS[(self.PERIODS.index(self.period) + 1) % len(self.PERIODS)]
        self.sub_title = self.subtitle()
        self.fetch()
