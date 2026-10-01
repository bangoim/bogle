"""Tests for how much a sale may sell, and which transactions may leave (bogle.sales).

Runs against bogle_test: the ceiling is the position at the close of the sale's
own day, read from the ledger (``bogle.domain.ledger``). What the repository
does with an oversell on its own is tested in ``test_holdings_repository`` — the
point here is that this layer never lets it get there.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from psycopg.rows import DictRow

from bogle import format as fmt
from bogle.domain.assets import AssetType
from bogle.domain.errors import InsufficientSharesError, TransactionNotFoundError, UncoveredSaleError
from bogle.domain.transactions import TransactionType
from bogle.repositories.assets import AssetRepository
from bogle.repositories.transactions import TransactionRepository
from bogle.sales import remove_transaction, resolve_sale_shares

BUY = datetime(2026, 1, 5, 12, tzinfo=UTC)
SELL = datetime(2026, 6, 20, 12, tzinfo=UTC)


def at(day: str) -> datetime:
    parsed = date.fromisoformat(day)
    return datetime(parsed.year, parsed.month, parsed.day, 12, tzinfo=UTC)


@pytest.fixture
def held(repo: AssetRepository, trepo: TransactionRepository) -> None:
    """Ten shares of PETR4, and a registered asset nobody ever bought."""
    repo.add("PETR4", Decimal("0.4"), asset_type=AssetType.STOCK)
    trepo.add_buy("PETR4", BUY, Decimal("10"), Decimal("20"))
    repo.add("MXRF11", Decimal("0.1"), asset_type=AssetType.FII)


class TestResolve:
    def test_a_quantity_within_the_position_passes_through(self, conn: psycopg.Connection[DictRow], held: None) -> None:
        assert resolve_sale_shares(conn, "PETR4", Decimal("4"), when=SELL) == Decimal("4")

    def test_the_whole_position_is_allowed(self, conn: psycopg.Connection[DictRow], held: None) -> None:
        assert resolve_sale_shares(conn, "PETR4", Decimal("10"), when=SELL) == Decimal("10")

    def test_one_share_too_many_is_refused(self, conn: psycopg.Connection[DictRow], held: None) -> None:
        with pytest.raises(InsufficientSharesError) as excinfo:
            resolve_sale_shares(conn, "PETR4", Decimal("11"), when=SELL)
        assert excinfo.value.held == Decimal("10")
        assert excinfo.value.requested == Decimal("11")
        assert str(excinfo.value) == "Em 2026-06-20 a posicao de 'PETR4' tem 10 cotas, e a venda pede 11."

    def test_selling_what_was_never_bought_says_there_is_no_position(
        self, conn: psycopg.Connection[DictRow], held: None
    ) -> None:
        with pytest.raises(InsufficientSharesError, match="nao ha posicao aberta em 'MXRF11'"):
            resolve_sale_shares(conn, "MXRF11", Decimal("1"), when=SELL)

    def test_the_ticker_is_matched_case_insensitively(self, conn: psycopg.Connection[DictRow], held: None) -> None:
        assert resolve_sale_shares(conn, "petr4", Decimal("10"), when=SELL) == Decimal("10")

    def test_nothing_is_written_by_the_refusal(
        self, conn: psycopg.Connection[DictRow], trepo: TransactionRepository, held: None
    ) -> None:
        # A checagem e uma leitura: recusar nao pode deixar rastro no ledger.
        with pytest.raises(InsufficientSharesError):
            resolve_sale_shares(conn, "PETR4", Decimal("11"), when=SELL)
        assert len(trepo.list("PETR4")) == 1

    def test_the_message_follows_the_configured_separator(
        self, conn: psycopg.Connection[DictRow], repo: AssetRepository, trepo: TransactionRepository
    ) -> None:
        repo.add("VALE3", Decimal("0.1"), asset_type=AssetType.STOCK)
        trepo.add_buy("VALE3", BUY, Decimal("1500.5"), Decimal("20"))
        fmt.configure(",")
        with pytest.raises(InsufficientSharesError) as excinfo:
            resolve_sale_shares(conn, "VALE3", Decimal("2000"), when=SELL)
        assert "1.500,5 cotas" in str(excinfo.value)

    def test_the_quantities_are_masked_with_every_other_amount(
        self, conn: psycopg.Connection[DictRow], held: None
    ) -> None:
        # Uma cota vezes um preco publico e o patrimonio: uma mensagem de erro nao
        # e um contorno do modo privacidade.
        fmt.hide_amounts(True)
        with pytest.raises(InsufficientSharesError) as excinfo:
            resolve_sale_shares(conn, "PETR4", Decimal("11"), when=SELL)
        assert "10 cotas" not in str(excinfo.value)
        assert fmt.MASK in str(excinfo.value)


class TestOnTheSaleDate:
    """A posicao que conta e a do fechamento do dia da venda, nunca a de hoje."""

    def test_a_sale_dated_before_the_purchase_is_refused(
        self, conn: psycopg.Connection[DictRow], trepo: TransactionRepository, held: None
    ) -> None:
        # Hoje ha 10, mas em 02/01 ainda nao havia nada: era o furo que deixava o
        # replay do custo medio recusar o ticker depois.
        with pytest.raises(InsufficientSharesError, match="Em 2026-01-02 nao ha posicao aberta em 'PETR4'"):
            resolve_sale_shares(conn, "PETR4", Decimal("5"), when=at("2026-01-02"))
        assert len(trepo.list("PETR4")) == 1

    def test_a_purchase_of_the_same_day_covers_the_sale(self, conn: psycopg.Connection[DictRow], held: None) -> None:
        assert resolve_sale_shares(conn, "PETR4", Decimal("10"), when=BUY) == Decimal("10")

    def test_a_past_sale_cannot_take_the_shares_of_a_later_one(
        self, conn: psycopg.Connection[DictRow], trepo: TransactionRepository, held: None
    ) -> None:
        # 10 em jan, venda de 8 em mar, mais 10 em abr: hoje ha 12, e em fev ha
        # 10, mas 8 deles sao os que a venda de marco vendeu.
        trepo.add_sale("PETR4", at("2026-03-10"), Decimal("8"), Decimal("22"))
        trepo.add_buy("PETR4", at("2026-04-01"), Decimal("10"), Decimal("21"))
        assert resolve_sale_shares(conn, "PETR4", Decimal("2"), when=at("2026-02-05")) == Decimal("2")
        with pytest.raises(InsufficientSharesError) as excinfo:
            resolve_sale_shares(conn, "PETR4", Decimal("5"), when=at("2026-02-05"))
        assert str(excinfo.value) == (
            "Em 2026-02-05 a posicao de 'PETR4' tem 10 cotas, mas so 2 estao livres: "
            "as outras cobrem a venda de 2026-03-10. A venda pede 5."
        )
        assert excinfo.value.covers == date(2026, 3, 10)

    def test_a_past_sale_with_nothing_free_says_which_sale_holds_them(
        self, conn: psycopg.Connection[DictRow], trepo: TransactionRepository, held: None
    ) -> None:
        trepo.add_sale("PETR4", at("2026-03-10"), Decimal("10"), Decimal("22"))
        trepo.add_buy("PETR4", at("2026-04-01"), Decimal("10"), Decimal("21"))
        with pytest.raises(InsufficientSharesError, match="mas todas cobrem a venda de 2026-03-10"):
            resolve_sale_shares(conn, "PETR4", Decimal("1"), when=at("2026-02-05"))

    def test_the_day_is_the_one_in_sao_paulo(self, conn: psycopg.Connection[DictRow], held: None) -> None:
        # 01:00 UTC de 05/01 ainda e 04/01 em Sao Paulo: antes da compra.
        with pytest.raises(InsufficientSharesError, match="Em 2026-01-04"):
            resolve_sale_shares(conn, "PETR4", Decimal("1"), when=datetime(2026, 1, 5, 1, tzinfo=UTC))


class TestFullExit:
    def test_no_quantity_means_the_whole_position(self, conn: psycopg.Connection[DictRow], held: None) -> None:
        assert resolve_sale_shares(conn, "PETR4", when=SELL) == Decimal("10")

    def test_it_reads_what_is_left_after_a_partial_sale(
        self, conn: psycopg.Connection[DictRow], trepo: TransactionRepository, held: None
    ) -> None:
        # O ponto de resolver no banco, e nao na tela que ofereceu: "tudo" e o que
        # o ledger tem agora, nao o que a tela viu quando abriu.
        trepo.add_sale("PETR4", SELL, Decimal("4"), Decimal("22"))
        assert resolve_sale_shares(conn, "PETR4", when=SELL) == Decimal("6")

    def test_on_a_past_date_it_is_the_position_of_that_day(
        self, conn: psycopg.Connection[DictRow], trepo: TransactionRepository, held: None
    ) -> None:
        trepo.add_buy("PETR4", at("2026-04-01"), Decimal("10"), Decimal("21"))
        assert resolve_sale_shares(conn, "PETR4", when=at("2026-02-05")) == Decimal("10")  # nao os 20 de hoje

    def test_there_is_nothing_to_exit_from_a_closed_position(
        self, conn: psycopg.Connection[DictRow], trepo: TransactionRepository, held: None
    ) -> None:
        trepo.add_sale("PETR4", SELL, Decimal("10"), Decimal("22"))
        with pytest.raises(InsufficientSharesError, match="nao ha posicao aberta em 'PETR4'"):
            resolve_sale_shares(conn, "PETR4", when=SELL)


class TestRemove:
    def test_a_purchase_nothing_depends_on_goes(
        self, conn: psycopg.Connection[DictRow], trepo: TransactionRepository, held: None
    ) -> None:
        [purchase] = trepo.list("PETR4")
        remove_transaction(conn, purchase.id)
        assert trepo.list("PETR4") == []

    def test_a_purchase_a_sale_depends_on_stays(
        self, conn: psycopg.Connection[DictRow], trepo: TransactionRepository, held: None
    ) -> None:
        trepo.add_sale("PETR4", SELL, Decimal("6"), Decimal("22"))
        purchase = next(t for t in trepo.list("PETR4") if t.transaction_type is TransactionType.BUY)
        with pytest.raises(UncoveredSaleError) as excinfo:
            remove_transaction(conn, purchase.id)
        assert str(excinfo.value) == (
            f"Remover a transacao {purchase.id} deixaria a venda de 'PETR4' em 2026-06-20 sem cotas: "
            "faltariam 6. Remova a venda antes."
        )
        assert len(trepo.list("PETR4")) == 2  # nada removido

    def test_a_purchase_the_sale_does_not_need_goes(
        self, conn: psycopg.Connection[DictRow], trepo: TransactionRepository, held: None
    ) -> None:
        extra = trepo.add_buy("PETR4", at("2026-02-01"), Decimal("5"), Decimal("21"))
        trepo.add_sale("PETR4", SELL, Decimal("6"), Decimal("22"))
        remove_transaction(conn, extra.id)
        assert len(trepo.list("PETR4")) == 2

    def test_a_sale_can_always_go(
        self, conn: psycopg.Connection[DictRow], trepo: TransactionRepository, held: None
    ) -> None:
        sale = trepo.add_sale("PETR4", SELL, Decimal("6"), Decimal("22"))
        remove_transaction(conn, sale.id)
        assert [t.transaction_type for t in trepo.list("PETR4")] == [TransactionType.BUY]

    def test_a_missing_transaction_is_reported(self, conn: psycopg.Connection[DictRow], held: None) -> None:
        with pytest.raises(TransactionNotFoundError):
            remove_transaction(conn, 999_999)
