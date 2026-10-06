"""Tests for what ``bogle <comando>`` does before the command runs, and for the
shim that turns the failures the app expects into one line on stderr.

Both exist because of the same afternoon: a version shipped migration 006, the
database never got it, and ``bogle sell --all`` answered with the raw text of a
``CHECK`` violation. The schema now follows the code on start-up, and a database
error that still gets through is a sentence, not a traceback.
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest
from psycopg import errors as pg_errors
from typer.testing import CliRunner

from bogle import cli as cli_mod
from bogle.cli import app
from bogle.domain.errors import ValidationError


def _never(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("não deveria ter sido chamado")


class TestSchemaOnStartup:
    @pytest.fixture(autouse=True)
    def _quiet_preferences(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cli_mod, "_read_preferences", lambda: (".", None))

    def test_a_command_applies_pending_migrations_before_reading_anything(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        order: list[str] = []

        def migrate() -> list[str]:
            order.append("migrate")
            return ["006_allow_zero_target_weight"]

        def preferences() -> tuple[str, None]:
            order.append("preferences")
            return (".", None)

        monkeypatch.setattr(cli_mod, "migrate_if_pending", migrate)
        monkeypatch.setattr(cli_mod, "_read_preferences", preferences)
        result = CliRunner().invoke(app, ["list"])
        assert result.exit_code == 0
        assert order == ["migrate", "preferences"]
        assert "aviso: banco de dados atualizado: 006_allow_zero_target_weight." in result.output

    def test_nothing_pending_says_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cli_mod, "migrate_if_pending", list)
        result = CliRunner().invoke(app, ["list"])
        assert result.exit_code == 0
        assert "aviso" not in result.output

    def test_against_the_real_database_it_is_a_no_op_once_migrated(self) -> None:
        # bogle_test ja recebeu tudo no conftest: a checagem roda de verdade e
        # nao encontra nada.
        result = CliRunner().invoke(app, ["list"])
        assert result.exit_code == 0
        assert "atualizado" not in result.output

    def test_the_interactive_mode_leaves_it_to_run_tui(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A TUI migra por conta propria (antes de ler as preferencias dela); o
        # callback nao faz duas vezes.
        monkeypatch.setattr(cli_mod, "migrate_if_pending", _never)
        monkeypatch.setattr(cli_mod, "_is_interactive", lambda: True)
        monkeypatch.setattr("bogle.tui.run_tui", lambda: None)
        assert CliRunner().invoke(app, []).exit_code == 0

    def test_help_never_touches_the_database(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cli_mod, "migrate_if_pending", _never)
        result = CliRunner().invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "position" in result.output


class TestRunShim:
    """``_run``: expected failures become one line and status 1; a bug keeps its traceback."""

    @pytest.fixture(autouse=True)
    def _no_dotenv(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cli_mod, "load_dotenv", lambda: None)

    @staticmethod
    def _stderr_after(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], exc: BaseException) -> str:
        def failing_app() -> None:
            raise exc

        monkeypatch.setattr(cli_mod, "app", failing_app)
        with pytest.raises(SystemExit) as exit_info:
            cli_mod._run()
        assert exit_info.value.code == 1
        return capsys.readouterr().err

    def test_a_domain_error_is_the_message_itself(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        err = self._stderr_after(monkeypatch, capsys, ValidationError("informe --shares, ou --all."))
        assert err == "erro: informe --shares, ou --all.\n"

    def test_an_unreachable_database_gets_the_connection_hint(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        err = self._stderr_after(monkeypatch, capsys, psycopg.OperationalError("connection refused"))
        assert "não foi possível conectar ao banco de dados" in err
        assert "BOGLE_DATABASE_URL" in err

    def test_any_other_database_error_is_one_line_not_a_traceback(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # O caso que motivou: a CHECK que o schema ainda impunha antes da 006.
        violation = pg_errors.CheckViolation(
            'new row for relation "assets" violates check constraint "assets_target_weight_check"'
        )
        err = self._stderr_after(monkeypatch, capsys, violation)
        assert err.startswith("erro no banco de dados: new row for relation")
        assert "Traceback" not in err

    def test_a_bug_keeps_its_traceback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def buggy_app() -> None:
            raise RuntimeError("isso e um bug")

        monkeypatch.setattr(cli_mod, "app", buggy_app)
        with pytest.raises(RuntimeError):
            cli_mod._run()
