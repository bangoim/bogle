"""End-to-end tests for ``bogle import`` (issue #34), via subprocess against ``bogle_test``."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
import pytest
from psycopg.rows import DictRow

from bogle.domain.assets import AssetType
from bogle.repositories.assets import AssetRepository
from bogle.repositories.transactions import TransactionRepository
from tests.xlsx_builder import HEADER, MOVEMENT_HEADER, build_xlsx, trade_row

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BOGLE_BIN = PROJECT_ROOT / ".venv" / "bin" / "bogle"
FIXTURE = Path(__file__).parent / "fixtures" / "b3_negociacao.xlsx"
PORTFOLIO = ("AUVP11", "B5P211", "MUND11", "NB1011", "VWRA11")


@pytest.fixture
def registered(conn: psycopg.Connection[DictRow]) -> psycopg.Connection[DictRow]:
    """The five ETFs of the export, committed so the subprocess sees them."""
    repo = AssetRepository(conn)
    for ticker in PORTFOLIO:
        repo.add(ticker, Decimal("0.2"), asset_type=AssetType.ETF)
    return conn


def run_cli(*args: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["COLUMNS"] = "200"
    return subprocess.run(
        [str(BOGLE_BIN), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(PROJECT_ROOT),
        input=stdin,
        check=False,
    )


def count(conn: psycopg.Connection[DictRow]) -> int:
    return len(TransactionRepository(conn).list())


# O separador default do bogle e a virgula (decimal_separator).
REPORT = (
    "Atenção:\n"
    "1. Novas: 21 (AUVP11 2, B5P211 7, MUND11 2, NB1011 2, VWRA11 8)\n"
    "2. Posição zerada com target: AUVP11 20,00%, VWRA11 20,00% (bogle update AUVP11 --weight 0 tira do plano)\n"
    "3. 21 operações importadas sem taxas\n"
    "4. 3 vendas importadas sem IR retido\n"
)


class TestPreview:
    def test_dry_run_writes_nothing_and_asks_nothing(self, registered: psycopg.Connection[DictRow]) -> None:
        result = run_cli("import", str(FIXTURE), "--dry-run")
        assert result.returncode == 0, result.stderr
        assert result.stdout == REPORT + "prévia: nada foi gravado.\n"
        assert "[s/N]" not in result.stdout
        assert count(registered) == 0

    def test_the_report_is_the_same_with_and_without_dry_run(self, registered: psycopg.Connection[DictRow]) -> None:
        preview = run_cli("import", str(FIXTURE), "--dry-run")
        written = run_cli("import", str(FIXTURE), stdin="s\n")
        assert written.returncode == 0, written.stderr
        assert written.stdout.startswith(REPORT)
        assert preview.stdout.removesuffix("prévia: nada foi gravado.\n") == REPORT


class TestConfirmation:
    def test_a_yes_writes_every_row(self, registered: psycopg.Connection[DictRow]) -> None:
        result = run_cli("import", str(FIXTURE), stdin="s\n")
        assert result.returncode == 0, result.stderr
        assert "Importar 21 operações? [s/N]" in result.stdout
        assert result.stdout.endswith("21 operações gravadas.\n")
        assert count(registered) == 21

    @pytest.mark.parametrize("stdin", ["n\n", "\n", ""])
    def test_a_no_or_a_closed_stdin_changes_nothing(self, registered: psycopg.Connection[DictRow], stdin: str) -> None:
        result = run_cli("import", str(FIXTURE), stdin=stdin)
        assert result.returncode == 1
        assert "nada foi alterado." in result.stderr
        assert count(registered) == 0

    def test_nothing_new_does_not_ask(self, registered: psycopg.Connection[DictRow]) -> None:
        assert run_cli("import", str(FIXTURE), stdin="s\n").returncode == 0
        again = run_cli("import", str(FIXTURE))
        assert again.returncode == 0, again.stderr
        assert "1. Novas: 0\n2. Duplicadas: 21 (AUVP11 2, B5P211 7, MUND11 2, NB1011 2, VWRA11 8)\n" in again.stdout
        assert "[s/N]" not in again.stdout
        assert count(registered) == 21


class TestFees:
    def test_fee_rate_charges_every_row(self, registered: psycopg.Connection[DictRow]) -> None:
        result = run_cli("import", str(FIXTURE), "--fee-rate", "0.00032", stdin="s\n")
        assert result.returncode == 0, result.stderr
        assert "sem taxas" not in result.stdout
        fees = [t.fees for t in TransactionRepository(registered).list()]
        assert sum(fees, Decimal("0")) == Decimal("54.67")

    def test_a_comma_rate_is_the_same_rate(self, registered: psycopg.Connection[DictRow]) -> None:
        result = run_cli("import", str(FIXTURE), "--fee-rate", "0,00032", "--dry-run")
        assert result.returncode == 0, result.stderr
        assert "sem taxas" not in result.stdout

    @pytest.mark.parametrize("rate", ["1", "-0.1", "abc"])
    def test_an_invalid_rate_is_refused(self, registered: psycopg.Connection[DictRow], rate: str) -> None:
        result = run_cli("import", str(FIXTURE), "--fee-rate", rate, "--dry-run")
        assert result.returncode == 1
        assert result.stderr.startswith("erro: --fee-rate")


class TestRefusals:
    def test_a_csv_is_refused_before_anything(self, registered: psycopg.Connection[DictRow], tmp_path: Path) -> None:
        path = tmp_path / "negociacao.csv"
        path.write_text(";".join(HEADER) + "\n", encoding="utf-8")
        result = run_cli("import", str(path))
        assert result.returncode == 1
        assert result.stderr.startswith("erro: negociacao.csv não é um .xlsx válido: use o .xlsx baixado")
        assert result.stdout == ""

    def test_the_movement_statement(self, registered: psycopg.Connection[DictRow], tmp_path: Path) -> None:
        path = build_xlsx(tmp_path / "movimentacao.xlsx", [list(MOVEMENT_HEADER)])
        result = run_cli("import", str(path))
        assert result.returncode == 1
        assert "extrato de movimentação: proventos e eventos ainda não são importados" in result.stderr

    def test_a_missing_file(self, registered: psycopg.Connection[DictRow], tmp_path: Path) -> None:
        result = run_cli("import", str(tmp_path / "nada.xlsx"))
        assert result.returncode == 1
        assert "arquivo não encontrado" in result.stderr


class TestReplace:
    """18 typed at 106.68 against the B3's 5 at 106.70 + 13 at 106.67 (issue #34)."""

    @pytest.fixture
    def typed(self, registered: psycopg.Connection[DictRow]) -> None:
        TransactionRepository(registered).add_buy(
            "VWRA11",
            datetime(2026, 1, 9, tzinfo=ZoneInfo("America/Sao_Paulo")),
            shares=Decimal("18"),
            unit_price=Decimal("106.68"),
            fees=Decimal("0.63"),
        )

    @pytest.fixture
    def path(self, tmp_path: Path) -> Path:
        return build_xlsx(
            tmp_path / "negociacao.xlsx",
            [
                list(HEADER),
                trade_row("09/01/2026", "VWRA11", 5, Decimal("106.70")),
                trade_row("09/01/2026", "VWRA11", 13, Decimal("106.67")),
            ],
        )

    def test_without_replace_the_day_is_reported_and_kept(
        self, registered: psycopg.Connection[DictRow], typed: None, path: Path
    ) -> None:
        result = run_cli("import", str(path))
        assert result.returncode == 0, result.stderr
        assert result.stdout == (
            "Atenção:\n"
            "1. Novas: 0\n"
            "2. Duplicadas: 2 (VWRA11 2)\n"
            "3. Valor do dia difere do ledger: VWRA11 (2026-01-09 compra: arquivo 1.920,21 × ledger 1.920,24); "  # noqa: RUF001
            "--replace troca pelos lançamentos da B3\n"
        )
        assert [t.shares for t in TransactionRepository(registered).list()] == [Decimal("18")]

    def test_dry_run_with_replace_shows_the_swap(
        self, registered: psycopg.Connection[DictRow], typed: None, path: Path
    ) -> None:
        result = run_cli("import", str(path), "--replace", "--dry-run")
        assert result.returncode == 0, result.stderr
        assert result.stdout == (
            "Atenção:\n"
            "1. Novas: 0\n"
            "2. Substituídas: 1 (VWRA11 2026-01-09 compra: 1 → 2 lançamentos)\n"
            "prévia: nada foi gravado.\n"
        )
        assert count(registered) == 1

    def test_replace_swaps_after_one_confirmation(
        self, registered: psycopg.Connection[DictRow], typed: None, path: Path
    ) -> None:
        result = run_cli("import", str(path), "--replace", "--fee-rate", "0.00032", stdin="s\n")
        assert result.returncode == 0, result.stderr
        assert "Importar 2 operações no lugar de 1 lançamento? [s/N]" in result.stdout
        assert result.stdout.endswith("2 operações gravadas, 1 lançamento removido.\n")
        rows = TransactionRepository(registered).list()
        assert [(t.shares, t.fees) for t in rows] == [(Decimal("5"), Decimal("0.18")), (Decimal("13"), Decimal("0.45"))]
        again = run_cli("import", str(path), "--replace")
        assert "1. Novas: 0\n2. Duplicadas: 2 (VWRA11 2)\n" in again.stdout
        assert "[s/N]" not in again.stdout

    def test_a_no_keeps_the_typed_row(self, registered: psycopg.Connection[DictRow], typed: None, path: Path) -> None:
        result = run_cli("import", str(path), "--replace", stdin="n\n")
        assert result.returncode == 1
        assert [t.shares for t in TransactionRepository(registered).list()] == [Decimal("18")]
