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
        assert result.exit_code == 0
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
        assert result.returncode == 0
        assert "position" in result.stdout
        assert "Traceback" not in result.stderr

    def test_bo_is_the_same_entry_point(self) -> None:
        assert BO_BIN.exists(), "instale o pacote (uv pip install -e .) para criar o alias `bo`"
        result = self._run_bo()
        assert result.returncode == 0
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


class TestRebalanceReminderStaysOnCommands:
    def test_no_args_does_not_emit_the_cli_reminder(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # O aviso de ciclo vencido virou toast na Home; a linha em stderr e so
        # dos comandos diretos.
        def unexpected(*_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("sem aviso em stderr no modo interativo")

        monkeypatch.setattr(cli_mod, "_warn_if_rebalance_due", unexpected)
        monkeypatch.setattr(cli_mod, "_is_interactive", lambda: True)
        monkeypatch.setattr("bogle.tui.run_tui", lambda: None)
        assert CliRunner().invoke(app, []).exit_code == 0
