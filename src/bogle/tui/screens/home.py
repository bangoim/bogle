"""Home screen: logo, headline summary and the menu (issue #73).

The summary is deliberately minimal — four numbers, all measured at the previous
close (D-1), so opening ``bogle`` never waits on an intraday quote. Live prices
belong to the Position screen. It loads in a worker thread with a placeholder in
place while it computes, and an expected failure (database down, provider
unreachable) becomes an inline message plus a toast instead of a crash.
"""

from __future__ import annotations

from typing import ClassVar, override

from rich.markup import escape
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Grid, Vertical, VerticalScroll
from textual.widgets import Footer, Header, Static
from textual.worker import get_current_worker

from bogle import format as fmt
from bogle.reports.overview import PortfolioOverview
from bogle.tui import services
from bogle.tui.errors import HANDLED, message_for
from bogle.tui.screens.menu import Entries, MenuScreen, items_of
from bogle.tui.screens.position import PositionScreen
from bogle.tui.screens.register import RegisterScreen
from bogle.tui.screens.reports import ReportsScreen
from bogle.tui.screens.transactions import TransactionsScreen
from bogle.tui.widgets.menu import Menu, MenuItem, menu_bindings
from bogle.tui.widgets.metric import Metric

# Letras de 4x6 pixels desenhadas com meio-bloco, duas linhas de pixels por
# linha de texto. Em duas linhas de texto — 4 pixels de altura — nao cabem a
# barra do meio nem uma cauda, e o logo saia lido como "bodlc".
LOGO = r"""
█▀▀▄ ▄▀▀▄ ▄▀▀▀ █    █▀▀▀
█▀▀▄ █  █ █ ▀█ █    █▀▀
█▄▄▀ ▀▄▄▀ ▀▄▄▀ █▄▄▄ █▄▄▄
""".strip("\n")

_ENTRIES: Entries = (
    (MenuItem("1", "position", "Posicao", "precos ao vivo, pesos e drift"), PositionScreen),
    (MenuItem("2", "register", "Registrar", "compra, venda ou provento"), RegisterScreen),
    (MenuItem("3", "transactions", "Transacoes", "listar e remover lancamentos"), TransactionsScreen),
    (MenuItem("4", "reports", "Relatorios", "rentabilidade, historico, proventos"), ReportsScreen),
)

MENU_ITEMS = items_of(_ENTRIES)

_TWR_LEGEND = "Rentabilidade em TWR: exclui o efeito de aportes e retiradas e considera proventos."

_PATRIMONY = "Patrimonio total"
_PATRIMONY_PARTIAL = "Patrimonio parcial"
_VARIATION = "Variacao"
_VARIATION_PARTIAL = "Variacao parcial"


