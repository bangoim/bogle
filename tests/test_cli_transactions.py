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
        assert "não encontrado" in result.stderr
        assert "Traceback" not in result.stderr

    def test_repository_validation_surfaces_friendly(self, petr4: None) -> None:
        result = run_cli("buy", "PETR4", "-s", "0", "-p", "-2")
        assert result.returncode == 1
        assert "shares deve ser maior que zero" in result.stderr
        assert "unit_price deve ser maior que zero" in result.stderr

    def test_invalid_decimal_is_friendly(self, petr4: None) -> None:
        result = run_cli("buy", "PETR4", "-s", "abc", "-p", "5")
        assert result.returncode == 1
        assert "--shares deve ser um número decimal" in result.stderr

    def test_invalid_date_is_friendly(self, petr4: None) -> None:
        result = run_cli("buy", "PETR4", "-s", "1", "-p", "5", "--date", "15/01/2026")
        assert result.returncode == 1
        assert "--date deve ser uma data ISO" in result.stderr


class TestSell:
    def test_success_with_tax_withheld(self, trepo: TransactionRepository, petr4: None) -> None:
        # A compra antes da venda: a posicao que conta e a da data dela.
        assert run_cli("buy", "PETR4", "-s", "100", "-p", "30", "--date", "2026-01-05").returncode == 0
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

    def test_a_partial_sale_says_nothing_about_targets(self, repo: AssetRepository, petr4: None) -> None:
        assert run_cli("buy", "PETR4", "-s", "100", "-p", "30").returncode == 0
        result = run_cli("sell", "PETR4", "-s", "40", "-p", "35")
        assert result.returncode == 0
        assert "target" not in result.stdout
        asset = repo.get("PETR4")
        assert asset is not None and asset.target_weight == Decimal("0.2")

    def test_a_total_sale_clears_the_target_and_prints_the_way_back(self, repo: AssetRepository, petr4: None) -> None:
        # A politica de bogle.closeout na CLI: mesma acao da TUI, e a linha exata
        # que a desfaz no lugar do botao "Reverter".
        assert run_cli("buy", "PETR4", "-s", "100", "-p", "30").returncode == 0
        result = run_cli("sell", "PETR4", "-s", "100", "-p", "35")
        assert result.returncode == 0
        assert "a venda zerou a posição" in result.stdout
        assert "target de 20.00%" in result.stdout
        assert "para reverter: bogle update PETR4 --weight 0.2" in result.stdout
        asset = repo.get("PETR4")
        assert asset is not None and asset.target_weight == Decimal("0")

    def test_the_printed_command_really_reverts_it(self, repo: AssetRepository, petr4: None) -> None:
        # Sem isso a linha e uma promessa nao verificada: (0, 1] no parse_weight,
        # por exemplo, aceita 0.2 mas o comando teria de existir com esse nome.
        assert run_cli("buy", "PETR4", "-s", "100", "-p", "30").returncode == 0
        assert run_cli("sell", "PETR4", "-s", "100", "-p", "35").returncode == 0
        assert run_cli("update", "PETR4", "--weight", "0.2").returncode == 0
        asset = repo.get("PETR4")
        assert asset is not None and asset.target_weight == Decimal("0.2")


class TestSellCeiling:
    """O ledger aceita vender o que nao se tem; o comando, nao (bogle.sales)."""

    def test_selling_more_than_the_position_is_refused(self, trepo: TransactionRepository, petr4: None) -> None:
        assert run_cli("buy", "PETR4", "-s", "100", "-p", "30").returncode == 0
        result = run_cli("sell", "PETR4", "-s", "150", "-p", "35")
        assert result.returncode == 1
        assert "tem 100 cotas" in result.stderr
        assert "pede 150" in result.stderr
        assert "Traceback" not in result.stderr
        # Nada gravado: a recusa nao pode deixar meia venda no ledger.
        assert [t.transaction_type for t in trepo.list("PETR4")] == [TransactionType.BUY]

    def test_selling_a_ticker_never_bought_is_refused(self, petr4: None) -> None:
        result = run_cli("sell", "PETR4", "-s", "1", "-p", "35")
        assert result.returncode == 1
        assert "não há posição aberta em 'PETR4'" in result.stderr

    def test_a_sale_dated_before_the_purchase_is_refused(self, trepo: TransactionRepository, petr4: None) -> None:
        # A posicao de hoje cobre, a da data nao: o que conta e a da data.
        assert run_cli("buy", "PETR4", "-s", "100", "-p", "30", "--date", "2026-03-10").returncode == 0
        result = run_cli("sell", "PETR4", "-s", "40", "-p", "35", "--date", "2026-03-02")
        assert result.returncode == 1
        assert "Em 2026-03-02 não há posição aberta em 'PETR4'" in result.stderr
        assert [t.transaction_type for t in trepo.list("PETR4")] == [TransactionType.BUY]


