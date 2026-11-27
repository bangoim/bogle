"""Tests for ``bogle glide`` (issue #79): editing the points, the table year by
year, the targets written again when an edit moves this year's, and the mark on
the Position views.

The trajectory of the issue (39/59/71); "today" is the real date, as the command
reads it, so the year of birth is chosen relative to it.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from psycopg.rows import DictRow
from rich.console import Console
from typer.testing import CliRunner

from bogle import format as fmt
from bogle import glide_path as glide_mod
from bogle.cli import app
from bogle.cli.glide import EMPTIED, INACTIVE, NO_BIRTH_YEAR, NO_POINTS
from bogle.cli.position import _render
from bogle.domain.errors import AssetNotFoundError, GlidePointNotFoundError, ValidationError
from bogle.glide_path import (
    GLIDE_MARK,
    ActiveGlidePath,
    GlideStep,
    TargetChange,
    active_glide_path,
    check_manual_target,
    check_point,
    clear_glide_path,
    glide_schedule,
    load_glide_view,
    marked_target,
    remove_glide_point,
    set_glide_point,
    step_notice,
)
from bogle.reports.snapshot import compute_snapshot
from bogle.repositories.assets import AssetRepository
from bogle.repositories.glide_path import GlidePathRepository
from bogle.repositories.transactions import TransactionRepository
from bogle.settings import BIRTH_YEAR, GLIDE_PATH_APPLIED_YEAR, get_setting, set_value
from bogle.tui import services
from tests.test_cli import run_cli
from tests.test_glide_path import CONTEXT, seed, weights

D = Decimal
TODAY = date.today()
FORTY = TODAY.year - 40
"""Born 40 years ago: between the anchors 39 and 59, so every year is a step."""
TWENTY_SIX = TODAY.year - 26
"""Born 26 years ago: before the first anchor, so this year is the anchor of 39."""


@pytest.fixture
def clean(conn: psycopg.Connection[DictRow]) -> Iterator[psycopg.Connection[DictRow]]:
    """``conn``, and the trajectory gone after the test as well as before (see test_glide_path)."""
    yield conn
    conn.execute("TRUNCATE glide_path_points, user_settings")


def points(conn: psycopg.Connection[DictRow]) -> dict[int, dict[str, Decimal]]:
    return GlidePathRepository(conn).points()


def invoke(*args: str, stdin: str | None = None) -> Any:
    return CliRunner().invoke(app, ["glide", *args], input=stdin)


# ---------------------------------------------------------------- regras do ponto


class TestCheckPoint:
    @pytest.mark.parametrize("age", [-1, 121])
    def test_an_age_out_of_range_is_refused(self, age: int) -> None:
        with pytest.raises(ValidationError, match=rf"Idade deve estar entre 0 e 120, recebido {age}\."):
            check_point(age, {"A": D("1")})

    @pytest.mark.parametrize("age", [0, 120])
    def test_the_ends_of_the_range_are_accepted(self, age: int) -> None:
        assert check_point(age, {"A": D("1")}) == {"A": D("1")}

    def test_a_sum_other_than_one_is_refused(self) -> None:
        with pytest.raises(ValidationError, match=r"Os pesos somam 0\.95; devem somar exatamente 1\."):
            check_point(39, {"A": D("0.70"), "B": D("0.25")})

    def test_the_sum_is_exact(self) -> None:
        with pytest.raises(ValidationError, match=r"somam 1\.0001"):
            check_point(39, {"A": D("0.7001"), "B": D("0.30")})

    @pytest.mark.parametrize("weight", ["1.5", "-0.5"])
    def test_a_weight_out_of_zero_one_is_refused(self, weight: str) -> None:
        with pytest.raises(ValidationError, match=rf"Peso de A deve estar em \[0, 1\], recebido {weight}\."):
            check_point(39, {"A": D(weight), "B": D("0")})

    def test_more_than_four_places_is_refused(self) -> None:
        with pytest.raises(ValidationError, match=r"Peso de A aceita até 4 casas decimais, recebido 0\.12345\."):
            check_point(39, {"A": D("0.12345"), "B": D("0.87655")})

    def test_trailing_zeros_are_not_places(self) -> None:
        assert check_point(39, {"A": D("0.700000"), "B": D("0.3")}) == {"A": D("0.700000"), "B": D("0.3")}

    def test_a_ticker_at_zero_is_not_stored(self) -> None:
        assert check_point(71, {"A": D("1"), "B": D("0")}) == {"A": D("1")}

    def test_no_weight_at_all_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="ao menos um ticker"):
            check_point(39, {})


# ---------------------------------------------------------------- edicao


class TestSetPoint:
    def test_an_unknown_ticker_is_refused(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, points={})
        with pytest.raises(AssetNotFoundError, match=r"Ativo 'XPTO' não encontrado\."):
            set_glide_point(clean, 39, {"MUND11": D("0.5"), "XPTO": D("0.5")}, TODAY)
        with pytest.raises(ValidationError, match=r"Ativos não encontrados: XPTO, YYYY\."):
            set_glide_point(clean, 39, {"YYYY": D("0.5"), "XPTO": D("0.5")}, TODAY)
        assert points(clean) == {}

    def test_replaces_the_whole_point(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean)
        set_glide_point(clean, 59, {"MUND11": D("0.5"), "B5P211": D("0.5")}, TODAY)
        assert points(clean)[59] == {"B5P211": D("0.5"), "MUND11": D("0.5")}  # NB1011 saiu

    def test_the_first_point_with_birth_year_applies_on_the_spot(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, weights=("0.60", "0.30", "0.10"), points={}, birth_year=TWENTY_SIX)
        edit = set_glide_point(clean, 39, CONTEXT[39], TODAY)
        assert edit.active
        assert edit.application is not None
        assert edit.application.year == TODAY.year
        assert weights(clean) == {"B5P211": D("0.05"), "MUND11": D("0.70"), "NB1011": D("0.25")}
        assert get_setting(clean, GLIDE_PATH_APPLIED_YEAR) == TODAY.year

    def test_without_birth_year_nothing_is_applied(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, weights=("0.60", "0.30", "0.10"), points={}, birth_year=None)
        edit = set_glide_point(clean, 39, CONTEXT[39], TODAY)
        assert (edit.active, edit.application, edit.points) == (False, None, 1)
        assert weights(clean)["MUND11"] == D("0.60")

    def test_a_point_that_moves_this_year_applies_it_again(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, birth_year=TWENTY_SIX)
        set_value(clean, GLIDE_PATH_APPLIED_YEAR, TODAY.year)
        edit = set_glide_point(clean, 39, {"MUND11": D("0.80"), "NB1011": D("0.20")}, TODAY)
        assert edit.application is not None
        assert edit.application.changes == (
            TargetChange("B5P211", D("0.05"), D("0")),
            TargetChange("MUND11", D("0.70"), D("0.80")),
            TargetChange("NB1011", D("0.25"), D("0.20")),
        )
        assert weights(clean) == {"B5P211": D("0"), "MUND11": D("0.80"), "NB1011": D("0.20")}

    def test_a_point_that_does_not_move_this_year_writes_only_the_point(
        self, clean: psycopg.Connection[DictRow]
    ) -> None:
        # Aos 26, antes da primeira ancora: o ponto dos 59 so pesa daqui a anos.
        seed(clean, birth_year=TWENTY_SIX)
        set_value(clean, GLIDE_PATH_APPLIED_YEAR, TODAY.year)
        AssetRepository(clean).update_weight("MUND11", D("0.5"))  # prova que nada reescreve
        edit = set_glide_point(clean, 59, {"MUND11": D("0.5"), "B5P211": D("0.5")}, TODAY)
        assert edit.application is None
        assert weights(clean)["MUND11"] == D("0.5")

    def test_a_ticker_that_leaves_the_trajectory_goes_to_zero(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, birth_year=TWENTY_SIX, points={39: CONTEXT[39]})
        set_value(clean, GLIDE_PATH_APPLIED_YEAR, TODAY.year)
        set_glide_point(clean, 39, {"MUND11": D("0.75"), "B5P211": D("0.25")}, TODAY)
        assert weights(clean) == {"B5P211": D("0.25"), "MUND11": D("0.75"), "NB1011": D("0")}

    def test_a_failed_application_takes_the_point_down_with_it(
        self, clean: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seed(clean, birth_year=TWENTY_SIX)

        def explode(*_args: Any) -> None:
            raise RuntimeError("falhou no meio")

        monkeypatch.setattr(glide_mod, "set_value", explode)
        with pytest.raises(RuntimeError):
            set_glide_point(clean, 39, {"MUND11": D("1")}, TODAY)
        assert points(clean)[39] == CONTEXT[39]
        assert weights(clean)["MUND11"] == D("0.70")


class TestRemovePoint:
    def test_a_point_that_does_not_exist_is_refused(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean)
        with pytest.raises(GlidePointNotFoundError, match=r"A trajetória não tem ponto aos 50 anos\."):
            remove_glide_point(clean, 50, TODAY)

    def test_a_removal_that_moves_this_year_applies_it_again(self, clean: psycopg.Connection[DictRow]) -> None:
        # Aos 40, entre 39 e 59: sem o 59, a linha vai do 39 ao 71.
        seed(clean, weights=("0.6875", "0.2425", "0.07"), birth_year=FORTY)
        set_value(clean, GLIDE_PATH_APPLIED_YEAR, TODAY.year)
        edit = remove_glide_point(clean, 59, TODAY)
        assert edit.application is not None
        assert edit.points == 2
        assert weights(clean) == {"B5P211": D("0.0703"), "MUND11": D("0.6875"), "NB1011": D("0.2422")}

    def test_removing_the_last_point_keeps_the_targets(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, birth_year=TWENTY_SIX, points={39: CONTEXT[39]})
        edit = remove_glide_point(clean, 39, TODAY)
        assert (edit.active, edit.application, edit.points) == (False, None, 0)
        assert weights(clean) == {"B5P211": D("0.05"), "MUND11": D("0.70"), "NB1011": D("0.25")}
        assert active_glide_path(clean) is None


class TestClear:
    def test_keeps_the_targets_and_frees_the_manual_edit(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, birth_year=TWENTY_SIX)
        assert clear_glide_path(clean) == 3
        assert points(clean) == {}
        assert weights(clean) == {"B5P211": D("0.05"), "MUND11": D("0.70"), "NB1011": D("0.25")}
        check_manual_target(clean, "MUND11", D("0.5"), TODAY)  # nao recusa mais

    def test_an_empty_trajectory_clears_nothing(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, points={})
        assert clear_glide_path(clean) == 0


# ---------------------------------------------------------------- tabela


GLIDE = ActiveGlidePath(points=CONTEXT, birth_year=2000)


class TestSchedule:
    def test_one_row_per_year_from_now_to_the_last_point(self) -> None:
        schedule = glide_schedule(GLIDE, 2026)
        assert schedule.tickers == ("B5P211", "MUND11", "NB1011")
        assert [row.year for row in schedule.years] == list(range(2026, 2072))
        assert (schedule.years[0].age, schedule.years[-1].age) == (26, 71)
        assert [row.age for row in schedule.years if row.anchor] == [39, 59, 71]

    def test_the_next_step_is_the_first_year_that_changes_something(self) -> None:
        # Aos 26 nada muda ate os 40: o degrau e 01/01/2040.
        schedule = glide_schedule(GLIDE, 2026)
        assert schedule.next_step == GlideStep(
            year=2040,
            changes=(
                TargetChange("B5P211", D("0.05"), D("0.07")),
                TargetChange("MUND11", D("0.70"), D("0.6875")),
                TargetChange("NB1011", D("0.25"), D("0.2425")),
            ),
        )

    def test_only_the_tickers_that_change(self) -> None:
        points = {40: {"A": D("0.5"), "B": D("0.5")}, 41: {"A": D("0.4"), "B": D("0.5"), "C": D("0.1")}}
        schedule = glide_schedule(ActiveGlidePath(points=points, birth_year=2000), 2040)
        assert schedule.next_step is not None
        assert [change.ticker for change in schedule.next_step.changes] == ["A", "C"]

    def test_past_the_last_point_only_the_current_year_and_no_step(self) -> None:
        schedule = glide_schedule(GLIDE, 2080)
        assert [(row.year, row.age, row.anchor) for row in schedule.years] == [(2080, 80, False)]
        assert schedule.next_step is None

    def test_on_the_last_point_itself_one_row(self) -> None:
        schedule = glide_schedule(GLIDE, 2071)
        assert [(row.year, row.anchor) for row in schedule.years] == [(2071, True)]

    def test_the_footer_of_the_issue(self) -> None:
        fmt.configure(",")
        step = glide_schedule(GLIDE, 2039).next_step
        assert step is not None
        assert step_notice(step) == (
            "01/01/2040: B5P211 5,00% -> 7,00%, MUND11 70,00% -> 68,75%, NB1011 25,00% -> 24,25%"
        )

    def test_the_view_without_birth_year_has_no_schedule(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, birth_year=None)
        view = load_glide_view(clean, TODAY)
        assert (view.birth_year, view.schedule) == (None, None)
        assert view.points == CONTEXT


# ---------------------------------------------------------------- posicao


class TestPositionMark:
    def test_the_mark_and_the_padding(self) -> None:
        tickers = frozenset({"MUND11"})
        assert marked_target(D("0.7"), "MUND11", tickers) == f"70.00% {GLIDE_MARK}"
        assert marked_target(D("0.1"), "PETR4", tickers) == "10.00%  "
        assert marked_target(D("0.1"), "PETR4", frozenset()) == "10.00%"

    def test_the_snapshot_knows_the_tickers_of_the_trajectory(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, birth_year=TWENTY_SIX, points={39: {"MUND11": D("0.75"), "NB1011": D("0.25")}})
        assert compute_snapshot(clean, None, today=TODAY).glide_tickers == {"MUND11", "NB1011"}

    def test_without_birth_year_no_ticker_is_marked(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, birth_year=None)
        assert compute_snapshot(clean, None, today=TODAY).glide_tickers == frozenset()

    def test_the_command_marks_them_too(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, birth_year=TWENTY_SIX, extra={"PETR4": "0"})
        trades = TransactionRepository(clean)
        trades.add_buy("MUND11", datetime(2026, 2, 1, 12, tzinfo=UTC), D("10"), D("100"))
        trades.add_buy("PETR4", datetime(2026, 2, 1, 12, tzinfo=UTC), D("10"), D("30"))
        snapshot = compute_snapshot(clean, None, today=TODAY)
        buffer = io.StringIO()
        _render(snapshot.summary, Console(file=buffer, width=200), glide_tickers=snapshot.glide_tickers)
        rows = {line.split("│")[1].strip(): line for line in buffer.getvalue().splitlines() if line.startswith("│")}
        assert f"70.00% {GLIDE_MARK}" in rows["MUND11"]
        assert GLIDE_MARK not in rows["PETR4"]


# ---------------------------------------------------------------- comando


class TestShowCommand:
    def test_without_birth_year_explains_how_to_set_it(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, birth_year=None)
        result = invoke("show")
        assert result.exit_code == 0
        assert NO_BIRTH_YEAR in result.output
        assert "bogle config set birth_year <ano>" in result.output
        assert "Ano" not in result.output

    def test_without_points_says_how_to_create_one(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, points={}, birth_year=FORTY)
        result = invoke("show")
        assert result.exit_code == 0
        assert result.output.strip() == NO_POINTS

    def test_the_table_year_by_year(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, birth_year=FORTY)
        result = invoke("show")
        assert result.exit_code == 0, result.output
        lines = result.output.splitlines()
        header = next(line for line in lines if "Ano" in line)
        assert [cell.strip() for cell in header.strip("┃ ").split("┃")] == [
            "Ano",
            "Idade",
            "B5P211",
            "MUND11",
            "NB1011",
        ]
        rows = [line for line in lines if line.startswith("│")]
        assert len(rows) == 71 - 40 + 1
        assert rows[0].split("│")[1:3] == [f" {TODAY.year} ", " 40    "]
        assert "59 •" in rows[59 - 40]
        assert "71 •" in rows[-1]
        assert "39" not in "".join(row.split("│")[2] for row in rows)  # o ponto dos 39 ja passou
        footer = result.output.strip().splitlines()[-1]
        assert footer == (
            f"01/01/{TODAY.year + 1}: B5P211 7,00% -> 9,00%, MUND11 68,75% -> 67,50%, NB1011 24,25% -> 23,50%"
        )


class TestSetCommand:
    def test_creates_the_point_and_announces_the_application(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, weights=("0.60", "0.30", "0.10"), points={}, birth_year=TWENTY_SIX)
        result = invoke("set", "39", "MUND11=0,70", "nb1011=0.25", "B5P211=0.05")
        assert result.exit_code == 0, result.output
        assert "ponto gravado: 39 anos: B5P211 5,00%, MUND11 70,00%, NB1011 25,00%." in result.output
        assert (
            f"aviso: alvos de {TODAY.year} aplicados pela trajetória: "
            "B5P211 10,00% -> 5,00%, MUND11 60,00% -> 70,00%, NB1011 30,00% -> 25,00%." in result.output
        )
        assert points(clean) == {39: CONTEXT[39]}

    def test_an_omitted_ticker_is_zero(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean)
        result = invoke("set", "71", "MUND11=0.30", "B5P211=0.70")
        assert result.exit_code == 0, result.output
        assert points(clean)[71] == {"B5P211": D("0.70"), "MUND11": D("0.30")}

    def test_without_birth_year_says_it_is_inactive(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, points={}, birth_year=None)
        result = invoke("set", "39", "MUND11=1")
        assert result.exit_code == 0, result.output
        assert f"aviso: {INACTIVE}" in result.output
        assert weights(clean)["MUND11"] == D("0.70")

    @pytest.mark.parametrize(
        ("args", "message"),
        [
            (("39", "MUND11=0.70", "NB1011=0.25"), "Os pesos somam 0,95; devem somar exatamente 1."),
            (("39", "XPTO=1"), "Ativo 'XPTO' não encontrado."),
            (("121", "MUND11=1"), "Idade deve estar entre 0 e 120, recebido 121."),
            (("39", "MUND11=1.5"), "Peso de MUND11 deve estar em [0, 1], recebido 1.5."),
            (("39", "MUND11=0.5", "mund11=0.5"), "Peso repetido para MUND11: informe um só por ticker."),
            (("39", "MUND11"), "Peso espera TICKER=PESO (ex: MUND11=0.70), recebido 'MUND11'."),
        ],
    )
    def test_refuses_in_portuguese_and_writes_nothing(
        self, clean: psycopg.Connection[DictRow], args: tuple[str, ...], message: str
    ) -> None:
        seed(clean, points={})
        result = invoke("set", *args)
        assert result.exit_code == 1
        assert str(result.exception) == message
        assert points(clean) == {}

    def test_the_refusal_reaches_stderr(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, points={})
        result = run_cli("glide", "set", "39", "MUND11=0.70", "NB1011=0.20")
        assert result.returncode == 1
        assert result.stderr.strip() == "erro: Os pesos somam 0,9; devem somar exatamente 1."


class TestRemoveCommand:
    def test_removes_and_announces_the_application(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, weights=("0.6875", "0.2425", "0.07"), birth_year=FORTY)
        set_value(clean, GLIDE_PATH_APPLIED_YEAR, TODAY.year)
        result = invoke("remove", "59")
        assert result.exit_code == 0, result.output
        assert "ponto dos 59 anos removido." in result.output
        assert f"aviso: alvos de {TODAY.year} aplicados pela trajetória" in result.output
        assert 59 not in points(clean)

    def test_the_last_point_says_the_targets_stay(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, birth_year=TWENTY_SIX, points={39: CONTEXT[39]})
        result = invoke("remove", "39")
        assert result.exit_code == 0, result.output
        assert EMPTIED in result.output
        assert weights(clean)["MUND11"] == D("0.70")

    def test_an_age_without_a_point_is_refused(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean)
        result = invoke("remove", "50")
        assert result.exit_code == 1
        assert str(result.exception) == "A trajetória não tem ponto aos 50 anos."


class TestClearCommand:
    def test_asks_and_a_no_changes_nothing(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, birth_year=TWENTY_SIX)
        result = invoke("clear", stdin="n\n")
        assert result.exit_code == 1
        assert "Apagar 3 pontos da trajetória? [s/N]" in result.output
        assert "nada foi alterado." in result.output
        assert points(clean) == CONTEXT

    def test_a_yes_clears_and_frees_the_manual_edit(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, birth_year=TWENTY_SIX)
        result = invoke("clear", stdin="s\n")
        assert result.exit_code == 0, result.output
        assert "trajetória apagada (3 pontos); os alvos ficam como estão." in result.output
        assert points(clean) == {}
        assert weights(clean) == {"B5P211": D("0.05"), "MUND11": D("0.70"), "NB1011": D("0.25")}
        update = CliRunner().invoke(app, ["update", "MUND11", "--weight", "0.6"])
        assert update.exit_code == 0, update.output
        assert weights(clean)["MUND11"] == D("0.6")

    def test_yes_skips_the_question(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, birth_year=TWENTY_SIX)
        result = invoke("clear", "--yes")
        assert result.exit_code == 0, result.output
        assert "Apagar" not in result.output
        assert points(clean) == {}

    def test_an_empty_trajectory_asks_nothing(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, points={})
        result = invoke("clear")
        assert result.exit_code == 0
        assert result.output.strip() == "A trajetória já está vazia."


# ---------------------------------------------------------------- servicos da TUI


class TestTuiServices:
    def test_the_screen_reads_the_view_and_the_tickers_of_the_form(self, clean: psycopg.Connection[DictRow]) -> None:
        # PETR4 e VALE3 encerrados (sem posicao, sem alvo): so VALE3, que esta num ponto, entra no formulario.
        seed(clean, birth_year=FORTY, extra={"PETR4": "0", "VALE3": "0"}, points={**CONTEXT, 90: {"VALE3": D("1")}})
        state = services.load_glide()
        assert state.tickers == ("B5P211", "MUND11", "NB1011", "VALE3")
        assert state.view.schedule is not None
        assert state.view.schedule.years[0].year == TODAY.year

    def test_save_remove_and_clear_write_through_the_engine(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, birth_year=TWENTY_SIX)
        set_value(clean, GLIDE_PATH_APPLIED_YEAR, TODAY.year)
        edit = services.save_glide_point(39, {"MUND11": D("1")})
        assert edit.application is not None
        assert weights(clean)["MUND11"] == D("1")
        assert services.remove_glide_point(59).points == 2
        assert services.clear_glide_path() == 2
        assert points(clean) == {}
        assert get_setting(clean, BIRTH_YEAR) == TWENTY_SIX
