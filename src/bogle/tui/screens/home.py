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
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Header, Static
from textual.worker import get_current_worker

from bogle import format as fmt
from bogle.reports.overview import PortfolioOverview
from bogle.reports.valuation import RETRIABLE
from bogle.tui import services
from bogle.tui.errors import HANDLED, message_for
from bogle.tui.screens.assets import AssetsScreen
from bogle.tui.screens.config import ConfigScreen
from bogle.tui.screens.menu import Entries, MenuScreen, items_of
from bogle.tui.screens.position import PositionScreen
from bogle.tui.screens.register import RegisterScreen
from bogle.tui.screens.reports import ReportsScreen
from bogle.tui.screens.status import StatusScreen
from bogle.tui.screens.suggest import SuggestScreen
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

# Seis itens, e nao oito: Status e Config viraram atalhos do rodape (`s` e `c`).
# Sao as duas telas que se consulta de vez em quando, e o menu e o que decide a
# altura da Home — com elas dentro, o resumo empurrava o logo para fora do scroll
# em terminais de altura normal.
_ENTRIES: Entries = (
    (MenuItem("1", "position", "Posicao", "precos ao vivo, pesos e drift"), PositionScreen),
    (MenuItem("2", "register", "Registrar", "compra, venda ou provento"), RegisterScreen),
    (MenuItem("3", "transactions", "Transacoes", "listar e remover lancamentos"), TransactionsScreen),
    (MenuItem("4", "suggest", "Aporte", "como dividir para reduzir o drift"), SuggestScreen),
    (MenuItem("5", "reports", "Relatorios", "rentabilidade, historico, proventos"), ReportsScreen),
    (MenuItem("6", "assets", "Ativos", "cadastrar, atualizar e remover"), AssetsScreen),
)

MENU_ITEMS = items_of(_ENTRIES)

_COLUMNS = 2
"""O menu e servido em duas colunas: cada item ocupa meia largura e o bloco
inteiro cabe em tres linhas. Num terminal estreito as duas empilham (app.tcss),
o que devolve a lista de uma coluna sem mudar nada aqui."""

