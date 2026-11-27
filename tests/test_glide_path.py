"""Tests for the glide path engine (issue #78): targets by age, applied on 01/01.

The trajectory of the context of the issue: born in 2000, MUND11 / NB1011 /
B5P211 at 70/25/5 up to 39, 45/10/45 at 59 and 30/0/70 from 71 on.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from psycopg.rows import DictRow
from typer.testing import CliRunner

from bogle import cli as cli_mod
from bogle import format as fmt
from bogle import glide_path as glide_mod
from bogle.cli import app
from bogle.closeout import clear_closed_target, cleared_notice
from bogle.domain.assets import AssetType
from bogle.domain.errors import AssetInGlidePathError, BackupError, GlidePathError, GlidePathTargetError
from bogle.glide_path import (
    GlideApplication,
    TargetChange,
    application_notice,
    apply_glide_path_if_due,
    check_manual_target,
    glide_targets,
    returning_target,
)
from bogle.repositories.assets import AssetRepository
from bogle.repositories.transactions import TransactionRepository
from bogle.settings import BIRTH_YEAR, GLIDE_PATH_APPLIED_YEAR, get_setting, set_value
from bogle.tui import services
from tests.test_cli import run_cli

D = Decimal

CONTEXT: dict[int, dict[str, Decimal]] = {
    39: {"MUND11": D("0.70"), "NB1011": D("0.25"), "B5P211": D("0.05")},
    59: {"MUND11": D("0.45"), "NB1011": D("0.10"), "B5P211": D("0.45")},
    71: {"MUND11": D("0.30"), "NB1011": D("0"), "B5P211": D("0.70")},
}
BIRTH = 2000
TICKERS = ("MUND11", "NB1011", "B5P211")


def targets_in(year: int) -> dict[str, Decimal]:
    return glide_targets(CONTEXT, year - BIRTH)


def as_pct(targets: dict[str, Decimal]) -> tuple[Decimal, ...]:
    """MUND11 / NB1011 / B5P211 in percent, as the issue writes them."""
    return tuple(targets[ticker] * 100 for ticker in TICKERS)


# ---------------------------------------------------------------- calculo puro


class TestGlideTargets:
    def test_at_the_anchors_the_anchors(self) -> None:
        assert as_pct(targets_in(2039)) == (70, 25, 5)
        assert as_pct(targets_in(2059)) == (45, 10, 45)
        assert as_pct(targets_in(2071)) == (30, 0, 70)

    def test_before_the_first_anchor_the_first(self) -> None:
        assert as_pct(targets_in(2026)) == (70, 25, 5)
        assert as_pct(glide_targets(CONTEXT, 0)) == (70, 25, 5)

    def test_after_the_last_anchor_the_last(self) -> None:
        assert as_pct(targets_in(2072)) == (30, 0, 70)
        assert as_pct(targets_in(2100)) == (30, 0, 70)

    @pytest.mark.parametrize(
        ("year", "expected"),
        [
            (2040, (D("68.75"), D("24.25"), D("7.00"))),
            (2050, (D("56.25"), D("16.75"), D("27.00"))),
            (2060, (D("43.75"), D("9.17"), D("47.08"))),
            (2065, (D("37.50"), D("5.00"), D("57.50"))),
        ],
    )
    def test_between_anchors_a_straight_line(self, year: int, expected: tuple[Decimal, ...]) -> None:
        assert as_pct(targets_in(year)) == expected

    def test_one_step_per_year(self) -> None:
        # MUND11 -1,25 p.p. por ano de 2040 a 2071.
        for year in range(2040, 2072):
            assert targets_in(year - 1)["MUND11"] - targets_in(year)["MUND11"] == D("0.0125")

    @pytest.mark.parametrize("year", range(2026, 2081))
    def test_the_rounded_targets_add_up_to_exactly_one(self, year: int) -> None:
        targets = targets_in(year)
        assert sum(targets.values()) == 1
        assert all(value == value.quantize(D("0.0001")) for value in targets.values())
        assert all(value >= 0 for value in targets.values())

    def test_the_leftover_units_go_to_the_largest_remainders(self) -> None:
        # 2/3, 1/6, 1/6: cortados em 4 casas faltam 2 unidades, e os tres restos
        # empatam — o desempate e pelo ticker, nunca pela ordem do dict.
        points = {0: {"C": D("0"), "B": D("0"), "A": D("1")}, 3: {"C": D("0.5"), "B": D("0.5"), "A": D("0")}}
        assert glide_targets(points, 1) == {"A": D("0.6667"), "B": D("0.1667"), "C": D("0.1666")}

    def test_a_ticker_missing_from_an_anchor_weighs_zero_there(self) -> None:
        points = {40: {"A": D("1")}, 50: {"A": D("0.5"), "B": D("0.5")}}
        assert glide_targets(points, 45) == {"A": D("0.75"), "B": D("0.25")}
        assert glide_targets(points, 30) == {"A": D("1"), "B": D("0")}

    def test_no_points_no_targets(self) -> None:
        assert glide_targets({}, 40) == {}

    def test_an_anchor_that_does_not_add_up_to_one_is_refused(self) -> None:
        points = {**CONTEXT, 59: {"MUND11": D("0.45"), "NB1011": D("0.10"), "B5P211": D("0.44")}}
        with pytest.raises(GlidePathError, match=r"aos 59 anos somam 99\.00%"):
            glide_targets(points, 40)


class TestNotice:
    def test_names_each_change_with_both_targets(self) -> None:
        fmt.configure(",")
        application = GlideApplication(
            year=2040,
            age=40,
            changes=(
                TargetChange("B5P211", D("0.05"), D("0.07")),
                TargetChange("MUND11", D("0.70"), D("0.6875")),
            ),
        )
        assert application_notice(application) == (
            "alvos de 2040 aplicados pela trajetória: B5P211 5,00% -> 7,00%, MUND11 70,00% -> 68,75%."
        )

    def test_one_change_is_singular(self) -> None:
        application = GlideApplication(year=2040, age=40, changes=(TargetChange("MUND11", D("0.7"), D("0.6875")),))
        assert application_notice(application) == "alvo de 2040 aplicado pela trajetória: MUND11 70.00% -> 68.75%."

    def test_nothing_changed_nothing_to_say(self) -> None:
        assert application_notice(GlideApplication(year=2027, age=27, changes=())) is None
        assert application_notice(None) is None


# ---------------------------------------------------------------- banco


@pytest.fixture
def clean(conn: psycopg.Connection[DictRow]) -> Iterator[psycopg.Connection[DictRow]]:
    """``conn``, and the trajectory gone after the test as well as before.

    Some tests elsewhere run the CLI against bogle_test without truncating it
    first; a trajectory left behind would be applied (and announced) there.
    """
    yield conn
    conn.execute("TRUNCATE glide_path_points, user_settings")


def seed(
    conn: psycopg.Connection[DictRow],
    *,
    weights: tuple[str, str, str] = ("0.70", "0.25", "0.05"),
    birth_year: int | None = BIRTH,
    points: dict[int, dict[str, Decimal]] | None = None,
    extra: dict[str, str] | None = None,
) -> None:
    """The three ETFs of the context, the trajectory and the year of birth."""
    repo = AssetRepository(conn)
    for ticker, weight in zip(TICKERS, weights, strict=True):
        repo.add(ticker, D(weight), asset_type=AssetType.ETF)
    for ticker, weight in (extra or {}).items():
        repo.add(ticker, D(weight), asset_type=AssetType.STOCK)
    for age, anchor in (CONTEXT if points is None else points).items():
        for ticker, weight in anchor.items():
            conn.execute(
                "INSERT INTO glide_path_points (age, ticker, target_weight) VALUES (%s, %s, %s)", (age, ticker, weight)
            )
    if birth_year is not None:
        set_value(conn, BIRTH_YEAR, birth_year)


def weights(conn: psycopg.Connection[DictRow]) -> dict[str, Decimal]:
    return {asset.ticker: asset.target_weight for asset in AssetRepository(conn).list()}


class TestApplication:
    def test_writes_the_targets_of_the_year_and_says_what_changed(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean)
        application = apply_glide_path_if_due(clean, date(2040, 1, 1))
        assert application is not None
        assert (application.year, application.age) == (2040, 40)
        assert weights(clean) == {"B5P211": D("0.07"), "MUND11": D("0.6875"), "NB1011": D("0.2425")}
        assert application.changes == (
            TargetChange("B5P211", D("0.05"), D("0.07")),
            TargetChange("MUND11", D("0.70"), D("0.6875")),
            TargetChange("NB1011", D("0.25"), D("0.2425")),
        )
        assert get_setting(clean, GLIDE_PATH_APPLIED_YEAR) == 2040

    def test_the_notice_of_the_issue(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean)
        fmt.configure(",")
        notice = application_notice(apply_glide_path_if_due(clean, date(2040, 1, 1)))
        assert notice == (
            "alvos de 2040 aplicados pela trajetória: "
            "B5P211 5,00% -> 7,00%, MUND11 70,00% -> 68,75%, NB1011 25,00% -> 24,25%."
        )

    def test_runs_once_a_year(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean)
        assert apply_glide_path_if_due(clean, date(2040, 1, 1)) is not None
        # Um alvo mexido depois da aplicacao mostra que a segunda nao escreve nada.
        AssetRepository(clean).update_weight("MUND11", D("0.5"))
        assert apply_glide_path_if_due(clean, date(2040, 12, 31)) is None
        assert weights(clean)["MUND11"] == D("0.5")
        assert get_setting(clean, GLIDE_PATH_APPLIED_YEAR) == 2040

    def test_the_next_year_applies_again(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean)
        apply_glide_path_if_due(clean, date(2040, 1, 1))
        application = apply_glide_path_if_due(clean, date(2041, 1, 2))
        assert application is not None
        assert weights(clean)["MUND11"] == D("0.675")
        assert get_setting(clean, GLIDE_PATH_APPLIED_YEAR) == 2041

    def test_a_year_with_nothing_to_change_is_still_recorded_and_silent(
        self, clean: psycopg.Connection[DictRow]
    ) -> None:
        seed(clean)
        application = apply_glide_path_if_due(clean, date(2027, 1, 1))
        assert application is not None
        assert application.changes == ()
        assert application_notice(application) is None
        assert get_setting(clean, GLIDE_PATH_APPLIED_YEAR) == 2027

    def test_without_birth_year_nothing_happens(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, birth_year=None)
        assert apply_glide_path_if_due(clean, date(2040, 1, 1)) is None
        assert weights(clean)["MUND11"] == D("0.70")
        assert get_setting(clean, GLIDE_PATH_APPLIED_YEAR) is None

    def test_without_points_nothing_happens(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, points={})
        assert apply_glide_path_if_due(clean, date(2040, 1, 1)) is None
        assert weights(clean)["MUND11"] == D("0.70")
        assert get_setting(clean, GLIDE_PATH_APPLIED_YEAR) is None

    def test_a_ticker_out_of_the_trajectory_goes_to_zero(self, clean: psycopg.Connection[DictRow]) -> None:
        # Tambem a ordem das escritas: com MUND11 subindo antes de PETR4 descer, a
        # soma passaria por 1,10 e o invariante derrubaria a aplicacao.
        seed(clean, weights=("0.60", "0.25", "0.05"), extra={"PETR4": "0.10"})
        application = apply_glide_path_if_due(clean, date(2026, 1, 1))
        assert application is not None
        assert weights(clean) == {"B5P211": D("0.05"), "MUND11": D("0.70"), "NB1011": D("0.25"), "PETR4": D("0")}
        assert application.changes == (
            TargetChange("MUND11", D("0.60"), D("0.70")),
            TargetChange("PETR4", D("0.10"), D("0")),
        )
        assert "PETR4 10.00% -> 0.00%" in (application_notice(application) or "")

    def test_a_failure_writes_nothing_and_does_not_record_the_year(
        self, clean: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seed(clean)

        def explode(*_args: Any) -> None:
            raise RuntimeError("falhou no meio")

        monkeypatch.setattr(glide_mod, "set_value", explode)  # depois de escrever os alvos
        with pytest.raises(RuntimeError):
            apply_glide_path_if_due(clean, date(2040, 1, 1))
        assert weights(clean) == {"B5P211": D("0.05"), "MUND11": D("0.70"), "NB1011": D("0.25")}
        assert get_setting(clean, GLIDE_PATH_APPLIED_YEAR) is None

    def test_an_invalid_trajectory_fails_before_writing(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, points={39: {"MUND11": D("0.7"), "NB1011": D("0.2")}})
        with pytest.raises(GlidePathError):
            apply_glide_path_if_due(clean, date(2040, 1, 1))
        assert get_setting(clean, GLIDE_PATH_APPLIED_YEAR) is None

    def test_an_asset_of_the_trajectory_cannot_be_removed(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean)
        with pytest.raises(AssetInGlidePathError, match="está na trajetória"):
            AssetRepository(clean).remove("NB1011")


class TestManualTarget:
    def test_a_ticker_of_the_trajectory_is_refused(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean)
        with pytest.raises(GlidePathTargetError, match=r"\(68.75% em 2040\). Para mudar, use 'bogle glide'"):
            check_manual_target(clean, "mund11", D("0.5"), date(2040, 3, 1))

    def test_the_value_of_the_trajectory_is_not_a_change(self, clean: psycopg.Connection[DictRow]) -> None:
        # E o que o "Reverter" de uma venda que zerou a posicao grava de volta.
        seed(clean)
        check_manual_target(clean, "MUND11", D("0.6875"), date(2040, 3, 1))

    def test_a_ticker_out_of_the_trajectory_is_free(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, weights=("0.60", "0.25", "0.05"), extra={"PETR4": "0.10"})
        check_manual_target(clean, "PETR4", D("0.05"), date(2040, 3, 1))

    def test_without_birth_year_nothing_is_refused(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, birth_year=None)
        check_manual_target(clean, "MUND11", D("0.5"), date(2040, 3, 1))

    def test_the_tui_service_refuses_too(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, birth_year=date.today().year - 20)
        with pytest.raises(GlidePathTargetError):
            services.update_asset(ticker="MUND11", target_weight=D("0.5"))
        assert weights(clean)["MUND11"] == D("0.70")
        # O tipo continua livre: so o alvo e da trajetoria.
        assert services.update_asset(ticker="MUND11", asset_type=AssetType.STOCK).asset_type is AssetType.STOCK

    def test_the_command_refuses_and_points_to_bogle_glide(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, birth_year=date.today().year - 20)
        result = run_cli("update", "MUND11", "--weight", "0.5")
        assert result.returncode == 1
        assert "erro: Alvo de 'MUND11' definido pela trajetória" in result.stderr
        assert "use 'bogle glide'" in result.stderr
        assert weights(clean)["MUND11"] == D("0.70")


BUY = datetime(2039, 2, 1, 12, tzinfo=UTC)
SELL = datetime(2039, 6, 1, 12, tzinfo=UTC)


class TestSaleThatEmptiesThePosition:
    def sell_out(self, conn: psycopg.Connection[DictRow], ticker: str) -> None:
        trades = TransactionRepository(conn)
        trades.add_buy(ticker, BUY, D("10"), D("100"))
        trades.add_sale(ticker, SELL, D("10"), D("110"))

    def test_says_the_target_comes_back_next_01_01(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean)
        self.sell_out(clean, "MUND11")
        cleared = clear_closed_target(clean, "MUND11", today=date(2039, 6, 1))
        assert cleared is not None
        assert (cleared.returns_in, cleared.returning_target) == (2040, D("0.6875"))
        assert weights(clean)["MUND11"] == D("0")  # a venda zera mesmo assim
        assert cleared_notice(cleared).endswith("\nO alvo volta a 68.75% em 01/01/2040 pela trajetória.")

    def test_a_ticker_with_no_target_next_year_does_not_come_back(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, weights=("0.30", "0.01", "0.69"))
        self.sell_out(clean, "NB1011")
        cleared = clear_closed_target(clean, "NB1011", today=date(2071, 6, 1))
        assert cleared is not None
        assert cleared.returns_in is None
        assert "trajetória" not in cleared_notice(cleared)

    def test_without_a_trajectory_the_notice_is_as_it_was(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, birth_year=None)
        self.sell_out(clean, "MUND11")
        cleared = clear_closed_target(clean, "MUND11", today=date(2039, 6, 1))
        assert cleared is not None
        assert cleared.returns_in is None
        assert "\n" not in cleared_notice(cleared)

    def test_an_invalid_trajectory_does_not_undo_the_sale(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, points={39: {"MUND11": D("0.7")}})
        self.sell_out(clean, "MUND11")
        cleared = clear_closed_target(clean, "MUND11", today=date(2039, 6, 1))
        assert cleared is not None
        assert cleared.returns_in is None

    def test_returning_target_reads_next_year(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean)
        assert returning_target(clean, "B5P211", date(2059, 7, 1)) == D("0.4708")
        assert returning_target(clean, "NB1011", date(2070, 7, 1)) is None  # 0 aos 71

    def test_the_tui_sale_carries_it(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, birth_year=date.today().year - 20)
        services.record_buy(
            ticker="MUND11", when=BUY.replace(year=2026), shares=D("10"), unit_price=D("100"), fees=D("0")
        )
        outcome = services.record_sell(
            ticker="MUND11",
            when=SELL.replace(year=2026),
            shares=D("10"),
            unit_price=D("110"),
            fees=D("0"),
            tax_withheld=D("0"),
        )
        assert outcome.cleared is not None
        assert outcome.cleared.returns_in == date.today().year + 1
        assert outcome.cleared.returning_target == D("0.70")

    def test_the_command_prints_it(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, birth_year=date.today().year - 20)
        TransactionRepository(clean).add_buy("MUND11", BUY.replace(year=2026), D("10"), D("100"))
        result = run_cli("sell", "MUND11", "--all", "--price", "110", "--date", "2026-06-01")
        assert result.returncode == 0, result.stderr
        assert f"O alvo volta a 70,00% em 01/01/{date.today().year + 1} pela trajetória." in result.stdout
        assert "para reverter: bogle update MUND11 --weight 0,7" in result.stdout


# ---------------------------------------------------------------- frontends


class TestCommandStartup:
    """The callback of every command: apply, announce on stderr, never block."""

    def test_announces_what_it_applied(self, clean: psycopg.Connection[DictRow]) -> None:
        # Aos 40, todo ano e um degrau: ha sempre o que aplicar.
        seed(clean, birth_year=date.today().year - 40)
        result = CliRunner().invoke(app, ["list"])
        assert result.exit_code == 0
        year = date.today().year
        assert (
            f"aviso: alvos de {year} aplicados pela trajetória: "
            "B5P211 5,00% -> 7,00%, MUND11 70,00% -> 68,75%, NB1011 25,00% -> 24,25%." in result.output
        )
        assert get_setting(clean, GLIDE_PATH_APPLIED_YEAR) == year
        # A segunda execucao do ano nao aplica nem avisa.
        again = CliRunner().invoke(app, ["list"])
        assert "trajetória" not in again.output

    def test_without_a_trajectory_says_nothing(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, birth_year=None)
        result = CliRunner().invoke(app, ["list"])
        assert result.exit_code == 0
        assert "aviso" not in result.output

    def test_a_failure_is_said_and_the_command_runs(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, points={39: {"MUND11": D("0.7")}})
        result = CliRunner().invoke(app, ["list"])
        assert result.exit_code == 0
        assert "aviso: alvos da trajetória não aplicados: Trajetória inválida" in result.output
        assert "MUND11" in result.output  # a tabela do `list` saiu
        assert get_setting(clean, GLIDE_PATH_APPLIED_YEAR) is None

    def test_a_backup_takes_the_database_as_it_is(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Como a migracao: o backup e uma copia do banco que foi pedido.
        def never() -> None:
            raise AssertionError("o backup não aplica a trajetória")

        def stop_here(**_kwargs: Any) -> None:
            raise BackupError("parou no backup")

        monkeypatch.setattr(cli_mod, "_apply_glide_path", never)
        monkeypatch.setattr(cli_mod, "_read_preferences", lambda: (".", None))
        monkeypatch.setattr("bogle.cli.backup.create_backup", stop_here)
        result = CliRunner().invoke(app, ["backup"])
        assert isinstance(result.exception, BackupError)


class TestTuiService:
    def test_returns_the_notice_for_the_home(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean)
        notice = services.apply_glide_path(today=date(2040, 1, 1))
        assert notice is not None
        assert notice.startswith("alvos de 2040 aplicados pela trajetória: B5P211 5.00% -> 7.00%")
        assert services.apply_glide_path(today=date(2040, 1, 2)) is None

    def test_a_failure_becomes_a_notice_too(self, clean: psycopg.Connection[DictRow]) -> None:
        seed(clean, points={39: {"MUND11": D("0.7")}})
        notice = services.apply_glide_path(today=date(2040, 1, 1))
        assert notice is not None
        assert notice.startswith("alvos da trajetória não aplicados: Trajetória inválida")

    def test_a_database_that_is_down_is_the_homes_to_report(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def down() -> None:
            raise psycopg.OperationalError("connection refused")

        monkeypatch.setattr(services, "get_connection", down)
        assert services.apply_glide_path(today=date(2040, 1, 1)) is None
