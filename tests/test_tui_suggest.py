"""Tests for the TUI's contribution screen (issue #76): the amount, the split and
the cycle evaluation it records.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from decimal import ROUND_DOWN, Decimal
from typing import Any

import pytest
from textual.widgets import DataTable, Input

from bogle.domain.assets import AssetType
from bogle.domain.errors import MissingPriceError, ValidationError
from bogle.format import MASK
from bogle.rebalancing import AporteSuggestion, TickerSuggestion, UnquotedTarget
from bogle.tui import services
from bogle.tui.screens.modals import EditModal
from bogle.tui.screens.suggest import SuggestScreen
from bogle.tui.widgets.form import Field
from tests.tui_fakes import (
    ToastSpy,
    make_app,
    make_suggestion,
    open_screen,
    settle,
    stub_services,
    table_columns,
    table_rows,
)


class SuggestSpy:
    """Serves a suggestion and records what was asked for."""

    def __init__(
        self,
        *,
        error: Exception | None = None,
        build: Callable[..., AporteSuggestion] = make_suggestion,
    ) -> None:
        self.amounts: list[Decimal] = []
        self.calls: list[dict[str, Any]] = []
        self.error = error
        self.build = build

    def __call__(self, amount: Decimal, **kwargs: Any) -> Any:
        if self.error is not None:
            raise self.error
        self.amounts.append(amount)
        # Copia: a tela reaproveita os mesmos dicts entre as cargas.
        informed = {key: dict(kwargs.get(key) or {}) for key in ("prices", "quantities", "values")}
        self.calls.append({**kwargs, **informed})
        return self.build(amount=amount, **informed)

    @property
    def last(self) -> dict[str, Any]:
        return self.calls[-1]


@pytest.fixture(autouse=True)
def _services(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_services(monkeypatch)


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> SuggestSpy:
    loader = SuggestSpy()
    monkeypatch.setattr(services, "load_suggestion", loader)
    return loader


async def ask(pilot: Any, screen: SuggestScreen, amount: str) -> None:
    field = screen.query_one("#amount", Field)
    field.set_value(amount)
    field.input.focus()
    await pilot.press("enter")
    await settle(pilot)


class TestAmount:
    @pytest.mark.asyncio
    async def test_opens_asking_for_the_amount_without_fetching(self, spy: SuggestSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            assert spy.amounts == []
            assert screen.note == "Informe o valor do aporte e pressione Enter."
            assert app.focused is screen.query_one("#amount", Field).input

    @pytest.mark.asyncio
    async def test_enter_asks_for_the_split_and_shows_the_amount_in_the_header(self, spy: SuggestSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            assert spy.amounts == [Decimal("1500")]
            assert screen.sub_title == "aporte - 1,500.00"

    @pytest.mark.asyncio
    async def test_an_invalid_amount_never_reaches_the_service(self, spy: SuggestSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "0")
            assert spy.amounts == []
            assert screen.query_one("#amount", Field).error == "Valor do aporte deve ser maior que zero, recebido 0."

    @pytest.mark.asyncio
    async def test_r_recalculates_the_same_amount(self, spy: SuggestSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            screen.query_one(DataTable).focus()  # sai do campo para o atalho valer
            await pilot.press("r")
            await settle(pilot)
            assert spy.amounts == [Decimal("1500"), Decimal("1500")]


class TestSplit:
    @pytest.mark.asyncio
    async def test_columns_and_rows_match_the_command(self, spy: SuggestSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            assert table_columns(screen) == [
                "Ticker",
                "Preco",
                "Valor sugerido",
                "Qtde papeis",
                "Custo efetivo",
                "Peso atual",
                "Target",
                "Peso apos",
                "Drift apos",
            ]
            assert table_rows(screen)[0] == [
                "AUVP11",
                "126.25",
                "1,010.00",
                "8",
                "1,010.00",
                "26.10%",
                "30.00%",
                "28.40%",
                "-1.60%",
            ]

    @pytest.mark.asyncio
    async def test_fixed_income_has_no_share_count(self, spy: SuggestSpy) -> None:
        # Renda fixa entra por valor, nao por cota inteira.
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            assert table_rows(screen)[1][3] == "-"

    @pytest.mark.asyncio
    async def test_the_totals_close_the_account(self, spy: SuggestSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            assert "Alocado 1,499.50" in screen.totals
            assert "Taxa B3 (est.) 0.30" in screen.totals
            assert "Total 1,499.80" in screen.totals
            assert "Sobra (caixa) 0.20" in screen.totals

    @pytest.mark.asyncio
    async def test_the_totals_leave_the_amount_to_the_field(self, spy: SuggestSpy) -> None:
        # O aporte e o que voce tem para aplicar, e ja esta no campo e no
        # subtitulo; nos totais ele so repetiria um numero.
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            assert "Aporte" not in screen.totals

    @pytest.mark.asyncio
    async def test_the_totals_fit_in_one_line_when_there_is_room(self, spy: SuggestSpy) -> None:
        # Em 80 colunas os quatro pares nao cabem e a quebra cai entre dois deles;
        # com espaco, a sobra fica na mesma linha do que as compras custam.
        app = make_app()
        async with app.run_test(size=(140, 40)) as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            assert screen.totals.splitlines()[0] == (
                "Alocado 1,499.50   Taxa B3 (est.) 0.30   Total 1,499.80   Sobra (caixa) 0.20"
            )
            totals = screen.query_one("#suggest-totals")
            assert totals.size.height == 2  # os pares numa linha, a procedencia na outra

    @pytest.mark.asyncio
    async def test_the_warnings_share_one_numbered_attention(self, monkeypatch: pytest.MonkeyPatch) -> None:
        warnings = ["Preços de MUND11, NB1011 definidos pelo usuário", "Compra em NB1011 fixada pelo usuário"]
        monkeypatch.setattr(
            services, "load_suggestion", lambda amount, **_: make_suggestion(amount=amount, warnings=warnings)
        )
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            assert screen.note == (
                "Atenção:\n1. Preços de MUND11, NB1011 definidos pelo usuário\n2. Compra em NB1011 fixada pelo usuário"
            )

    @pytest.mark.asyncio
    async def test_without_warnings_there_is_no_note(self, spy: SuggestSpy) -> None:
        # A legenda das teclas esta no rodape, a taxa e marcada como estimada no
        # proprio rotulo, e o registro do ciclo acontece sem ser anunciado.
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            assert screen.note == ""


class TestHiddenAmounts:
    @pytest.mark.asyncio
    async def test_h_also_takes_the_amount_out_of_the_header(self, spy: SuggestSpy) -> None:
        # O subtitulo carrega o valor do aporte: mascarar a tabela e deixa-lo no
        # cabecalho esconderia o aporte no lugar menos visivel da tela.
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            assert screen.sub_title == "aporte - 1,500.00"
            screen.query_one(DataTable).focus()
            await pilot.press("h")
            await pilot.pause()
            assert screen.sub_title == f"aporte - {MASK}"

    @pytest.mark.asyncio
    async def test_h_masks_the_amounts_but_keeps_the_weights(self, spy: SuggestSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            screen.query_one(DataTable).focus()
            await pilot.press("h")
            await pilot.pause()
            # Os quatro pesos ficam: eles dizem como a carteira esta, nao quanto
            # ha nela.
            assert table_rows(screen)[0] == [
                "AUVP11",
                MASK,
                MASK,
                MASK,
                MASK,
                "26.10%",
                "30.00%",
                "28.40%",
                "-1.60%",
            ]
            assert f"Alocado {MASK}" in screen.totals
            assert f"Taxa B3 (est.) {MASK}" in screen.totals
            assert f"Sobra (caixa) {MASK}" in screen.totals


class TestFailures:
    @pytest.mark.asyncio
    async def test_an_unpriced_portfolio_is_reported_and_keeps_the_screen(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(services, "load_suggestion", SuggestSpy(error=MissingPriceError(["CDB01"])))
        toasts = ToastSpy()
        toasts.install(monkeypatch, SuggestScreen)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            assert isinstance(app.screen, SuggestScreen)
            assert "Sem preco atual para: CDB01" in screen.note
            assert toasts.severity_of("Sem preco atual") == "error"

    @pytest.mark.asyncio
    async def test_an_empty_portfolio_says_so(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            services,
            "load_suggestion",
            SuggestSpy(error=ValidationError("Nenhuma posicao ativa para sugerir aporte.")),
        )
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            assert screen.note == "Nenhuma posicao ativa para sugerir aporte."
            assert table_rows(screen) == []


class TestManualPrice:
    """'p' define o preco de uma ordem limitada e recalcula a sugestao."""

    @pytest.mark.asyncio
    async def test_p_opens_the_modal_with_the_market_price_and_its_time(self, spy: SuggestSpy) -> None:
        # O horario e o ponto: a cotacao na tabela pode ter minutos de atraso, e e
        # contra o preco de agora que uma ordem limitada e decidida.
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            await pilot.press("p")
            await settle(pilot)
            modal = app.screen
            assert isinstance(modal, EditModal)
            assert modal.dialog_title == "Preco de AUVP11"
            assert "126.25" in modal.body
            assert "14:07" in modal.body
            assert modal.typed == ""  # nenhum preco informado ainda

    @pytest.mark.asyncio
    async def test_a_price_recalculates_the_shares_and_marks_the_row(self, spy: SuggestSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            assert table_rows(screen)[0][3] == "8"  # 1010 / 126.25
            await pilot.press("p")
            await settle(pilot)
            app.screen.query_one(Input).value = "100"
            await pilot.press("enter")
            await settle(pilot)
            assert spy.last["prices"] == {"AUVP11": Decimal("100")}
            row = table_rows(screen)[0]
            assert row[1] == "100.00 *"  # marcado como informado por voce
            assert row[3] == "10"  # 1010 / 100

    @pytest.mark.asyncio
    async def test_the_comma_is_accepted_like_anywhere_else(self, spy: SuggestSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            await pilot.press("p")
            await settle(pilot)
            app.screen.query_one(Input).value = "114,86"
            await pilot.press("enter")
            await settle(pilot)
            assert spy.last["prices"] == {"AUVP11": Decimal("114.86")}

    @pytest.mark.asyncio
    async def test_an_empty_value_goes_back_to_the_quote(self, spy: SuggestSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            screen.prices["AUVP11"] = Decimal("100")
            screen.fetch()
            await settle(pilot)
            await pilot.press("p")
            await settle(pilot)
            modal = app.screen
            assert isinstance(modal, EditModal)
            assert modal.typed == "100"  # reabre com o que foi informado
            modal.query_one(Input).value = ""
            await pilot.press("enter")
            await settle(pilot)
            assert spy.last["prices"] == {}
            assert table_rows(screen)[0][1] == "126.25"

    @pytest.mark.asyncio
    async def test_escape_leaves_the_price_alone(self, spy: SuggestSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            await pilot.press("p")
            await settle(pilot)
            app.screen.query_one(Input).value = "100"
            await pilot.press("escape")
            await settle(pilot)
            assert screen.prices == {}
            assert len(spy.calls) == 1  # nao recalculou

    @pytest.mark.asyncio
    async def test_an_invalid_price_is_refused_without_losing_the_table(
        self, spy: SuggestSpy, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Recusado na tela, e nao no motor: pelo caminho do erro a tabela seria
        # limpa, e um dedo errado nao deve custar a sugestao que estava a vista.
        toasts = ToastSpy()
        toasts.install(monkeypatch, SuggestScreen)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            await pilot.press("p")
            await settle(pilot)
            app.screen.query_one(Input).value = "-3"
            await pilot.press("enter")
            await settle(pilot)
            assert screen.prices == {}
            assert len(spy.calls) == 1
            assert table_rows(screen)[0][1] == "126.25"
            assert toasts.severity_of("maior que zero") == "error"

    @pytest.mark.asyncio
    async def test_fixed_income_has_no_price_to_set(self, spy: SuggestSpy, monkeypatch: pytest.MonkeyPatch) -> None:
        toasts = ToastSpy()
        toasts.install(monkeypatch, SuggestScreen)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            screen.query_one(DataTable).move_cursor(row=1)  # CDB-XP-2027
            await pilot.pause()
            await pilot.press("p")
            await settle(pilot)
            assert isinstance(app.screen, SuggestScreen)  # nenhum modal abriu
            assert toasts.severity_of("nao tem preco a definir") == "warning"

    @pytest.mark.asyncio
    async def test_the_engine_warning_reaches_the_note(self, monkeypatch: pytest.MonkeyPatch) -> None:
        warning = "Preço de AUVP11 definido pelo usuário"
        monkeypatch.setattr(
            services, "load_suggestion", lambda amount, **_: make_suggestion(amount=amount, warnings=[warning])
        )
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            assert warning in screen.note


def unquoted_suggestion(
    *, amount: Decimal | None = None, prices: Mapping[str, Decimal] | None = None, **overrides: Any
) -> AporteSuggestion:
    """The usual suggestion plus MUND11, a fund with no quote yet (its first day).

    Without a price it sits the contribution out; with one it joins the split the
    way the engine does it — at the top, where the money went, marked as informed.
    """
    base = make_suggestion(amount=amount, prices=prices, **overrides)
    price = {ticker.upper(): value for ticker, value in (prices or {}).items()}.get("MUND11")
    if price is None:
        return replace(base, unquoted=[UnquotedTarget("MUND11", AssetType.ETF, Decimal("0.6"), Decimal("0"))])
    shares = (Decimal("900") / price).to_integral_value(rounding=ROUND_DOWN)
    mund = TickerSuggestion(
        ticker="MUND11",
        asset_type=AssetType.ETF,
        price=price,
        allocation=Decimal("900.00"),
        quantity=shares,
        effective_cost=shares * price,
        target_weight=Decimal("0.6"),
        weight_after=Decimal("0.2"),
        current_weight=Decimal("0"),
        is_manual_price=True,
    )
    return replace(base, items=[mund, *base.items])


class TestUnquotedTarget:
    """Um target sem cotacao ganha uma linha, e o p nela o traz para o aporte."""

    @pytest.fixture
    def spy(self, monkeypatch: pytest.MonkeyPatch) -> SuggestSpy:
        loader = SuggestSpy(build=unquoted_suggestion)
        monkeypatch.setattr(services, "load_suggestion", loader)
        return loader

    @pytest.mark.asyncio
    async def test_it_gets_a_row_at_the_bottom(self, spy: SuggestSpy) -> None:
        # So no aviso, o ticker nao teria linha para o p agir — e era o que faltava.
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            assert table_rows(screen)[-1] == [
                "MUND11",
                "sem cotacao",
                "-",
                "-",
                "-",
                "0.00%",
                "60.00%",
                "0.00%",
                "-60.00%",
            ]

    @pytest.mark.asyncio
    async def test_p_on_it_says_there_is_no_quote(self, spy: SuggestSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            screen.query_one(DataTable).move_cursor(row=2)  # MUND11
            await pilot.pause()
            await pilot.press("p")
            await settle(pilot)
            modal = app.screen
            assert isinstance(modal, EditModal)
            assert modal.dialog_title == "Preco de MUND11"
            assert "Sem cotacao do provedor" in modal.body
            assert modal.typed == ""

    @pytest.mark.asyncio
    async def test_a_price_brings_it_into_the_split(self, spy: SuggestSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            screen.query_one(DataTable).move_cursor(row=2)
            await pilot.pause()
            await pilot.press("p")
            await settle(pilot)
            app.screen.query_one(Input).value = "100"
            await pilot.press("enter")
            await settle(pilot)
            assert spy.last["prices"] == {"MUND11": Decimal("100")}
            rows = table_rows(screen)
            mund = next(row for row in rows if row[0] == "MUND11")
            assert mund[1] == "100.00 *"
            assert mund[3] == "9"  # 900 / 100
            assert all(row[1] != "sem cotacao" for row in rows)

    @pytest.mark.asyncio
    async def test_the_cursor_follows_the_ticker_when_the_rows_move(self, spy: SuggestSpy) -> None:
        # Com o preco o MUND11 sobe para o topo; o cursor ficar na linha 2 poria a
        # proxima tecla num ticker que voce nao escolheu.
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            table = screen.query_one(DataTable)
            table.move_cursor(row=2)
            await pilot.pause()
            await pilot.press("p")
            await settle(pilot)
            app.screen.query_one(Input).value = "100"
            await pilot.press("enter")
            await settle(pilot)
            assert table_rows(screen)[0][0] == "MUND11"
            assert table.cursor_row == 0

    @pytest.mark.asyncio
    async def test_once_priced_the_modal_says_empty_leaves_it_out(self, spy: SuggestSpy) -> None:
        # "Em branco volta a cotacao" seria falso: nao ha cotacao para voltar.
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            screen.prices["MUND11"] = Decimal("100")
            screen.fetch()
            await settle(pilot)
            screen.query_one(DataTable).move_cursor(row=0)  # MUND11, agora no aporte
            await pilot.pause()
            await pilot.press("p")
            await settle(pilot)
            modal = app.screen
            assert isinstance(modal, EditModal)
            assert modal.typed == "100"
            assert "Em branco fica fora dele" in modal.body
            assert "volta a usar a cotacao" not in modal.body

    @pytest.mark.asyncio
    async def test_fixed_income_without_a_quote_is_not_fixed_by_a_price(self, monkeypatch: pytest.MonkeyPatch) -> None:
        tesouro = UnquotedTarget("TESOURO-IPCA-2035", AssetType.TESOURO, Decimal("0.2"), Decimal("0"))
        monkeypatch.setattr(
            services,
            "load_suggestion",
            lambda amount, **_: make_suggestion(amount=amount, unquoted=[tesouro]),
        )
        toasts = ToastSpy()
        toasts.install(monkeypatch, SuggestScreen)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            screen.query_one(DataTable).move_cursor(row=2)
            await pilot.pause()
            await pilot.press("p")
            await settle(pilot)
            assert isinstance(app.screen, SuggestScreen)  # nenhum modal abriu
            assert toasts.severity_of("nao se aplica") == "warning"


class TestPinnedPurchase:
    """'q' fixa a compra de um ticker e o resto do aporte vai para os outros."""

    @pytest.mark.asyncio
    async def test_q_asks_for_whole_shares_on_variable_income(self, spy: SuggestSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            await pilot.press("q")
            await settle(pilot)
            modal = app.screen
            assert isinstance(modal, EditModal)
            assert modal.dialog_title == "Quantidade de AUVP11"
            assert "Cotas inteiras" in modal.body
            assert modal.typed == ""

    @pytest.mark.asyncio
    async def test_a_quantity_reaches_the_engine_and_marks_the_row(self, spy: SuggestSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            await pilot.press("q")
            await settle(pilot)
            app.screen.query_one(Input).value = "5"
            await pilot.press("enter")
            await settle(pilot)
            assert spy.last["quantities"] == {"AUVP11": Decimal("5")}
            assert spy.last["refresh"] is False  # mudou a compra, nao a cotacao
            row = table_rows(screen)[0]
            assert row[3] == "5 *"
            assert row[4] == "631.25"  # 5 x 126.25

    @pytest.mark.asyncio
    async def test_zero_is_a_quantity(self, spy: SuggestSpy) -> None:
        # Zero e como se tira o ticker deste aporte — nao um campo vazio.
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            await pilot.press("q")
            await settle(pilot)
            app.screen.query_one(Input).value = "0"
            await pilot.press("enter")
            await settle(pilot)
            assert spy.last["quantities"] == {"AUVP11": Decimal("0")}

    @pytest.mark.asyncio
    async def test_fixed_income_is_pinned_by_value(self, spy: SuggestSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            screen.query_one(DataTable).move_cursor(row=1)  # CDB-XP-2027
            await pilot.pause()
            await pilot.press("q")
            await settle(pilot)
            modal = app.screen
            assert isinstance(modal, EditModal)
            assert modal.dialog_title == "Valor de CDB-XP-2027"
            modal.query_one(Input).value = "300,50"
            await pilot.press("enter")
            await settle(pilot)
            assert spy.last["values"] == {"CDB-XP-2027": Decimal("300.50")}
            assert spy.last["quantities"] == {}
            row = table_rows(screen)[1]
            assert row[3] == "-"
            assert row[4] == "300.50 *"

    @pytest.mark.asyncio
    async def test_an_empty_value_goes_back_to_the_suggestion(self, spy: SuggestSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            screen.quantities["AUVP11"] = Decimal("5")
            screen.fetch()
            await settle(pilot)
            await pilot.press("q")
            await settle(pilot)
            modal = app.screen
            assert isinstance(modal, EditModal)
            assert modal.typed == "5"  # reabre com o que foi fixado
            modal.query_one(Input).value = ""
            await pilot.press("enter")
            await settle(pilot)
            assert spy.last["quantities"] == {}
            assert table_rows(screen)[0][3] == "8"

    @pytest.mark.asyncio
    async def test_escape_leaves_the_purchase_alone(self, spy: SuggestSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            await pilot.press("q")
            await settle(pilot)
            app.screen.query_one(Input).value = "5"
            await pilot.press("escape")
            await settle(pilot)
            assert screen.quantities == {}
            assert len(spy.calls) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("typed", "message"), [("1,5", "numero inteiro de cotas"), ("-2", "zero ou mais")])
    async def test_a_quantity_that_is_not_whole_shares_is_refused_without_losing_the_table(
        self, spy: SuggestSpy, monkeypatch: pytest.MonkeyPatch, typed: str, message: str
    ) -> None:
        toasts = ToastSpy()
        toasts.install(monkeypatch, SuggestScreen)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            await pilot.press("q")
            await settle(pilot)
            app.screen.query_one(Input).value = typed
            await pilot.press("enter")
            await settle(pilot)
            assert screen.quantities == {}
            assert len(spy.calls) == 1
            assert table_rows(screen)[0][3] == "8"
            assert toasts.severity_of(message) == "error"

    @pytest.mark.asyncio
    async def test_the_engine_warning_reaches_the_note(self, monkeypatch: pytest.MonkeyPatch) -> None:
        warning = "Compra em AUVP11 fixada pelo usuário"
        monkeypatch.setattr(
            services, "load_suggestion", lambda amount, **_: make_suggestion(amount=amount, warnings=[warning])
        )
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            assert warning in screen.note

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("target", "message"),
        [
            (UnquotedTarget("MUND11", AssetType.ETF, Decimal("0.6"), Decimal("0")), "informe o preco (p)"),
            (
                UnquotedTarget("TESOURO-IPCA-2035", AssetType.TESOURO, Decimal("0.2"), Decimal("0")),
                "confira o ticker no cadastro",
            ),
        ],
    )
    async def test_an_unquoted_target_is_not_pinned(
        self, monkeypatch: pytest.MonkeyPatch, target: UnquotedTarget, message: str
    ) -> None:
        monkeypatch.setattr(
            services, "load_suggestion", lambda amount, **_: make_suggestion(amount=amount, unquoted=[target])
        )
        toasts = ToastSpy()
        toasts.install(monkeypatch, SuggestScreen)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            screen.query_one(DataTable).move_cursor(row=2)
            await pilot.pause()
            await pilot.press("q")
            await settle(pilot)
            assert isinstance(app.screen, SuggestScreen)  # nenhum modal abriu
            assert toasts.severity_of(message) == "warning"


class TestProvenance:
    @pytest.mark.asyncio
    async def test_the_totals_say_where_the_price_came_from_and_when(self, spy: SuggestSpy) -> None:
        # Sem isso a tela mostra um preco sem dizer de que momento ele e — que foi
        # exatamente a duvida que trouxe esta funcionalidade.
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            assert "Fonte(s) brapi, calculado" in screen.totals
            assert "Cotação 2026-08-11 14:07" in screen.totals


class TestFreshQuotes:
    @pytest.mark.asyncio
    async def test_r_asks_the_provider_again(self, spy: SuggestSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            assert spy.last["refresh"] is False
            screen.query_one(DataTable).focus()
            await pilot.press("r")
            await settle(pilot)
            assert spy.last["refresh"] is True

    @pytest.mark.asyncio
    async def test_a_price_change_reuses_the_cached_quote(self, spy: SuggestSpy) -> None:
        # Informar um preco nao e motivo para bater na API: o que mudou foi o que
        # o usuario quer pagar, nao a cotacao.
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, SuggestScreen())
            await ask(pilot, screen, "1500")
            await pilot.press("p")
            await settle(pilot)
            app.screen.query_one(Input).value = "100"
            await pilot.press("enter")
            await settle(pilot)
            assert spy.last["refresh"] is False
