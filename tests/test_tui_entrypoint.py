"""Tests for how the TUI is launched (issue #73).

``bogle`` with no arguments used to print the help; it now opens the interface.
The help is still one ``--help`` away, and a non-terminal invocation (a pipe, a
script) falls back to it instead of failing inside Textual.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from typer.testing import CliRunner

from bogle import cli as cli_mod
from bogle import format as fmt
from bogle.cli import app
from tests.test_cli import PROJECT_ROOT, run_cli

BO_BIN = PROJECT_ROOT / ".venv" / "bin" / "bo"


@pytest.fixture(autouse=True)
def _truncate_for_cli(conn: psycopg.Connection) -> Iterator[None]:
    """Requesting `conn` truncates bogle_test before the subprocess runs."""
    yield


class TestNoArguments:
    def test_interactive_terminal_opens_the_tui(self, monkeypatch: pytest.MonkeyPatch) -> None:
        launched: list[str] = []
        monkeypatch.setattr(cli_mod, "_is_interactive", lambda: True)
        monkeypatch.setattr("bogle.tui.run_tui", lambda: launched.append("tui"))
        result = CliRunner().invoke(app, [])
        assert result.exit_code == 0
        assert launched == ["tui"]

    def test_without_a_terminal_prints_the_help_instead(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def unexpected() -> None:
            raise AssertionError("a TUI nao pode abrir sem terminal")

        monkeypatch.setattr(cli_mod, "_is_interactive", lambda: False)
        monkeypatch.setattr("bogle.tui.run_tui", unexpected)
        result = CliRunner().invoke(app, [])
        # Status 2 e o mesmo de antes da TUI (quando `no_args_is_help` cuidava
        # disso): "nenhum comando" nao e sucesso, e scripts podem checar isso.
        assert result.exit_code == 2
        assert "position" in result.output  # o help lista os comandos

    def test_help_flag_never_opens_the_tui(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def unexpected() -> None:
            raise AssertionError("--help nao pode abrir a TUI")

        monkeypatch.setattr(cli_mod, "_is_interactive", lambda: True)
        monkeypatch.setattr("bogle.tui.run_tui", unexpected)
        result = CliRunner().invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "position" in result.output


class TestIsInteractive:
    def test_requires_a_terminal_on_both_ends(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class Stream:
            def __init__(self, tty: bool) -> None:
                self._tty = tty

            def isatty(self) -> bool:
                return self._tty

        def patch(stdin: bool, stdout: bool) -> None:
            monkeypatch.setattr("sys.stdin", Stream(stdin))
            monkeypatch.setattr("sys.stdout", Stream(stdout))

        patch(True, True)
        assert cli_mod._is_interactive()
        patch(True, False)
        assert not cli_mod._is_interactive()
        patch(False, True)
        assert not cli_mod._is_interactive()


class TestEndToEnd:
    """Real processes: ``capture_output`` means no tty, so no TUI can open."""

    def test_bogle_without_arguments_prints_the_help(self) -> None:
        result = run_cli()
        assert result.returncode == 2  # convencao do click para "sem comando"
        assert "position" in result.stdout
        assert "Traceback" not in result.stderr

    def test_bo_is_the_same_entry_point(self) -> None:
        assert BO_BIN.exists(), "instale o pacote (uv pip install -e .) para criar o alias `bo`"
        result = self._run_bo()
        assert result.returncode == 2
        assert "position" in result.stdout

    def test_bo_accepts_subcommands(self) -> None:
        result = self._run_bo("list")
        assert result.returncode == 0
        assert "Nenhum ativo cadastrado" in result.stdout

    @staticmethod
    def _run_bo(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(BO_BIN), *args],
            capture_output=True,
            text=True,
            env=os.environ.copy(),
            cwd=str(PROJECT_ROOT),
            check=False,
        )


class TestPreferences:
    def test_no_args_reads_nothing_and_warns_about_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # No modo interativo a TUI cuida das preferencias (e o aviso de ciclo
        # vencido virou toast na Home): o callback nao le nada nem escreve em
        # stderr antes de abrir a interface.
        def unexpected(*_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("o modo interativo nao passa pelas preferencias da CLI")

        monkeypatch.setattr(cli_mod, "_read_preferences", unexpected)
        monkeypatch.setattr(cli_mod, "_is_interactive", lambda: True)
        monkeypatch.setattr("bogle.tui.run_tui", lambda: None)
        result = CliRunner().invoke(app, [])
        assert result.exit_code == 0
        assert result.output == ""

    def test_a_command_applies_the_configured_separator(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cli_mod, "_read_preferences", lambda: (",", None))
        assert CliRunner().invoke(app, ["list"]).exit_code == 0
        assert fmt.separators().decimal == ","

    def test_an_overdue_cycle_still_warns_on_stderr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cli_mod, "_read_preferences", lambda: (".", "ciclo vencido desde 2026-07-01."))
        result = CliRunner().invoke(app, ["list"])
        assert result.exit_code == 0
        assert "aviso: ciclo vencido desde 2026-07-01." in result.output

    def test_status_does_not_repeat_the_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # `bogle status` ja reporta o ciclo por inteiro.
        monkeypatch.setattr(cli_mod, "_read_preferences", lambda: (".", "ciclo vencido desde 2026-07-01."))
        result = CliRunner().invoke(app, ["status"])
        assert "aviso:" not in result.output