class TestSellAll:
    def test_it_sells_the_whole_position_without_being_told_the_quantity(
        self, trepo: TransactionRepository, repo: AssetRepository, petr4: None
    ) -> None:
        assert run_cli("buy", "PETR4", "-s", "100", "-p", "30").returncode == 0
        assert run_cli("buy", "PETR4", "-s", "37.5", "-p", "31").returncode == 0
        result = run_cli("sell", "PETR4", "--all", "-p", "35")
        assert result.returncode == 0
        assert "--all: vendendo a posição inteira, 137.5 cotas." in result.stdout

        tx = next(t for t in trepo.list("PETR4") if t.transaction_type is TransactionType.SELL)
        assert tx.shares == Decimal("137.5")
        # Zerou a posicao, entao o target vai junto — a politica de bogle.closeout.
        assert "a venda zerou a posição" in result.stdout
        asset = repo.get("PETR4")
        assert asset is not None and asset.target_weight == Decimal("0")

    def test_it_reads_what_is_left_after_a_partial_sale(self, trepo: TransactionRepository, petr4: None) -> None:
        assert run_cli("buy", "PETR4", "-s", "100", "-p", "30").returncode == 0
        assert run_cli("sell", "PETR4", "-s", "40", "-p", "35").returncode == 0
        assert run_cli("sell", "PETR4", "--all", "-p", "36").returncode == 0
        sales = [t for t in trepo.list("PETR4") if t.transaction_type is TransactionType.SELL]
        assert sorted(t.shares for t in sales) == [Decimal("40"), Decimal("60")]

    def test_with_a_date_it_is_the_position_of_that_day(self, trepo: TransactionRepository, petr4: None) -> None:
        assert run_cli("buy", "PETR4", "-s", "100", "-p", "30", "--date", "2026-01-05").returncode == 0
        assert run_cli("buy", "PETR4", "-s", "50", "-p", "31", "--date", "2026-04-01").returncode == 0
        result = run_cli("sell", "PETR4", "--all", "-p", "35", "--date", "2026-02-05")
        assert result.returncode == 0, result.stderr
        sale = next(t for t in trepo.list("PETR4") if t.transaction_type is TransactionType.SELL)
        assert sale.shares == Decimal("100")  # a posicao de fevereiro, nao as 150 de hoje

    def test_it_refuses_a_position_that_is_already_closed(self, petr4: None) -> None:
        assert run_cli("buy", "PETR4", "-s", "10", "-p", "30").returncode == 0
        assert run_cli("sell", "PETR4", "--all", "-p", "35").returncode == 0
        result = run_cli("sell", "PETR4", "--all", "-p", "35")
        assert result.returncode == 1
        assert "não há posição aberta em 'PETR4'" in result.stderr

    def test_shares_and_all_together_are_refused(self, trepo: TransactionRepository, petr4: None) -> None:
        assert run_cli("buy", "PETR4", "-s", "100", "-p", "30").returncode == 0
        result = run_cli("sell", "PETR4", "--all", "-s", "40", "-p", "35")
        assert result.returncode == 1
        assert "--all já é a quantidade" in result.stderr
        assert [t.transaction_type for t in trepo.list("PETR4")] == [TransactionType.BUY]

    def test_neither_of_them_is_refused_with_what_to_type(self, petr4: None) -> None:
        result = run_cli("sell", "PETR4", "-p", "35")
        assert result.returncode == 1
        assert "informe --shares, ou --all" in result.stderr


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
        assert "--tax-withheld é obrigatório para JCP" in result.stderr

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
        assert "--tax-withheld não se aplica a RENDIMENTO" in result.stderr

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
        assert "Nenhuma transação registrada." in result.stdout

    def test_empty_with_ticker_filter(self) -> None:
        result = run_cli("transactions", "petr4")
        assert result.returncode == 0
        assert "Nenhuma transação registrada para PETR4." in result.stdout

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
        assert f"transação {tx_id} removida" in result.stdout
        assert trepo.list("PETR4") == []

    def test_a_purchase_a_sale_depends_on_is_refused(self, trepo: TransactionRepository, petr4: None) -> None:
        assert run_cli("buy", "PETR4", "-s", "10", "-p", "30", "--date", "2026-01-05").returncode == 0
        assert run_cli("sell", "PETR4", "-s", "4", "-p", "35", "--date", "2026-02-05").returncode == 0
        purchase = next(t for t in trepo.list("PETR4") if t.transaction_type is TransactionType.BUY)
        result = run_cli("transaction", "remove", str(purchase.id))
        assert result.returncode == 1
        assert "deixaria a venda de 'PETR4' em 2026-02-05 sem cotas" in result.stderr
        assert "Traceback" not in result.stderr
        assert len(trepo.list("PETR4")) == 2

    def test_missing_is_friendly(self) -> None:
        result = run_cli("transaction", "remove", "999999")
        assert result.returncode == 1
        assert "Transação 999999 não encontrada" in result.stderr


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
        assert "não foi possível conectar ao banco de dados" in result.stderr
        assert "Traceback" not in result.stderr
