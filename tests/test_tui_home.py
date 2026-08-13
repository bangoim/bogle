"""Tests for the TUI's Home screen (issue #73): the D-1 summary, the navigation
and how an expected failure is reported.
"""

from __future__ import annotations

import threading
from datetime import date
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from psycopg import errors as pg_errors

from bogle import format as fmt
from bogle.domain.errors import MarketDataError
from bogle.format import MASK
from bogle.reports.valuation import NO_SOURCE, SHORT_SERIES
from bogle.tui import services
from bogle.tui.app import BogleApp
from bogle.tui.screens.config import ConfigScreen
from bogle.tui.screens.home import HomeScreen
from bogle.tui.screens.position import PositionScreen
from bogle.tui.screens.register import RegisterScreen
from bogle.tui.screens.reports import ReportsScreen
from bogle.tui.screens.status import StatusScreen
from bogle.tui.widgets.menu import Menu
from bogle.tui.widgets.metric import PLACEHOLDER, Metric
from tests.tui_fakes import (
    ToastSpy,
    empty_overview,
    make_app,
    make_overview,
    settle,
    stub_services,
)


@pytest.fixture(autouse=True)
def _services(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_services(monkeypatch)


def use_overview(monkeypatch: pytest.MonkeyPatch, overview: Any) -> None:
    monkeypatch.setattr(services, "load_overview", lambda **_: overview)


def metric(screen: HomeScreen, metric_id: str) -> str:
    return screen.query_one(f"#{metric_id}", Metric).value


class TestSummary:
    @pytest.mark.asyncio
    async def test_shows_the_four_headline_numbers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        use_overview(monkeypatch, make_overview())
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            home = app.screen
            assert isinstance(home, HomeScreen)
            assert metric(home, "patrimony") == "7,866.20"
            assert metric(home, "variation") == "+516.20  (+7.02%)"
            assert metric(home, "twr-12m") == "+12.75%"
            assert metric(home, "twr-total") == "+18.40%"

    @pytest.mark.asyncio
    async def test_reference_close_is_in_the_panel_title(self, monkeypatch: pytest.MonkeyPatch) -> None:
        use_overview(monkeypatch, make_overview())
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            assert app.screen.query_one("#summary").border_title == "Carteira - fechamento de 2026-08-11"

    @pytest.mark.asyncio
    async def test_starts_with_a_placeholder_before_the_worker_answers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        release = threading.Event()

        def slow(**_: Any) -> Any:
            release.wait(timeout=5)
            return make_overview()

        monkeypatch.setattr(services, "load_overview", slow)
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert metric(app.screen, "patrimony") == PLACEHOLDER  # type: ignore[arg-type]
            release.set()
            await settle(pilot)
            assert metric(app.screen, "patrimony") == "7,866.20"  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_twr_legend_is_shown_when_everything_is_priced(self, monkeypatch: pytest.MonkeyPatch) -> None:
        use_overview(monkeypatch, make_overview())
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            assert "TWR" in app.screen.note  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_empty_ledger_says_so(self, monkeypatch: pytest.MonkeyPatch) -> None:
        use_overview(monkeypatch, empty_overview())
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            home = app.screen
            assert isinstance(home, HomeScreen)
            assert home.note == "Nenhuma transacao registrada ainda."
            assert metric(home, "patrimony") == "-"
            assert metric(home, "variation") == "-"

    @pytest.mark.asyncio
    async def test_excluded_tickers_are_reported_with_the_reason(self, monkeypatch: pytest.MonkeyPatch) -> None:
        use_overview(
            monkeypatch,
            make_overview(
                excluded=["TESOURO-IPCA-2035"],
                excluded_reasons={"TESOURO-IPCA-2035": NO_SOURCE},
            ),
        )
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            note = app.screen.note  # type: ignore[attr-defined]
            assert "TESOURO-IPCA-2035" in note
            assert NO_SOURCE in note
            assert "fora do patrimonio, da variacao e das rentabilidades" in note

    @pytest.mark.asyncio
    async def test_a_provider_hiccup_says_it_is_worth_trying_again(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # "Sem historico" lia como fato permanente do ativo; a serie curta e o
        # provedor tendo um minuto ruim, e reabrir resolve.
        use_overview(
            monkeypatch,
            make_overview(excluded=["VWRA11"], excluded_reasons={"VWRA11": SHORT_SERIES}),
        )
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            note = app.screen.note  # type: ignore[attr-defined]
            assert SHORT_SERIES in note
            assert "feche e abra o bogle" in note

    @pytest.mark.asyncio
    async def test_a_permanent_exclusion_does_not_suggest_retrying(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Nada que o usuario faca traz historico de Tesouro (#17): sugerir tentar
        # de novo seria mandar bater numa porta fechada.
        use_overview(
            monkeypatch,
            make_overview(excluded=["TESOURO-IPCA-2035"], excluded_reasons={"TESOURO-IPCA-2035": NO_SOURCE}),
        )
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            assert "feche e abra" not in app.screen.note  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_a_partial_reading_is_not_labelled_total(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Com ticker de fora, o numero e um subconjunto da carteira: o rotulo tem
        # de dizer isso, senao a Home e a tela de Posicao mostram dois
        # "Patrimonio total" diferentes sem explicacao.
        use_overview(monkeypatch, make_overview(excluded=["TESOURO-IPCA-2035"]))
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            home = app.screen
            assert isinstance(home, HomeScreen)
            assert home.query_one("#patrimony", Metric).caption == "Patrimonio parcial"
            assert home.query_one("#variation", Metric).caption == "Variacao parcial"

    @pytest.mark.asyncio
    async def test_a_complete_reading_keeps_the_plain_labels(self, monkeypatch: pytest.MonkeyPatch) -> None:
        use_overview(monkeypatch, make_overview())
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            home = app.screen
            assert isinstance(home, HomeScreen)
            assert home.query_one("#patrimony", Metric).caption == "Patrimonio total"
            assert home.query_one("#variation", Metric).caption == "Variacao"

    @pytest.mark.asyncio
    async def test_a_twelve_month_window_shorter_than_its_name_is_disclosed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Carteira com menos de 12 meses: a janela ancora na primeira transacao,
        # entao "12m" cobre menos que isso e a nota diz desde quando.
        use_overview(monkeypatch, make_overview(inception=date(2026, 5, 1), twr_12m_start=date(2026, 5, 1)))
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            assert "ancora na primeira transacao (2026-05-01)" in app.screen.note  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_ticker_with_brackets_is_not_read_as_markup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Ticker e dado do usuario e a nota e montada com markup: sem escape,
        # "[/]" e lido como tag de fechamento e a atualizacao da nota estoura.
        use_overview(monkeypatch, make_overview(excluded=["TES[/]2035", "AB[dim]CD"]))
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            note = app.screen.note  # type: ignore[attr-defined]
            assert "TES[/]2035" in note
            assert "AB[dim]CD" in note

    @pytest.mark.asyncio
    async def test_variation_without_a_base_drops_the_percentage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Investido <= 0 (vendas devolveram mais do que entrou): a porcentagem
        # nao tem base, mas o valor em R$ continua valendo.
        use_overview(monkeypatch, make_overview(invested=Decimal("-100"), patrimony=Decimal("50")))
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            assert metric(app.screen, "variation") == "+150.00"  # type: ignore[arg-type]


class TestHiddenAmounts:
    @pytest.mark.asyncio
    async def test_h_hides_the_amounts_and_keeps_the_percentages(self, monkeypatch: pytest.MonkeyPatch) -> None:
        use_overview(monkeypatch, make_overview())
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            home = app.screen
            assert isinstance(home, HomeScreen)
            await pilot.press("h")
            await pilot.pause()
            assert metric(home, "patrimony") == MASK
            assert metric(home, "variation") == f"{MASK}  (+7.02%)"
            assert metric(home, "twr-12m") == "+12.75%"  # desempenho nao e valor
            assert metric(home, "twr-total") == "+18.40%"

    @pytest.mark.asyncio
    async def test_h_again_brings_them_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        use_overview(monkeypatch, make_overview())
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            await pilot.press("h")
            await pilot.pause()
            await pilot.press("h")
            await pilot.pause()
            assert metric(app.screen, "patrimony") == "7,866.20"  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_the_toggle_is_remembered_for_the_next_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Sem isso, quem oculta tem de ocultar de novo a cada abertura.
        saved: list[bool] = []
        monkeypatch.setattr(services, "save_hide_amounts", saved.append)
        use_overview(monkeypatch, make_overview())
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            await pilot.press("h")
            await settle(pilot)
            assert saved == [True]
            await pilot.press("h")
            await settle(pilot)
            assert saved == [True, False]

    @pytest.mark.asyncio
    async def test_a_failed_save_still_applies_and_warns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(hidden: bool) -> None:
            raise psycopg.OperationalError("connection refused")

        monkeypatch.setattr(services, "save_hide_amounts", boom)
        use_overview(monkeypatch, make_overview())
        toasts = ToastSpy()
        toasts.install(monkeypatch, BogleApp)
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            await pilot.press("h")
            await settle(pilot)
            assert metric(app.screen, "patrimony") == MASK  # type: ignore[arg-type]
            assert toasts.severity_of("nao foi salva") == "warning"

    @pytest.mark.asyncio
    async def test_opens_hidden_when_the_setting_says_so(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # `hide_values` decide a abertura; a TUI aplica antes de subir a app.
        use_overview(monkeypatch, make_overview())
        fmt.hide_amounts(True)
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            assert metric(app.screen, "patrimony") == MASK  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_reloading_while_hidden_keeps_them_hidden(self, monkeypatch: pytest.MonkeyPatch) -> None:
        use_overview(monkeypatch, make_overview())
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            await pilot.press("h")
            await pilot.pause()
            await pilot.press("r")  # recarrega do zero
            await settle(pilot)
            assert metric(app.screen, "patrimony") == MASK  # type: ignore[arg-type]


class TestNavigation:
    @pytest.mark.asyncio
    async def test_number_opens_the_position_screen_and_escape_comes_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        use_overview(monkeypatch, make_overview())
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            await pilot.press("1")
            await pilot.pause()
            assert isinstance(app.screen, PositionScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, HomeScreen)

    @pytest.mark.asyncio
    async def test_every_menu_item_opens_the_screen_it_promises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A lista pareia item e tela justamente para nao sair de sincronia; este
        # teste percorre a lista inteira em vez de fixar um numero.
        use_overview(monkeypatch, make_overview())
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            for item, factory in HomeScreen.ENTRIES:
                await pilot.press(item.key)
                await settle(pilot)
                assert isinstance(app.screen, type(factory())), f"{item.key} ({item.label})"
                await pilot.press("escape")
                await settle(pilot)
                assert isinstance(app.screen, HomeScreen)

    @pytest.mark.asyncio
    async def test_enter_on_the_menu_opens_the_highlighted_item(self, monkeypatch: pytest.MonkeyPatch) -> None:
        use_overview(monkeypatch, make_overview())
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            assert app.screen.query_one("#menu-left", Menu).has_focus
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, PositionScreen)

    @pytest.mark.asyncio
    async def test_arrows_move_the_highlight(self, monkeypatch: pytest.MonkeyPatch) -> None:
        use_overview(monkeypatch, make_overview())
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            await pilot.press("down", "enter")  # segundo item: Registrar
            await settle(pilot)
            assert isinstance(app.screen, RegisterScreen)

    @pytest.mark.asyncio
    async def test_s_and_c_open_the_two_screens_that_left_the_menu(self, monkeypatch: pytest.MonkeyPatch) -> None:
        use_overview(monkeypatch, make_overview())
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            await pilot.press("s")
            await settle(pilot)
            assert isinstance(app.screen, StatusScreen)
            await pilot.press("escape")
            await settle(pilot)
            await pilot.press("c")
            await settle(pilot)
            assert isinstance(app.screen, ConfigScreen)

    @pytest.mark.asyncio
    async def test_coming_back_from_config_refreshes_the_summary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A Config grava (separador, privacidade, indices): o resumo pode mudar,
        # entao ela conta como tela que suja a Home, igual as do menu.
        loads = []

        def load(**_: Any) -> Any:
            loads.append(1)
            return make_overview()

        monkeypatch.setattr(services, "load_overview", load)
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            await pilot.press("c")
            await settle(pilot)
            await pilot.press("escape")
            await settle(pilot)
            assert len(loads) == 2

    @pytest.mark.asyncio
    async def test_the_menu_splits_in_two_columns_on_a_wide_terminal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Seis itens em tres linhas: e o que faz o resumo e o logo caberem sem
        # scroll num terminal de altura normal.
        use_overview(monkeypatch, make_overview())
        app = make_app()
        async with app.run_test(size=(148, 30)) as pilot:
            await settle(pilot)
            home = app.screen
            assert isinstance(home, HomeScreen)
            assert home.has_class("-wide")
            assert home.query_one("#menu").size.height == 3  # tres linhas de itens
            assert [menu.option_count for menu in home.query(Menu)] == [3, 3]

    @pytest.mark.asyncio
    async def test_a_narrower_terminal_keeps_the_single_column(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Abaixo de -wide as descricoes quebrariam em duas linhas cada, e a troca
        # sairia pior que a lista simples.
        use_overview(monkeypatch, make_overview())
        app = make_app()
        async with app.run_test(size=(90, 30)) as pilot:
            await settle(pilot)
            home = app.screen
            assert isinstance(home, HomeScreen)
            assert not home.has_class("-wide")
            assert home.query_one("#menu").size.height == 6  # os seis itens, um por linha

    @pytest.mark.asyncio
    async def test_the_side_arrows_move_between_the_columns_keeping_the_row(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        use_overview(monkeypatch, make_overview())
        app = make_app()
        async with app.run_test(size=(148, 30)) as pilot:
            await settle(pilot)
            home = app.screen
            assert isinstance(home, HomeScreen)
            left = home.query_one("#menu-left", Menu)
            right = home.query_one("#menu-right", Menu)

            await pilot.press("down")  # segunda linha da esquerda: Registrar
            await pilot.press("right")
            await pilot.pause()
            assert right.has_focus
            assert right.highlighted == 1  # Relatorios, a segunda da direita
            await pilot.press("enter")
            await settle(pilot)
            assert isinstance(app.screen, ReportsScreen)

            await pilot.press("escape")
            await settle(pilot)
            await pilot.press("left")
            await pilot.pause()
            assert left.has_focus

    @pytest.mark.asyncio
    async def test_q_quits_from_the_home_screen(self, monkeypatch: pytest.MonkeyPatch) -> None:
        use_overview(monkeypatch, make_overview())
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            assert app.is_running
            await pilot.press("q")
            await pilot.pause()
            assert not app.is_running

    @pytest.mark.asyncio
    async def test_q_does_not_quit_from_another_screen(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # `q` e atalho da Home; numa tela interna ele nao pode derrubar a app.
        use_overview(monkeypatch, make_overview())
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            await pilot.press("1")
            await settle(pilot)
            await pilot.press("q")
            await pilot.pause()
            assert app.is_running
            assert isinstance(app.screen, PositionScreen)

    @pytest.mark.asyncio
    async def test_coming_back_refreshes_the_summary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        loads = []

        def load(**_: Any) -> Any:
            loads.append(1)
            return make_overview()

        monkeypatch.setattr(services, "load_overview", load)
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            assert len(loads) == 1
            await pilot.press("1")
            await pilot.pause()
            await pilot.press("escape")
            await settle(pilot)
            assert len(loads) == 2  # um lancamento novo nao pode deixar o resumo velho


class TestFailures:
    @pytest.mark.asyncio
    async def test_provider_failure_becomes_a_toast_and_an_inline_note(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(**_: Any) -> Any:
            raise MarketDataError("Falha de rede ao acessar yfinance.", provider="yfinance")

        monkeypatch.setattr(services, "load_overview", boom)
        toasts = ToastSpy()
        toasts.install(monkeypatch, HomeScreen)
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            home = app.screen
            assert isinstance(home, HomeScreen)
            assert home.note == "Falha de rede ao acessar yfinance."
            assert metric(home, "patrimony") == "-"
            assert toasts.severity_of("yfinance") == "error"

    @pytest.mark.asyncio
    async def test_a_failed_reload_is_not_undone_by_the_privacy_toggle(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # O toggle redesenha do resumo guardado. Depois de uma recarga que falhou
        # nao existe resumo: ressuscitar o anterior mostraria numeros que a tela
        # acabou de dizer que nao tem, e apagaria a mensagem de erro.
        loads = {"n": 0}

        def load(**_: Any) -> Any:
            loads["n"] += 1
            if loads["n"] > 1:
                raise MarketDataError("Falha de rede ao acessar yfinance.", provider="yfinance")
            return make_overview()

        monkeypatch.setattr(services, "load_overview", load)
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            home = app.screen
            assert isinstance(home, HomeScreen)
            assert metric(home, "patrimony") == "7,866.20"

            await pilot.press("r")
            await settle(pilot)
            assert metric(home, "patrimony") == "-"

            await pilot.press("h")
            await pilot.pause()
            assert metric(home, "patrimony") == "-"
            assert home.note == "Falha de rede ao acessar yfinance."

    @pytest.mark.asyncio
    async def test_missing_schema_is_explained_instead_of_crashing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Banco sem as migracoes: erro previsivel, nao bug. Um worker que estoura
        # derruba a app inteira com traceback sobre a tela.
        def boom(**_: Any) -> Any:
            raise pg_errors.UndefinedTable('relation "transactions" does not exist')

        monkeypatch.setattr(services, "load_overview", boom)
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            assert app.is_running
            assert "Aplique as migracoes" in app.screen.note  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_database_down_shows_the_same_hint_as_the_cli(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(**_: Any) -> Any:
            raise psycopg.OperationalError("connection refused")

        monkeypatch.setattr(services, "load_overview", boom)
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            assert "nao foi possivel conectar ao banco de dados" in app.screen.note  # type: ignore[attr-defined]


class TestRebalanceReminder:
    @pytest.mark.asyncio
    async def test_overdue_cycle_is_a_warning_toast(self, monkeypatch: pytest.MonkeyPatch) -> None:
        use_overview(monkeypatch, make_overview())
        monkeypatch.setattr(services, "rebalance_notice", lambda **_: "ciclo vencido desde 2026-07-01.")
        toasts = ToastSpy()
        toasts.install(monkeypatch, HomeScreen)
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            assert toasts.messages == ["ciclo vencido desde 2026-07-01."]
            assert toasts.severity_of("ciclo vencido") == "warning"

    @pytest.mark.asyncio
    async def test_nothing_due_stays_quiet(self, monkeypatch: pytest.MonkeyPatch) -> None:
        use_overview(monkeypatch, make_overview())
        toasts = ToastSpy()
        toasts.install(monkeypatch, HomeScreen)
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            assert toasts.calls == []