class HomeScreen(MenuScreen):
    AUTO_FOCUS = "#menu"
    ENTRIES = _ENTRIES
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "app.quit", "Sair"),
        Binding("r", "reload", "Atualizar"),
        *menu_bindings(MENU_ITEMS),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.overview: PortfolioOverview | None = None
        """Last loaded summary; ``None`` until the worker finishes."""
        self.note = ""
        """Plain text of the note under the metrics (read by the tests)."""
        self._loaded = False

    @override
    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="home"):
            yield Static(LOGO, id="logo")
            with Vertical(id="summary"):
                with Grid(id="metrics"):
                    yield Metric(_PATRIMONY, id="patrimony")
                    yield Metric(_VARIATION, id="variation")
                    yield Metric("Rentabilidade 12m (TWR)", id="twr-12m")
                    yield Metric("Rentabilidade total (TWR)", id="twr-total")
                yield Static(id="summary-note")
            yield Menu(MENU_ITEMS, id="menu")
        yield Footer()

    @override
    def on_mount(self) -> None:
        super().on_mount()
        self._load_overview()
        self._check_rebalance()

    def render_amounts(self) -> None:
        """Redraw the summary after the privacy toggle (see ``BogleApp``)."""
        if self.overview is not None:
            self._show_overview(self.overview)

    def on_screen_resume(self) -> None:
        # Voltando de outra tela (um lancamento novo, por exemplo) o resumo pode
        # estar velho. Enquanto a primeira carga nao terminou, ela ja cobre isso.
        if self._loaded:
            self.action_reload()

    # --- navegacao ------------------------------------------------------

    def action_reload(self) -> None:
        for metric in self.query(Metric):
            metric.reset()
        self._load_overview()

    # --- carga ----------------------------------------------------------

    @work(thread=True, exclusive=True, group="overview")
    def _load_overview(self) -> None:
        worker = get_current_worker()
        try:
            overview = services.load_overview()
        except HANDLED as exc:
            if not worker.is_cancelled:
                self.app.call_from_thread(self._show_failure, message_for(exc))
            return
        # Uma carga cancelada (`r` durante outra) nao pode sobrescrever a nova.
        if not worker.is_cancelled:
            self.app.call_from_thread(self._show_overview, overview)

    @work(thread=True, group="rebalance")
    def _check_rebalance(self) -> None:
        # O aviso de ciclo vencido virou toast (na CLI e uma linha em stderr).
        notice = services.rebalance_notice()
        if notice is not None:
            self.app.call_from_thread(
                self.notify, notice, title="rebalanceamento", severity="warning", timeout=12, markup=False
            )

    def _show_overview(self, overview: PortfolioOverview) -> None:
        self.overview = overview
        self._loaded = True
        self.query_one("#summary").border_title = f"Carteira - fechamento de {overview.as_of.isoformat()}"
        # Com ticker excluido o numero e um subconjunto da carteira: o rotulo diz
        # isso, em vez de deixar so a nota explicando um "total" que nao e total.
        patrimony = self.query_one("#patrimony", Metric)
        variation = self.query_one("#variation", Metric)
        patrimony.set_caption(_PATRIMONY_PARTIAL if overview.is_partial else _PATRIMONY)
        variation.set_caption(_VARIATION_PARTIAL if overview.is_partial else _VARIATION)
        patrimony.show(fmt.money(overview.patrimony))
        variation.show(_variation(overview))
        self.query_one("#twr-12m", Metric).show(fmt.signed(overview.twr_12m, percent=True))
        self.query_one("#twr-total", Metric).show(fmt.signed(overview.twr_total, percent=True))
        self._show_note(_note_for(overview))

    def _show_failure(self, message: str) -> None:
        self._loaded = True
        for metric in self.query(Metric):
            metric.show(fmt.DASH)
        self._show_note(f"[red]{escape(message)}[/red]")
        self.notify(message, title="erro", severity="error", timeout=10, markup=False)

    def _show_note(self, markup: str) -> None:
        rendered = Text.from_markup(markup)
        self.note = rendered.plain
        self.query_one("#summary-note", Static).update(rendered)


def _variation(overview: PortfolioOverview) -> str:
    """``+516.20  (+7.02%)`` — the percentage is dropped when there is no base."""
    absolute = fmt.signed(overview.variation, percent=False)
    percent = overview.variation_percent
    return absolute if percent is None else f"{absolute}  ({fmt.signed(percent, percent=True)})"


def _note_for(overview: PortfolioOverview) -> str:
    if overview.is_empty:
        return "[yellow]Nenhuma transacao registrada ainda.[/yellow]"
    if overview.excluded:
        excluded = escape(", ".join(overview.excluded))
        return (
            f"[yellow]Nota:[/yellow] sem historico de precos para {excluded} — "
            "fora do patrimonio, da variacao e das rentabilidades."
        )
    if overview.patrimony is None:
        return f"[yellow]Nota:[/yellow] nenhuma posicao avaliavel no fechamento de {overview.as_of.isoformat()}."
    if overview.twr_12m_is_shorter and overview.twr_12m_start is not None:
        # Carteira com menos de 12 meses: a janela ancora na primeira transacao,
        # entao a rentabilidade "12m" cobre menos que isso. A CLI diz o mesmo
        # imprimindo a janela ao lado de cada periodo.
        return (
            f"[dim]{_TWR_LEGEND} A janela de 12m ancora na primeira transacao "
            f"({overview.twr_12m_start.isoformat()}).[/dim]"
        )
    return f"[dim]{_TWR_LEGEND}[/dim]"
