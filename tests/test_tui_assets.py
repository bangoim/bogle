"""Tests for the TUI's asset screens (issue #76): the list, the conditional
registration form, the update form and removal.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from textual.widgets import Checkbox, DataTable, Select

from bogle.domain.assets import AssetType, Indexer
from bogle.domain.errors import AssetHasTransactionsError, WeightSumExceededError
from bogle.tui import services
from bogle.tui.screens.assets import AssetFormScreen, AssetsScreen, AssetUpdateScreen
from bogle.tui.screens.modals import ConfirmModal
from bogle.tui.widgets.form import Field
from tests.tui_fakes import (
    ToastSpy,
    make_app,
    make_asset,
    make_assets,
    open_screen,
    settle,
    stub_services,
    table_columns,
    table_rows,
)


class AssetsSpy:
    """Serves the asset list and records the writes asked for."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.assets = make_assets()
        self.added: list[dict[str, Any]] = []
        self.updated: list[dict[str, Any]] = []
        self.removed: list[str] = []
        self.error = error
        self.loads = 0

    def list(self) -> Any:
        self.loads += 1
        return list(self.assets)

    def add(self, **fields: Any) -> Any:
        if self.error is not None:
            raise self.error
        self.added.append(fields)
        return make_asset(**fields)

    def update(self, **fields: Any) -> Any:
        if self.error is not None:
            raise self.error
        self.updated.append(fields)
        return make_asset(**fields)

    def remove(self, ticker: str) -> None:
        if self.error is not None:
            raise self.error
        self.removed.append(ticker)
        self.assets = [asset for asset in self.assets if asset.ticker != ticker]


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> AssetsSpy:
    stub_services(monkeypatch)
    assets = AssetsSpy()
    monkeypatch.setattr(services, "list_assets", assets.list)
    monkeypatch.setattr(services, "add_asset", assets.add)
    monkeypatch.setattr(services, "update_asset", assets.update)
    monkeypatch.setattr(services, "remove_asset", assets.remove)
    return assets


def fields_shown(screen: AssetFormScreen) -> dict[str, bool]:
    return {field.id: field.display for field in screen.query(Field) if field.id is not None}


def rows_shown(screen: AssetFormScreen) -> dict[str, bool]:
    return {row: screen.query_one(f"#{row}").display for row in ("prefixed-row", "indexer-row", "liquidity-row")}


class TestList:
    @pytest.mark.asyncio
    async def test_shows_the_metadata_the_cli_list_has_nowhere_to_show(self, spy: AssetsSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, AssetsScreen())
            assert table_columns(screen) == [
                "Ticker",
                "Tipo",
                "Target",
                "Emissor",
                "Indexador",
                "Taxa",
                "Liquidez",
                "Compra",
                "Vencimento",
            ]
            assert table_rows(screen)[1] == [
                "CDB-XP-2027",
                "CDB",
                "10.00%",
                "XP Investimentos",
                "CDI",
                "1.1",
                "no vencimento",
                "2026-04-01",
                "2027-04-01",
            ]

    @pytest.mark.asyncio
    async def test_variable_income_has_dashes_where_the_fields_do_not_apply(self, spy: AssetsSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, AssetsScreen())
            assert table_rows(screen)[0] == ["AUVP11", "FII", "30.00%", "-", "-", "-", "-", "-", "-"]

    @pytest.mark.asyncio
    async def test_a_prefixed_instrument_says_so_in_the_indexer_column(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub_services(monkeypatch)
        prefixed = make_asset(
            ticker="TESOURO-PRE-2029",
            asset_type=AssetType.TESOURO,
            target_weight=Decimal("0.1"),
            rate=Decimal("0.12"),
            is_prefixed=True,
        )
        monkeypatch.setattr(services, "list_assets", lambda: [prefixed])
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, AssetsScreen())
            assert table_rows(screen)[0][4] == "PREFIXADO"

    @pytest.mark.asyncio
    async def test_the_note_carries_the_weight_sum(self, spy: AssetsSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, AssetsScreen())
            assert screen.note == "4 ativos. Soma dos pesos: 80.00% (o maximo e 100.00%)."

    @pytest.mark.asyncio
    async def test_an_empty_portfolio_says_how_to_start(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub_services(monkeypatch)
        monkeypatch.setattr(services, "list_assets", list)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, AssetsScreen())
            assert screen.note == "Nenhum ativo cadastrado. Use 'a' para adicionar o primeiro."


