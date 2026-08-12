from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import psycopg
import pytest
from psycopg.rows import DictRow

from bogle.cli.transactions import _resolve_date
from bogle.domain.transactions import TransactionType
from bogle.repositories.assets import AssetRepository
from bogle.repositories.transactions import TransactionRepository
from tests.test_cli import SAO_PAULO, run_cli


@pytest.fixture(autouse=True)
def _truncate_for_cli(conn: psycopg.Connection) -> Iterator[None]:
    """Forca o truncate da fixture ``conn`` em TODO teste deste modulo.

    Sem isso, testes que nao pedem conn (ex.: listagem vazia) rodariam
    contra residuos do teste anterior — fixtures autouse de modulo nao
    atravessam arquivos.
    """
    yield


@pytest.fixture
def petr4(repo: AssetRepository) -> None:
    repo.add("PETR4", Decimal("0.2"))


class TestResolveDate:
    def test_default_is_timezone_aware_sao_paulo(self) -> None:
        resolved = _resolve_date(None)
        assert resolved.tzinfo == ZoneInfo("America/Sao_Paulo")

    def test_explicit_date_is_parsed(self) -> None:
        assert _resolve_date("2026-01-15") == datetime(2026, 1, 15, tzinfo=SAO_PAULO)


class TestBuy:
    def test_success_and_persistence(
        self, conn: psycopg.Connection[DictRow], trepo: TransactionRepository, petr4: None
    ) -> None:
        result = run_cli(
            "buy", "PETR4", "--shares", "100", "--price", "30.50", "--fees", "5.20", "--date", "2026-01-15"
        )
        assert result.returncode == 0
        assert "registrada: BUY PETR4 em 2026-01-15" in result.stdout
        # Linha completa: pina tambem a normalizacao dos Decimais (_fmt).
        assert "custo total: 3,055.2 (100 x 30.5 + 5.2 de fees)." in result.stdout

        tx = trepo.list("PETR4")[0]
        assert tx.transaction_type is TransactionType.BUY
        assert tx.shares == Decimal("100")
        assert tx.unit_price == Decimal("30.50")
        assert tx.fees == Decimal("5.20")
        assert tx.total_cost == Decimal("3055.20")
        assert tx.date == datetime(2026, 1, 15, tzinfo=SAO_PAULO)

    def test_date_defaults_to_today_in_sao_paulo(self, trepo: TransactionRepository, petr4: None) -> None:
        assert run_cli("buy", "PETR4", "-s", "10", "-p", "5").returncode == 0
        tx = trepo.list("PETR4")[0]
        today_sp = datetime.now(tz=ZoneInfo("America/Sao_Paulo")).date()
        assert tx.date.astimezone(ZoneInfo("America/Sao_Paulo")).date() == today_sp

    def test_unknown_ticker_is_friendly(self) -> None:
        result = run_cli("buy", "NOPE", "-s", "10", "-p", "5")
        assert result.returncode == 1
        assert "nao encontrado" in result.stderr
        assert "Traceback" not in result.stderr

    def test_repository_validation_surfaces_friendly(self, petr4: None) -> None:
        result = run_cli("buy", "PETR4", "-s", "0", "-p", "-2")
        assert result.returncode == 1
        assert "shares deve ser maior que zero" in result.stderr
        assert "unit_price deve ser maior que zero" in result.stderr

    def test_invalid_decimal_is_friendly(self, petr4: None) -> None:
        result = run_cli("buy", "PETR4", "-s", "abc", "-p", "5")
        assert result.returncode == 1
        assert "--shares deve ser um numero decimal" in result.stderr

    def test_invalid_date_is_friendly(self, petr4: None) -> None:
        result = run_cli("buy", "PETR4", "-s", "1", "-p", "5", "--date", "15/01/2026")
        assert result.returncode == 1
        assert "--date deve ser uma data ISO" in result.stderr


class TestSell:
    def test_success_with_tax_withheld(self, trepo: TransactionRepository, petr4: None) -> None:
        assert run_cli("buy", "PETR4", "-s", "100", "-p", "30").returncode == 0
        result = run_cli(
            "sell",
            "PETR4",
            "-s",
            "40",
            "-p",
            "35",
            "--fees",
            "2.50",
            "--tax-withheld",
            "0.07",
            "--date",
            "2026-03-10",
        )
        assert result.returncode == 0
        assert "registrada: SELL PETR4" in result.stdout
        assert "produto bruto da venda: 1,400" in result.stdout

        tx = next(t for t in trepo.list("PETR4") if t.transaction_type is TransactionType.SELL)
        assert tx.shares == Decimal("40")
        assert tx.total_investment == Decimal("1400")
        assert tx.total_cost == Decimal("2.50")
        assert tx.tax_withheld == Decimal("0.07")