_LEFT = MENU_ITEMS[: (len(MENU_ITEMS) + 1) // _COLUMNS]
_RIGHT = MENU_ITEMS[(len(MENU_ITEMS) + 1) // _COLUMNS :]

_TWR_LEGEND = "Rentabilidade em TWR: exclui o efeito de aportes e retiradas e considera proventos."

_PATRIMONY = "Patrimonio total"
_PATRIMONY_PARTIAL = "Patrimonio parcial"
_VARIATION = "Variacao"
_VARIATION_PARTIAL = "Variacao parcial"


class HomeScreen(MenuScreen):
    AUTO_FOCUS = "#menu-left"
    ENTRIES = _ENTRIES
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "app.quit", "Sair"),
        Binding("r", "reload", "Atualizar"),
        Binding("s", "status", "Status"),
        Binding("c", "config", "Config"),
        # Setas entre as colunas: a lista consome cima/baixo, esquerda/direita nao.
        Binding("left", "focus_column(-1)", "Coluna", show=False),
        Binding("right", "focus_column(1)", "Coluna", show=False),
        *menu_bindings(MENU_ITEMS),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.overview: PortfolioOverview | None = None
        """Last loaded summary; ``None`` until the worker finishes (or after a failure)."""
        self.note = ""
        """Plain text of the note under the metrics (read by the tests)."""
        self._may_be_stale = False
        """True once a screen that can write has been opened from the menu."""

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
            with Horizontal(id="menu"):
                yield Menu(_LEFT, id="menu-left")
                yield Menu(_RIGHT, id="menu-right")
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
        # Voltando de uma tela do menu (um lancamento novo, por exemplo) o resumo
        # pode estar velho. A ajuda tambem suspende a Home, mas nao escreve nada:
        # recarregar por causa dela custaria um recalculo D-1 inteiro por consulta.
        if self._may_be_stale:
            self._may_be_stale = False
            self.action_reload()

    # --- navegacao ------------------------------------------------------

    @override
    def action_open(self, item_id: str) -> None:
        self._may_be_stale = True
        super().action_open(item_id)

    def action_status(self) -> None:
        self._open(StatusScreen())

    def action_config(self) -> None:
        self._open(ConfigScreen())

    def _open(self, screen: StatusScreen | ConfigScreen) -> None:
        # Pelo mesmo caminho do menu: a Config grava, entao o resumo pode mudar.
        self._may_be_stale = True
        self.app.push_screen(screen)

    def action_focus_column(self, direction: int) -> None:
        """Move between the two columns of the menu, keeping the row in mind."""
        columns = list(self.query(Menu))
        current = next((index for index, menu in enumerate(columns) if menu.has_focus), 0)
        target = columns[max(0, min(len(columns) - 1, current + direction))]
        # A linha acompanha: sair da terceira da esquerda e cair na primeira da
        # direita seria uma volta ao topo sem motivo.
        row = columns[current].highlighted
        target.focus()
        if row is not None and target.option_count:
            target.highlighted = min(row, target.option_count - 1)

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
        self.query_one("#summary").border_title = f"Carteira - fechamento de {overview.as_of.isoformat()}"
        # Com ticker excluido o numero e um subconjunto da carteira: o rotulo diz
        # isso, em vez de deixar so a nota explicando um "total" que nao e total.
        # So estes dois ganham "parcial": em "Rentabilidade total" o total e o
        # periodo (desde a primeira transacao), e "total parcial" leria como se
        # fossem a mesma coisa. Quem esta fora das rentabilidades a nota nomeia.
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
        # Sem resumo guardado: um redraw (o toggle de privacidade) nao pode
        # ressuscitar numeros que a tela acabou de dizer que nao tem.
        self.overview = None
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


def _listed(tickers: list[str], reasons: dict[str, str]) -> str:
    return ", ".join(f"{escape(ticker)} ({escape(reasons.get(ticker, ''))})".replace(" ()", "") for ticker in tickers)


def _excluded_note(overview: PortfolioOverview) -> str:
    """Which tickers are out of which numbers, and why each one is.

    Two lists, because they are two different situations: a ticker nothing can
    price is out of all four numbers, while one whose series merely starts after
    the position still counts in the patrimony (its close at D-1 exists) and only
    misses the returns. Saying "out of everything" for the second would hide real
    money and make the Home disagree with the Position screen.

    The reason matters too: "no price history" reads like a fact about the asset,
    but two of the four causes are the provider having a bad minute — and those
    the user can do something about, which is why the retry line only shows up
    then. A series that is simply short is not one of them: the provider was
    already asked for everything it has.
    """
    clauses = []
    if overview.excluded:
        clauses.append(
            "fora do patrimonio, da variacao e das rentabilidades: "
            f"{_listed(overview.excluded, overview.excluded_reasons)}"
        )
    if overview.excluded_from_returns:
        clauses.append(
            "fora das rentabilidades, mas dentro do patrimonio: "
            f"{_listed(overview.excluded_from_returns, overview.returns_reasons)}"
        )
    # A primeira clausula continua a frase do "Nota:"; as seguintes viram frase
    # propria, e so por isso ganham maiuscula.
    body = ". ".join([clauses[0], *(clause[0].upper() + clause[1:] for clause in clauses[1:])])
    note = f"[yellow]Nota:[/yellow] {body}."
    if RETRIABLE & set(overview.all_reasons.values()):
        note += " [dim]'r' pede de novo; se insistir, feche e abra o bogle.[/dim]"
    return note


def _stale_note(overview: PortfolioOverview) -> str:
    """Which tickers are priced before the reference close, and at which one.

    The provider publishes each session's bar on its own schedule: asked for a
    date it has not reached, the valuator answers with the last close it has and
    says nothing. So the panel announces one fechamento while part of the
    portfolio sits on an earlier one — and the user finds the Position screen,
    whose live quote already has the missing day, showing a different patrimony.

    Names the ticker and the date, like the exclusion note does, instead of a
    single "os dados estao atrasados": with both, the difference against the
    Position screen is checkable line by line, which is what turns a number that
    looks wrong into a number that is merely older.
    """
    listed = ", ".join(
        f"{escape(ticker)} ({when.isoformat()})" for ticker, when in sorted(overview.stale_prices.items())
    )
    return (
        f"[yellow]Nota:[/yellow] sem fechamento de {overview.as_of.isoformat()} para {listed}; "
        "avaliados no ultimo fechamento disponivel."
    )


def _pending_note(overview: PortfolioOverview) -> str:
    """What was registered after the reference close, and is therefore not here.

    First line of the note on purpose: it is the answer to the question the user
    has at that exact moment — they registered a purchase, came back, and the
    summary is identical. Without it the only reading left is that 'r' is broken.
    """
    count = overview.pending_entries
    entries = "1 lancamento" if count == 1 else f"{count} lancamentos"
    verb = "ainda nao entra" if count == 1 else "ainda nao entram"
    # Sem valor quando o movimento e zero: um provento nao muda patrimonio nem
    # capital investido, e um "+0.00" ao lado dele soaria como um erro de conta.
    moved = f" ({fmt.signed_money(overview.pending_invested)})" if overview.pending_invested != 0 else ""
    return (
        f"[yellow]Nota:[/yellow] {entries} depois de {overview.as_of.isoformat()}{moved} {verb}: "
        f"o resumo e do fechamento desse dia."
    )


def _note_for(overview: PortfolioOverview) -> str:
    """The note under the metrics: what is pending, how fresh it is, what it is.

    In that order because it is the order of the questions: what I just
    registered is missing, then why the number does not match the live quote,
    then how to read what is on screen.
    """
    lines = [_pending_note(overview)] if overview.has_pending else []
    if overview.has_stale_prices:
        lines.append(_stale_note(overview))
    lines.append(_summary_note(overview))
    return "\n".join(line for line in lines if line)


def _summary_note(overview: PortfolioOverview) -> str:
    if overview.is_empty:
        return "[yellow]Nenhuma transacao registrada ainda.[/yellow]"
    if overview.excluded or overview.excluded_from_returns:
        return _excluded_note(overview)
    if overview.patrimony is None:
        # Carteira inteira comprada depois da referencia: a linha de cima ja
        # explicou o vazio, e repetir "no fechamento de X" seria dizer a mesma
        # coisa duas vezes, com "Nota:" duas vezes.
        if overview.has_pending:
            return ""
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