class TestConditionalFields:
    @pytest.mark.asyncio
    async def test_variable_income_asks_for_nothing_else(self, spy: AssetsSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, AssetFormScreen())
            assert fields_shown(screen) == {
                "ticker": True,
                "weight": True,
                "issuer": False,
                "rate": False,
                "purchase-date": False,
                "maturity-date": False,
            }
            assert rows_shown(screen) == {"prefixed-row": False, "indexer-row": False, "liquidity-row": False}

    @pytest.mark.asyncio
    async def test_tesouro_asks_for_indexer_rate_and_dates_but_no_issuer(self, spy: AssetsSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, AssetFormScreen())
            screen.query_one("#asset-type", Select).value = AssetType.TESOURO
            await pilot.pause()
            shown = fields_shown(screen)
            assert shown["rate"] and shown["purchase-date"] and shown["maturity-date"]
            assert not shown["issuer"]  # nao se aplica a TESOURO
            assert rows_shown(screen) == {"prefixed-row": True, "indexer-row": True, "liquidity-row": False}

    @pytest.mark.asyncio
    async def test_private_fixed_income_also_asks_for_issuer_and_liquidity(self, spy: AssetsSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, AssetFormScreen())
            screen.query_one("#asset-type", Select).value = AssetType.CDB
            await pilot.pause()
            assert fields_shown(screen)["issuer"] is True
            assert rows_shown(screen)["liquidity-row"] is True

    @pytest.mark.asyncio
    async def test_prefixed_takes_the_indexer_off_the_form(self, spy: AssetsSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, AssetFormScreen())
            screen.query_one("#asset-type", Select).value = AssetType.TESOURO
            await pilot.pause()
            screen.query_one("#prefixed", Checkbox).value = True
            await pilot.pause()
            assert rows_shown(screen)["indexer-row"] is False

    @pytest.mark.asyncio
    async def test_daily_liquidity_makes_the_maturity_date_optional(self, spy: AssetsSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, AssetFormScreen())
            screen.query_one("#asset-type", Select).value = AssetType.CDB
            await pilot.pause()
            maturity = screen.field("maturity-date")
            assert maturity.check() == "Vencimento e obrigatoria."

            screen.query_one("#daily-liquidity", Checkbox).value = True
            await pilot.pause()
            assert maturity.check() is None
            assert "opcional" in maturity.input.placeholder

    @pytest.mark.asyncio
    async def test_switching_back_to_a_simpler_type_clears_what_it_does_not_accept(self, spy: AssetsSpy) -> None:
        # Um valor digitado num campo que o tipo novo nao aceita nao pode chegar ao
        # servico, e a marca de invalido do tipo anterior nao pode ficar na tela.
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, AssetFormScreen())
            screen.query_one("#asset-type", Select).value = AssetType.CDB
            await pilot.pause()
            screen.field("issuer").set_value("XP")
            screen.query_one("#asset-type", Select).value = AssetType.STOCK
            await pilot.pause()
            issuer = screen.field("issuer")
            assert issuer.value == ""
            assert issuer.check() is None  # nao se aplica: nao reprova o formulario
            assert not issuer.input.has_class("-invalid")


