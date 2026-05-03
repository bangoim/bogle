from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BOGLE_BIN = PROJECT_ROOT / ".venv" / "bin" / "bogle"


@pytest.fixture(autouse=True)
def _truncate_for_cli(conn: psycopg.Connection) -> Iterator[None]:
    """O conftest já trunca antes do teste; CLI roda em subprocess e
    cria seu próprio conn, então só usamos esta fixture pra reaproveitar
    a limpeza."""
    yield


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()  # já tem BOGLE_DATABASE_URL setado pelo conftest
    return subprocess.run(
        [str(BOGLE_BIN), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(PROJECT_ROOT),
        check=False,
    )


def test_list_empty() -> None:
    result = run_cli("list")
    assert result.returncode == 0
    assert "Nenhum ativo cadastrado" in result.stdout


def test_add_success() -> None:
    result = run_cli("add", "VTI", "-w", "0.5")
    assert result.returncode == 0
    assert "VTI" in result.stdout
    assert "50.00%" in result.stdout


def test_add_duplicate_raises_with_exit_1() -> None:
    assert run_cli("add", "VTI", "-w", "0.5").returncode == 0
    result = run_cli("add", "VTI", "-w", "0.3")
    assert result.returncode == 1
    assert "ja existe" in result.stderr


def test_update_success() -> None:
    assert run_cli("add", "VTI", "-w", "0.5").returncode == 0
    result = run_cli("update", "VTI", "-w", "0.7")
    assert result.returncode == 0
    assert "70.00%" in result.stdout


def test_update_not_found() -> None:
    result = run_cli("update", "XYZ", "-w", "0.1")
    assert result.returncode == 1
    assert "nao encontrado" in result.stderr


def test_remove_success() -> None:
    assert run_cli("add", "VTI", "-w", "0.5").returncode == 0
    result = run_cli("remove", "VTI")
    assert result.returncode == 0
    assert "VTI" in result.stdout


def test_validation_weight_out_of_range() -> None:
    result = run_cli("add", "ABC", "-w", "1.5")
    assert result.returncode == 1
    assert "deve estar em (0, 1]" in result.stderr


def test_validation_weight_not_decimal() -> None:
    result = run_cli("add", "ABC", "-w", "foo")
    assert result.returncode == 1
    assert "deve ser um numero decimal" in result.stderr
