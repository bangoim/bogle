"""Tests for the TUI's Trajetoria screen (issue #79): the table of ``bogle glide
show``, the point form (``set``), removal and clearing, and the mark on the
Position screen. Services are faked: no database, no network.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from rich.text import Text
from textual.widgets import DataTable, Static

from bogle.domain.assets import AssetType
from bogle.domain.errors import GlidePointNotFoundError
from bogle.glide_path import GlideApplication, GlideEdit, TargetChange
from bogle.tui import services
from bogle.tui.screens.glide import (
    INACTIVE,
    NO_BIRTH_YEAR,
    NO_POINTS,
    GlidePathScreen,
    GlidePointScreen,
)
from bogle.tui.screens.home import HomeScreen
from bogle.tui.screens.modals import ConfirmModal, EditModal
from bogle.tui.screens.position import PositionScreen
from bogle.tui.widgets.form import Field
from tests.tui_fakes import (
    GLIDE_POINTS,
    ToastSpy,
    make_app,
    make_glide_state,
    make_position,
    open_screen,
    settle,
    snapshot_of,
    stub_services,
    table_columns,
    table_rows,
)

D = Decimal

APPLIED = GlideApplication(
    year=2026,
    age=40,
    changes=(TargetChange("MUND11", D("0.70"), D("0.6875")),),
)


class GlideSpy:
    """Serves the trajectory and records the writes asked for."""

    def __init__(self, *, edit: GlideEdit | None = None, error: Exception | None = None) -> None:
        self.state = make_glide_state()
        self.saved: list[tuple[int, dict[str, Decimal]]] = []
        self.removed: list[int] = []
        self.cleared = 0
        self.loads = 0
        self.edit = edit or GlideEdit(application=None, active=True, points=3)
        self.error = error

    def load(self, **_: Any) -> services.GlideState:
        self.loads += 1
        return self.state

    def save(self, age: int, weights: dict[str, Decimal], **_: Any) -> GlideEdit:
        if self.error is not None:
            raise self.error
        self.saved.append((age, dict(weights)))
        return self.edit

    def remove(self, age: int, **_: Any) -> GlideEdit:
        if self.error is not None:
            raise self.error
        self.removed.append(age)
        return self.edit

    def clear(self) -> int:
        self.cleared += 1
        return 3


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> GlideSpy:
    stub_services(monkeypatch)
    glide = GlideSpy()
    monkeypatch.setattr(services, "load_glide", glide.load)
    monkeypatch.setattr(services, "save_glide_point", glide.save)
    monkeypatch.setattr(services, "remove_glide_point", glide.remove)
    monkeypatch.setattr(services, "clear_glide_path", glide.clear)
    return glide


async def move_to_age(pilot: Any, screen: GlidePathScreen, age: int) -> None:
    table = screen.query_one(DataTable)
    table.move_cursor(row=table.get_row_index(str(age)))
    await pilot.pause()


class TestTable:
    @pytest.mark.asyncio
    async def test_one_row_per_year_from_now_to_the_last_point(self, spy: GlideSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, GlidePathScreen())
            assert table_columns(screen) == ["Ano", "Idade", "B5P211", "MUND11", "NB1011"]
            rows = table_rows(screen)
            # 1986 + 40 = 2026 (TODAY), ate 1986 + 71 = 2057.
            assert rows[0] == ["2026", "40", "7.00%", "68.75%", "24.25%"]
            assert rows[-1] == ["2057", "71 •", "70.00%", "30.00%", "0.00%"]
            assert len(rows) == 2057 - 2026 + 1
            assert ["2045", "59 •", "45.00%", "45.00%", "10.00%"] in rows

    @pytest.mark.asyncio
    async def test_the_current_year_stands_out(self, spy: GlideSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, GlidePathScreen())
            table = screen.query_one(DataTable)
            first, second = table.get_row_at(0), table.get_row_at(1)
            assert all(isinstance(cell, Text) and cell.style == "bold" for cell in first)
            assert not any(isinstance(cell, Text) and cell.style == "bold" for cell in second)

    @pytest.mark.asyncio
    async def test_the_next_step_is_the_note(self, spy: GlideSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, GlidePathScreen())
            assert screen.note == "01/01/2027: B5P211 7.00% -> 9.00%, MUND11 68.75% -> 67.50%, NB1011 24.25% -> 23.50%"

    @pytest.mark.asyncio
    async def test_past_the_last_point_only_the_current_year(self, spy: GlideSpy) -> None:
        spy.state = make_glide_state(birth_year=1950)  # 76 em 2026
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, GlidePathScreen())
            assert table_rows(screen) == [["2026", "76", "70.00%", "30.00%", "0.00%"]]
            assert screen.note == ""

    @pytest.mark.asyncio
    async def test_without_birth_year_says_where_to_set_it(self, spy: GlideSpy) -> None:
        spy.state = make_glide_state(birth_year=None)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, GlidePathScreen())
            assert table_rows(screen) == []
            assert screen.note == NO_BIRTH_YEAR

    @pytest.mark.asyncio
    async def test_without_points_says_how_to_create_one(self, spy: GlideSpy) -> None:
        spy.state = make_glide_state(points={})
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, GlidePathScreen())
            assert screen.note == NO_POINTS

    @pytest.mark.asyncio
    async def test_a_failure_is_said_beside_the_table(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub_services(monkeypatch)

        def fail(**_: Any) -> Any:
            raise GlidePointNotFoundError(50)

        monkeypatch.setattr(services, "load_glide", fail)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, GlidePathScreen())
            assert screen.note == "A trajetória não tem ponto aos 50 anos."

    @pytest.mark.asyncio
    async def test_seven_opens_it_from_the_home(self, spy: GlideSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await settle(pilot)
            assert isinstance(app.screen, HomeScreen)
            await pilot.press("7")
            await settle(pilot)
            assert isinstance(app.screen, GlidePathScreen)


class TestPointForm:
    @pytest.mark.asyncio
    async def test_a_creates_a_point_and_the_table_reloads(self, spy: GlideSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, GlidePathScreen())
            await pilot.press("a")
            await settle(pilot)
            form = app.screen
            assert isinstance(form, GlidePointScreen)
            assert [field.label for field in form.query(Field)] == ["Idade", *("B5P211", "MUND11", "NB1011", "PETR4")]
            form.field("age").set_value("65")
            form.field("w1").set_value("0,4")  # MUND11, com virgula
            form.field("w0").set_value("0.6")  # B5P211
            await pilot.press("ctrl+s")
            await settle(pilot)
            modal = app.screen
            assert isinstance(modal, ConfirmModal)
            assert modal.body == "65 anos: B5P211 60.00%, MUND11 40.00%"
            await pilot.press("enter")
            await settle(pilot)
            assert spy.saved == [(65, {"B5P211": D("0.6"), "MUND11": D("0.4")})]
            assert isinstance(app.screen, GlidePathScreen)
            assert spy.loads == 2

    @pytest.mark.asyncio
    async def test_the_form_shows_the_sum_as_it_is_typed(self, spy: GlideSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, GlidePathScreen())
            await pilot.press("a")
            await settle(pilot)
            form = app.screen
            assert isinstance(form, GlidePointScreen)
            assert form.field("w0").input.placeholder == "0.00"
            total = form.query_one("#glide-sum", Static)
            assert str(total.render()) == "Soma 0.00"
            form.field("w0").set_value("0,6")
            form.field("w1").set_value("0.35")
            form.field("w2").set_value("abc")  # ainda nao e peso: conta 0
            await settle(pilot)
            assert str(total.render()) == "Soma 0.95"
            assert not total.has_class("complete")
            form.field("w2").set_value("0.05")
            await settle(pilot)
            assert str(total.render()) == "Soma 1.00"
            assert total.has_class("complete")

    @pytest.mark.asyncio
    async def test_u_opens_the_selected_point_as_it_was_saved(self, spy: GlideSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, GlidePathScreen())
            await move_to_age(pilot, screen, 59)
            await pilot.press("u")
            await settle(pilot)
            form = app.screen
            assert isinstance(form, GlidePointScreen)
            assert form.field("age").value == "59"
            assert [form.field(f"w{index}").value for index in range(4)] == ["0.45", "0.45", "0.1", ""]
            form.field("w0").set_value("0.40")
            form.field("w2").set_value("0.15")
            await pilot.press("ctrl+s")
            await settle(pilot)
            modal = app.screen
            assert isinstance(modal, ConfirmModal)
            assert modal.body.endswith("\nSubstitui o ponto atual.")
            await pilot.press("enter")
            await settle(pilot)
            assert spy.saved == [(59, {"B5P211": D("0.40"), "MUND11": D("0.45"), "NB1011": D("0.15")})]

    @pytest.mark.asyncio
    async def test_u_on_a_year_between_points_starts_from_its_targets(self, spy: GlideSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, GlidePathScreen())
            await pilot.press("u")  # primeira linha: 40 anos, entre 39 e 59
            await settle(pilot)
            form = app.screen
            assert isinstance(form, GlidePointScreen)
            assert form.field("age").value == "40"
            assert [form.field(f"w{index}").value for index in range(4)] == ["0.07", "0.6875", "0.2425", ""]

    @pytest.mark.asyncio
    async def test_a_sum_other_than_one_never_reaches_the_service(
        self, spy: GlideSpy, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        toasts = ToastSpy()
        toasts.install(monkeypatch, GlidePointScreen)
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, GlidePathScreen())
            await pilot.press("a")
            await settle(pilot)
            form = app.screen
            assert isinstance(form, GlidePointScreen)
            form.field("age").set_value("65")
            form.field("w1").set_value("0.5")
            form.field("w2").set_value("0.4")
            await pilot.press("ctrl+s")
            await settle(pilot)
            assert app.screen is form
            assert toasts.severity_of("Os pesos somam 0.9; devem somar exatamente 1.") == "error"
            assert spy.saved == []

    @pytest.mark.asyncio
    async def test_an_age_out_of_range_is_called_out_on_the_field(self, spy: GlideSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, GlidePathScreen())
            await pilot.press("a")
            await settle(pilot)
            form = app.screen
            assert isinstance(form, GlidePointScreen)
            form.field("age").set_value("121")
            form.field("w1").set_value("1")
            await pilot.press("ctrl+s")
            await settle(pilot)
            assert form.field("age").error == "Idade deve estar entre 0 e 120, recebido 121."
            assert spy.saved == []

    @pytest.mark.asyncio
    async def test_a_weight_above_one_is_called_out_on_its_field(self, spy: GlideSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, GlidePathScreen())
            await pilot.press("a")
            await settle(pilot)
            form = app.screen
            assert isinstance(form, GlidePointScreen)
            form.field("w1").set_value("1.5")
            await pilot.pause()
            assert form.field("w1").error == "MUND11 deve estar em [0, 1], recebido 1.5."

    @pytest.mark.asyncio
    async def test_a_point_that_moves_this_year_says_what_it_applied(
        self, spy: GlideSpy, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spy.edit = GlideEdit(application=APPLIED, active=True, points=3)
        toasts = ToastSpy()
        toasts.install(monkeypatch, GlidePointScreen)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, GlidePathScreen())
            await move_to_age(pilot, screen, 59)
            await pilot.press("u")
            await settle(pilot)
            await pilot.press("ctrl+s")
            await settle(pilot)
            await pilot.press("enter")
            await settle(pilot)
            assert toasts.messages == [
                "ponto gravado: 59 anos: B5P211 45.00%, MUND11 45.00%, NB1011 10.00%.",
                "alvo de 2026 aplicado pela trajetória: MUND11 70.00% -> 68.75%.",
            ]

    @pytest.mark.asyncio
    async def test_a_point_without_birth_year_says_the_trajectory_is_inactive(
        self, spy: GlideSpy, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spy.state = make_glide_state(birth_year=None, points={})
        spy.edit = GlideEdit(application=None, active=False, points=1)
        toasts = ToastSpy()
        toasts.install(monkeypatch, GlidePointScreen)
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, GlidePathScreen())
            await pilot.press("a")
            await settle(pilot)
            form = app.screen
            assert isinstance(form, GlidePointScreen)
            form.field("age").set_value("39")
            form.field("w1").set_value("1")
            await pilot.press("ctrl+s")
            await settle(pilot)
            await pilot.press("enter")
            await settle(pilot)
            assert toasts.severity_of(INACTIVE) == "warning"

    @pytest.mark.asyncio
    async def test_a_refused_write_keeps_the_user_on_the_form(
        self, spy: GlideSpy, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spy.error = GlidePointNotFoundError(65)  # qualquer BogleError do servico
        toasts = ToastSpy()
        toasts.install(monkeypatch, GlidePointScreen)
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, GlidePathScreen())
            await pilot.press("a")
            await settle(pilot)
            form = app.screen
            assert isinstance(form, GlidePointScreen)
            form.field("age").set_value("65")
            form.field("w1").set_value("1")
            await pilot.press("ctrl+s")
            await settle(pilot)
            await pilot.press("enter")
            await settle(pilot)
            assert app.screen is form
            assert form.field("age").value == "65"
            assert toasts.severity_of("não tem ponto aos 65 anos") == "error"


class TestRemoval:
    @pytest.mark.asyncio
    async def test_d_on_a_point_confirms_and_removes_it(self, spy: GlideSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, GlidePathScreen())
            await move_to_age(pilot, screen, 59)
            await pilot.press("d")
            await settle(pilot)
            modal = app.screen
            assert isinstance(modal, ConfirmModal)
            assert modal.dialog_title == "Remover o ponto dos 59 anos?"
            await pilot.press("enter")
            await settle(pilot)
            assert spy.removed == [59]
            assert spy.loads == 2

    @pytest.mark.asyncio
    async def test_escape_removes_nothing(self, spy: GlideSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, GlidePathScreen())
            await move_to_age(pilot, screen, 59)
            await pilot.press("d")
            await settle(pilot)
            await pilot.press("escape")
            await settle(pilot)
            assert spy.removed == []

    @pytest.mark.asyncio
    async def test_d_off_a_point_asks_for_the_age(self, spy: GlideSpy) -> None:
        # Um ponto antes da idade atual nao tem linha na tabela.
        spy.state = make_glide_state(points={30: {"MUND11": D("1")}, **GLIDE_POINTS})
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, GlidePathScreen())
            await pilot.press("d")  # 40 anos: nao e ponto
            await settle(pilot)
            modal = app.screen
            assert isinstance(modal, EditModal)
            assert modal.body == "Pontos: 30, 39, 59, 71."
            await pilot.press("3", "0", "enter")
            await settle(pilot)
            confirm = app.screen
            assert isinstance(confirm, ConfirmModal)
            assert confirm.body == "30 anos: MUND11 100.00%"
            await pilot.press("enter")
            await settle(pilot)
            assert spy.removed == [30]

    @pytest.mark.asyncio
    async def test_an_age_that_is_not_a_point_is_refused(self, spy: GlideSpy, monkeypatch: pytest.MonkeyPatch) -> None:
        toasts = ToastSpy()
        toasts.install(monkeypatch, GlidePathScreen)
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, GlidePathScreen())
            await pilot.press("d")
            await settle(pilot)
            await pilot.press("4", "5", "enter")
            await settle(pilot)
            assert isinstance(app.screen, GlidePathScreen)
            assert toasts.severity_of("A trajetória não tem ponto aos 45 anos.") == "error"
            assert spy.removed == []

    @pytest.mark.asyncio
    async def test_removing_the_last_point_says_the_targets_stay(
        self, spy: GlideSpy, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spy.edit = GlideEdit(application=None, active=False, points=0)
        toasts = ToastSpy()
        toasts.install(monkeypatch, GlidePathScreen)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, GlidePathScreen())
            await move_to_age(pilot, screen, 71)
            await pilot.press("d")
            await settle(pilot)
            await pilot.press("enter")
            await settle(pilot)
            assert toasts.messages == ["ponto dos 71 anos removido.", "Trajetória vazia: os alvos ficam como estão."]

    @pytest.mark.asyncio
    async def test_a_removal_that_moves_this_year_says_what_it_applied(
        self, spy: GlideSpy, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spy.edit = GlideEdit(application=APPLIED, active=True, points=2)
        toasts = ToastSpy()
        toasts.install(monkeypatch, GlidePathScreen)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, GlidePathScreen())
            await move_to_age(pilot, screen, 59)
            await pilot.press("d")
            await settle(pilot)
            await pilot.press("enter")
            await settle(pilot)
            assert "alvo de 2026 aplicado pela trajetória: MUND11 70.00% -> 68.75%." in toasts.messages


class TestClear:
    @pytest.mark.asyncio
    async def test_l_confirms_and_clears(self, spy: GlideSpy, monkeypatch: pytest.MonkeyPatch) -> None:
        toasts = ToastSpy()
        toasts.install(monkeypatch, GlidePathScreen)
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, GlidePathScreen())
            await pilot.press("l")
            await settle(pilot)
            modal = app.screen
            assert isinstance(modal, ConfirmModal)
            assert modal.dialog_title == "Apagar 3 pontos da trajetória?"
            await pilot.press("enter")
            await settle(pilot)
            assert spy.cleared == 1
            assert toasts.messages == ["trajetória apagada (3 pontos); os alvos ficam como estão."]

    @pytest.mark.asyncio
    async def test_escape_clears_nothing(self, spy: GlideSpy) -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, GlidePathScreen())
            await pilot.press("l")
            await settle(pilot)
            await pilot.press("escape")
            await settle(pilot)
            assert spy.cleared == 0

    @pytest.mark.asyncio
    async def test_nothing_to_clear_asks_nothing(self, spy: GlideSpy) -> None:
        spy.state = make_glide_state(points={})
        app = make_app()
        async with app.run_test() as pilot:
            await open_screen(pilot, GlidePathScreen())
            await pilot.press("l")
            await settle(pilot)
            assert isinstance(app.screen, GlidePathScreen)
            assert spy.cleared == 0


class TestPositionMark:
    @pytest.mark.asyncio
    async def test_targets_of_the_trajectory_carry_the_mark(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub_services(monkeypatch)
        snapshot = snapshot_of(
            make_position("MUND11", AssetType.ETF, target_weight=D("0.6875")),
            make_position("PETR4", AssetType.STOCK, target_weight=D("0.1")),
            glide_tickers=frozenset({"MUND11"}),
        )
        monkeypatch.setattr(services, "load_snapshot", lambda **_: snapshot)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, PositionScreen())
            target = table_columns(screen).index("Target")
            assert [row[target] for row in table_rows(screen)] == ["68.75% ↘", "10.00%  "]

    @pytest.mark.asyncio
    async def test_without_a_trajectory_the_targets_are_as_they_were(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub_services(monkeypatch)
        app = make_app()
        async with app.run_test() as pilot:
            screen = await open_screen(pilot, PositionScreen())
            target = table_columns(screen).index("Target")
            assert [row[target] for row in table_rows(screen)] == ["50.00%", "10.00%"]