class TestRegistering:
    @pytest.mark.asyncio
    async def test_registers_variable_income_with_the_ticker_upper_cased(self, spy: AssetsSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, AssetFormScreen())
            screen.field("ticker").set_value("vale3")
            screen.field("weight").set_value("0.15")
            await pilot.press("ctrl+s")
            await settle(pilot)
            modal = app.screen
            assert isinstance(modal, ConfirmModal)
            assert modal.body == "VALE3 (STOCK), peso 15.00%"
            await pilot.press("enter")
            await settle(pilot)
            assert spy.added == [{"ticker": "VALE3", "target_weight": Decimal("0.15"), "asset_type": AssetType.STOCK}]

    @pytest.mark.asyncio
    async def test_registers_private_fixed_income_with_its_metadata(self, spy: AssetsSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, AssetFormScreen())
            screen.query_one("#asset-type", Select).value = AssetType.CDB
            await pilot.pause()
            screen.field("ticker").set_value("CDB-NU-2028")
            screen.field("weight").set_value("0.05")
            screen.field("issuer").set_value("Nubank")
            screen.field("rate").set_value("1.05")
            screen.field("purchase-date").set_value("2026-05-02")
            screen.field("maturity-date").set_value("2028-05-02")
            await pilot.press("ctrl+s")
            await settle(pilot)
            await pilot.press("enter")
            await settle(pilot)
            written = spy.added[-1]
            assert written["ticker"] == "CDB-NU-2028"
            assert written["issuer"] == "Nubank"
            assert written["indexer"] is Indexer.CDI
            assert written["rate"] == Decimal("1.05")
            assert written["is_prefixed"] is False
            assert written["daily_liquidity"] is False
            assert f"{written['purchase_date']:%Y-%m-%d}" == "2026-05-02"
            assert f"{written['maturity_date']:%Y-%m-%d}" == "2028-05-02"

    @pytest.mark.asyncio
    async def test_a_prefixed_instrument_is_written_without_an_indexer(self, spy: AssetsSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, AssetFormScreen())
            screen.query_one("#asset-type", Select).value = AssetType.TESOURO
            await pilot.pause()
            screen.query_one("#prefixed", Checkbox).value = True
            await pilot.pause()
            screen.field("ticker").set_value("TESOURO-PRE-2029")
            screen.field("weight").set_value("0.1")
            screen.field("rate").set_value("0.12")
            screen.field("purchase-date").set_value("2026-01-10")
            screen.field("maturity-date").set_value("2029-01-01")
            await pilot.press("ctrl+s")
            await settle(pilot)
            await pilot.press("enter")
            await settle(pilot)
            written = spy.added[-1]
            assert written["is_prefixed"] is True
            assert written["indexer"] is None

    @pytest.mark.asyncio
    async def test_an_invalid_weight_never_reaches_the_service(self, spy: AssetsSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, AssetFormScreen())
            screen.field("ticker").set_value("VALE3")
            screen.field("weight").set_value("1.5")  # fora de (0, 1]
            await pilot.press("ctrl+s")
            await settle(pilot)
            assert not isinstance(app.screen, ConfirmModal)
            assert spy.added == []
            assert screen.field("weight").error == "Peso-alvo deve estar em (0, 1], recebido 1.5."

    @pytest.mark.asyncio
    async def test_the_weight_sum_guard_keeps_the_user_on_the_form(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub_services(monkeypatch)
        spy = AssetsSpy(error=WeightSumExceededError(Decimal("1.2")))
        monkeypatch.setattr(services, "add_asset", spy.add)
        toasts = ToastSpy()
        toasts.install(monkeypatch, AssetFormScreen)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, AssetFormScreen())
            screen.field("ticker").set_value("VALE3")
            screen.field("weight").set_value("0.5")
            await pilot.press("ctrl+s")
            await settle(pilot)
            await pilot.press("enter")
            await settle(pilot)
            assert isinstance(app.screen, AssetFormScreen)  # continua no formulario
            assert screen.field("ticker").value == "VALE3"  # com o que foi digitado
            assert toasts.severity_of("Soma de target_weight") == "error"

    @pytest.mark.asyncio
    async def test_a_from_the_list_opens_the_form_and_the_list_reloads_after(self, spy: AssetsSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, AssetsScreen())
            assert spy.loads == 1
            await pilot.press("a")
            await settle(pilot)
            form = app.screen
            assert isinstance(form, AssetFormScreen)
            form.field("ticker").set_value("VALE3")
            form.field("weight").set_value("0.1")
            await pilot.press("ctrl+s")
            await settle(pilot)
            await pilot.press("enter")
            await settle(pilot)
            assert isinstance(app.screen, AssetsScreen)
            assert spy.loads == 2  # a lista recarregou com o ativo novo