class TestIncome:
    def test_dividend(self, trepo: TransactionRepository, petr4: None) -> None:
        result = run_cli("income", "PETR4", "--type", "DIVIDEND", "--amount", "123.45")
        assert result.returncode == 0
        assert "registrada: DIVIDEND PETR4" in result.stdout
        tx = trepo.list("PETR4")[0]
        assert tx.transaction_type is TransactionType.DIVIDEND
        assert tx.total_investment == Decimal("123.45")
        assert tx.tax_withheld == Decimal("0")

    def test_type_is_case_insensitive(self, petr4: None) -> None:
        assert run_cli("income", "PETR4", "--type", "dividend", "--amount", "10").returncode == 0

    def test_dividend_with_explicit_tax_withheld(self, trepo: TransactionRepository, petr4: None) -> None:
        result = run_cli("income", "PETR4", "--type", "DIVIDEND", "--amount", "100", "--tax-withheld", "1.5")
        assert result.returncode == 0
        tx = trepo.list("PETR4")[0]
        assert tx.tax_withheld == Decimal("1.5")  # nao descartado no despacho

    def test_jcp_requires_tax_withheld(self, trepo: TransactionRepository, petr4: None) -> None:
        result = run_cli("income", "PETR4", "--type", "JCP", "--amount", "200")
        assert result.returncode == 1
        assert "--tax-withheld e obrigatorio para JCP" in result.stderr

        result = run_cli("income", "PETR4", "--type", "JCP", "--amount", "200", "--tax-withheld", "30")
        assert result.returncode == 0
        tx = trepo.list("PETR4")[0]
        assert tx.transaction_type is TransactionType.JCP
        assert tx.total_investment == Decimal("200")
        assert tx.tax_withheld == Decimal("30")

    def test_rendimento_rejects_tax_withheld(self, trepo: TransactionRepository, repo: AssetRepository) -> None:
        repo.add("MXRF11", Decimal("0.05"))
        result = run_cli("income", "MXRF11", "--type", "RENDIMENTO", "--amount", "80", "--tax-withheld", "1")
        assert result.returncode == 1
        assert "--tax-withheld nao se aplica a RENDIMENTO" in result.stderr

        assert run_cli("income", "MXRF11", "--type", "RENDIMENTO", "--amount", "80").returncode == 0
        tx = trepo.list("MXRF11")[0]
        assert tx.transaction_type is TransactionType.RENDIMENTO
        assert tx.total_investment == Decimal("80")
        assert tx.tax_withheld == Decimal("0")

    def test_interest(self, trepo: TransactionRepository, petr4: None) -> None:
        result = run_cli("income", "PETR4", "--type", "INTEREST", "--amount", "55", "--tax-withheld", "12.375")
        assert result.returncode == 0
        tx = trepo.list("PETR4")[0]
        assert tx.transaction_type is TransactionType.INTEREST
        assert tx.total_investment == Decimal("55")
        assert tx.tax_withheld == Decimal("12.375")

    def test_buy_is_not_an_income_type(self, petr4: None) -> None:
        result = run_cli("income", "PETR4", "--type", "BUY", "--amount", "10")
        assert result.returncode == 2  # typer rejeita a choice antes do comando
        assert "BUY" in result.stderr


class TestListTransactions:
    def test_empty(self) -> None:
        result = run_cli("transactions")
        assert result.returncode == 0
        assert "Nenhuma transacao registrada." in result.stdout

    def test_empty_with_ticker_filter(self) -> None:
        result = run_cli("transactions", "petr4")
        assert result.returncode == 0
        assert "Nenhuma transacao registrada para PETR4." in result.stdout

    def test_lists_recorded_transactions(self, petr4: None) -> None:
        assert run_cli("buy", "PETR4", "-s", "100", "-p", "30", "--date", "2026-01-15").returncode == 0
        assert run_cli("income", "PETR4", "--type", "DIVIDEND", "--amount", "9.9").returncode == 0
        result = run_cli("transactions")
        assert result.returncode == 0
        assert "PETR4" in result.stdout
        assert "BUY" in result.stdout
        assert "DIVIDEND" in result.stdout  # coluna Tipo com no_wrap, nao trunca
        assert "2026-01-15" in result.stdout
        assert " - " in result.stdout  # placeholder de Qtd/Preco em linha de provento

    def test_filter_by_ticker(self, petr4: None, repo: AssetRepository) -> None:
        repo.add("VALE3", Decimal("0.1"))
        assert run_cli("buy", "PETR4", "-s", "10", "-p", "30").returncode == 0
        assert run_cli("buy", "VALE3", "-s", "5", "-p", "60").returncode == 0
        result = run_cli("transactions", "VALE3")
        assert "VALE3" in result.stdout
        assert "PETR4" not in result.stdout


class TestRemove:
    def test_success(self, trepo: TransactionRepository, petr4: None) -> None:
        assert run_cli("buy", "PETR4", "-s", "10", "-p", "30").returncode == 0
        tx_id = trepo.list("PETR4")[0].id
        result = run_cli("transaction", "remove", str(tx_id))
        assert result.returncode == 0
        assert f"transacao {tx_id} removida" in result.stdout
        assert trepo.list("PETR4") == []

    def test_missing_is_friendly(self) -> None:
        result = run_cli("transaction", "remove", "999999")
        assert result.returncode == 1
        assert "Transacao 999999 nao encontrada" in result.stderr


class TestDatabaseUnreachable:
    def test_friendly_error_without_traceback(self) -> None:
        import os
        import subprocess

        from tests.test_cli import BOGLE_BIN, PROJECT_ROOT

        env = os.environ.copy()
        env["BOGLE_DATABASE_URL"] = "postgresql://localhost/bogle_db_que_nao_existe"
        result = subprocess.run(
            [str(BOGLE_BIN), "transactions"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(PROJECT_ROOT),
            check=False,
        )
        assert result.returncode == 1
        assert "nao foi possivel conectar ao banco de dados" in result.stderr
        assert "Traceback" not in result.stderr
