"""Tests for the TUI's Transactions screen (issue #74): listing, the ticker
filter and removal with confirmation.
"""

from __future__ import annotations

from typing import Any

import pytest
from rich.text import Text
from textual.widgets import DataTable, Input

from bogle.domain.errors import TransactionNotFoundError
from bogle.tui import services
from bogle.tui.screens.modals import ConfirmModal
from bogle.tui.screens.transactions import TransactionsScreen
from tests.tui_fakes import ToastSpy, make_app, make_ledger, settle, stub_services


class LedgerSpy:
    """Serves a ledger and records the deletions asked for."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.transactions = make_ledger()
        self.deleted: list[int] = []
        self.error = error
        self.loads = 0

    def load(self) -> Any:
        self.loads += 1
        return list(self.transactions)

    def delete(self, transaction_id: int) -> None:
        if self.error is not None:
            raise self.error
        self.deleted.append(transaction_id)
        self.transactions = [t for t in self.transactions if t.id != transaction_id]


@pytest.fixture
def ledger(monkeypatch: pytest.MonkeyPatch) -> LedgerSpy:
    stub_services(monkeypatch)
    spy = LedgerSpy()
    monkeypatch.setattr(services, "load_transactions", spy.load)
    monkeypatch.setattr(services, "delete_transaction", spy.delete)
    return spy


async def open_ledger(pilot: Any) -> TransactionsScreen:
    await pilot.app.push_screen(TransactionsScreen())
    await settle(pilot)
    screen = pilot.app.screen
    assert isinstance(screen, TransactionsScreen)
    return screen


def rows(screen: TransactionsScreen) -> list[list[str]]:
    table = screen.query_one(DataTable)
    return [
        [cell.plain if isinstance(cell, Text) else str(cell) for cell in table.get_row_at(index)]
        for index in range(table.row_count)
    ]


class TestListing:
    @pytest.mark.asyncio
    async def test_columns_and_rows_match_the_cli(self, ledger: LedgerSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_ledger(pilot)
            table = screen.query_one(DataTable)
            assert [str(column.label) for column in table.columns.values()] == [
                "ID",
                "Data",
                "Tipo",
                "Ticker",
                "Qtd",
                "Preco",
                "Valor",
                "Fees",
                "IR",
            ]
            assert rows(screen)[0] == ["1", "2026-03-10", "BUY", "AUVP11", "3", "126.25", "378.75", "0.13", "0"]

    @pytest.mark.asyncio
    async def test_income_rows_have_no_quantity_or_price(self, ledger: LedgerSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_ledger(pilot)
            dividend = next(row for row in rows(screen) if row[2] == "DIVIDEND")
            assert dividend[4] == "-"
            assert dividend[5] == "-"
            assert dividend[6] == "45.5"  # normalizado, como na tabela da CLI

    @pytest.mark.asyncio
    async def test_counts_the_rows_in_the_note(self, ledger: LedgerSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_ledger(pilot)
            assert screen.note == "3 transacoes"

    @pytest.mark.asyncio
    async def test_empty_ledger_says_so(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub_services(monkeypatch)
        monkeypatch.setattr(services, "load_transactions", list)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_ledger(pilot)
            assert screen.query_one(DataTable).row_count == 0
            assert screen.note == "Nenhuma transacao registrada."

    @pytest.mark.asyncio
    async def test_r_reloads(self, ledger: LedgerSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await open_ledger(pilot)
            assert ledger.loads == 1
            await pilot.press("r")
            await settle(pilot)
            assert ledger.loads == 2


class TestFilter:
    @pytest.mark.asyncio
    async def test_f_focuses_the_filter_without_typing_into_it(self, ledger: LedgerSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_ledger(pilot)
            await pilot.press("f")
            await pilot.pause()
            filter_input = screen.query_one("#filter", Input)
            assert app.focused is filter_input
            assert filter_input.value == ""

    @pytest.mark.asyncio
    async def test_filtering_matches_part_of_the_ticker(self, ledger: LedgerSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_ledger(pilot)
            await pilot.press("f")
            await pilot.press("a", "u", "v")
            await pilot.pause()
            assert [row[3] for row in rows(screen)] == ["AUVP11", "AUVP11"]
            assert screen.note == "2 de 3 transacoes (filtro 'AUV')"

    @pytest.mark.asyncio
    async def test_filter_text_is_not_read_as_markup(self, ledger: LedgerSpy) -> None:
        # O texto do filtro entra na nota, que e montada com markup: "[/]" sem
        # escape e lido como tag de fechamento e estoura a atualizacao.
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_ledger(pilot)
            screen.query_one("#filter", Input).value = "[/]"
            await pilot.pause()
            assert screen.note == "Nenhuma transacao para '[/]'."

    @pytest.mark.asyncio
    async def test_filter_with_no_match(self, ledger: LedgerSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_ledger(pilot)
            screen.query_one("#filter", Input).value = "ZZZ"
            await pilot.pause()
            assert rows(screen) == []
            assert screen.note == "Nenhuma transacao para 'ZZZ'."


class TestRemoval:
    @pytest.mark.asyncio
    async def test_d_confirms_and_removes_the_selected_row(self, ledger: LedgerSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_ledger(pilot)
            await pilot.press("down")  # segunda linha: o dividendo, id 2
            await pilot.press("d")
            await settle(pilot)
            modal = app.screen
            assert isinstance(modal, ConfirmModal)
            assert modal.dialog_title == "Remover a transacao 2?"
            assert modal.body == "DIVIDEND PETR4 em 2026-05-15, valor 45.50"
            await pilot.press("enter")
            await settle(pilot)
            assert ledger.deleted == [2]
            assert [row[0] for row in rows(screen)] == ["1", "3"]  # recarregou sem a linha

    @pytest.mark.asyncio
    async def test_cancelling_keeps_the_row(self, ledger: LedgerSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_ledger(pilot)
            await pilot.press("d")
            await settle(pilot)
            await pilot.press("escape")
            await settle(pilot)
            assert ledger.deleted == []
            assert len(rows(screen)) == 3

    @pytest.mark.asyncio
    async def test_removal_respects_the_filter(self, ledger: LedgerSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_ledger(pilot)
            screen.query_one("#filter", Input).value = "AUVP"
            await pilot.pause()
            screen.query_one(DataTable).focus()
            await pilot.press("down")  # segunda linha *filtrada*: a venda, id 3
            await pilot.press("d")
            await settle(pilot)
            modal = app.screen
            assert isinstance(modal, ConfirmModal)
            assert modal.dialog_title == "Remover a transacao 3?"

    @pytest.mark.asyncio
    async def test_nothing_selected_is_a_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub_services(monkeypatch)
        monkeypatch.setattr(services, "load_transactions", list)
        toasts = ToastSpy()
        toasts.install(monkeypatch, TransactionsScreen)
        app = make_app()
        async with app.run_test() as pilot:
            await open_ledger(pilot)
            await pilot.press("d")
            await settle(pilot)
            assert not isinstance(app.screen, ConfirmModal)
            assert toasts.severity_of("Nenhuma transacao selecionada") == "warning"

    @pytest.mark.asyncio
    async def test_failed_removal_is_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub_services(monkeypatch)
        spy = LedgerSpy(error=TransactionNotFoundError(1))
        monkeypatch.setattr(services, "load_transactions", spy.load)
        monkeypatch.setattr(services, "delete_transaction", spy.delete)
        toasts = ToastSpy()
        toasts.install(monkeypatch, TransactionsScreen)
        app = make_app()
        async with app.run_test() as pilot:
            await open_ledger(pilot)
            await pilot.press("d")
            await settle(pilot)
            await pilot.press("enter")
            await settle(pilot)
            assert toasts.severity_of("Transacao 1 nao encontrada") == "error"
            # A falha e da remocao, nao da carga: as linhas continuam na tela.
            screen = app.screen
            assert isinstance(screen, TransactionsScreen)
            assert len(screen.shown) == 3
            assert screen.query_one(DataTable).row_count == 3