class TestUpdating:
    @pytest.mark.asyncio
    async def test_u_opens_with_the_current_weight_and_writes_the_change(self, spy: AssetsSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, AssetsScreen())
            await pilot.press("u")  # primeira linha: AUVP11, FII, 30%
            await settle(pilot)
            form = app.screen
            assert isinstance(form, AssetUpdateScreen)
            assert form.field("weight").value == "0.3"
            form.field("weight").set_value("0.25")
            await pilot.press("ctrl+s")
            await settle(pilot)
            await pilot.press("enter")
            await settle(pilot)
            assert spy.updated == [{"ticker": "AUVP11", "target_weight": Decimal("0.25")}]

    @pytest.mark.asyncio
    async def test_nothing_changed_is_refused_before_the_database(
        self, spy: AssetsSpy, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Mesma recusa do `bogle update` sem flag nenhuma, so mais cedo: sem
        # modal de confirmacao e sem ida ao banco.
        toasts = ToastSpy()
        toasts.install(monkeypatch, AssetUpdateScreen)
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, AssetsScreen())
            await pilot.press("u")
            await settle(pilot)
            assert isinstance(app.screen, AssetUpdateScreen)
            await pilot.press("ctrl+s")
            await settle(pilot)
            assert not isinstance(app.screen, ConfirmModal)
            assert spy.updated == []
            assert toasts.severity_of("Nada para atualizar") == "warning"

    @pytest.mark.asyncio
    async def test_fixed_income_cannot_change_type_here(self, spy: AssetsSpy) -> None:
        # Trocar o tipo de renda fixa deixaria metadados orfaos: o seletor abre
        # travado, com a explicacao na tela.
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, AssetsScreen())
            screen.query_one(DataTable).move_cursor(row=1)  # CDB-XP-2027
            await pilot.press("u")
            await settle(pilot)
            form = app.screen
            assert isinstance(form, AssetUpdateScreen)
            assert form.query_one("#asset-type", Select).disabled is True
            assert form.switchable is False

    @pytest.mark.asyncio
    async def test_variable_income_can_switch_between_variable_types(self, spy: AssetsSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, AssetsScreen())
            screen.query_one(DataTable).move_cursor(row=2)  # PETR4, STOCK
            await pilot.press("u")
            await settle(pilot)
            form = app.screen
            assert isinstance(form, AssetUpdateScreen)
            form.query_one("#asset-type", Select).value = AssetType.ETF
            await pilot.pause()
            await pilot.press("ctrl+s")
            await settle(pilot)
            modal = app.screen
            assert isinstance(modal, ConfirmModal)
            assert "tipo STOCK -> ETF" in modal.body
            await pilot.press("enter")
            await settle(pilot)
            assert spy.updated == [{"ticker": "PETR4", "asset_type": AssetType.ETF}]


class TestRemoving:
    @pytest.mark.asyncio
    async def test_d_confirms_and_removes(self, spy: AssetsSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, AssetsScreen())
            await pilot.press("d")
            await settle(pilot)
            modal = app.screen
            assert isinstance(modal, ConfirmModal)
            assert modal.dialog_title == "Remover o ativo AUVP11?"
            await pilot.press("enter")
            await settle(pilot)
            assert spy.removed == ["AUVP11"]
            assert [row[0] for row in table_rows(screen)] == ["CDB-XP-2027", "PETR4", "TESOURO-IPCA-2035"]

    @pytest.mark.asyncio
    async def test_cancelling_keeps_the_asset(self, spy: AssetsSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, AssetsScreen())
            await pilot.press("d")
            await settle(pilot)
            await pilot.press("escape")
            await settle(pilot)
            assert spy.removed == []
            assert len(table_rows(screen)) == 4

    @pytest.mark.asyncio
    async def test_an_asset_with_transactions_is_refused_and_the_table_stays(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub_services(monkeypatch)
        spy = AssetsSpy(error=AssetHasTransactionsError("AUVP11"))
        monkeypatch.setattr(services, "list_assets", spy.list)
        monkeypatch.setattr(services, "remove_asset", spy.remove)
        toasts = ToastSpy()
        toasts.install(monkeypatch, AssetsScreen)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, AssetsScreen())
            await pilot.press("d")
            await settle(pilot)
            await pilot.press("enter")
            await settle(pilot)
            assert toasts.severity_of("possui transacoes vinculadas") == "error"
            assert len(table_rows(screen)) == 4

    @pytest.mark.asyncio
    async def test_nothing_selected_is_a_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub_services(monkeypatch)
        monkeypatch.setattr(services, "list_assets", list)
        toasts = ToastSpy()
        toasts.install(monkeypatch, AssetsScreen)
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, AssetsScreen())
            await pilot.press("d")
            await settle(pilot)
            assert not isinstance(app.screen, ConfirmModal)
            assert toasts.severity_of("Nenhum ativo selecionado") == "warning"
