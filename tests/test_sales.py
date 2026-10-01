"""Tests for how much a sale may sell (bogle.sales).

Runs against bogle_test: the ceiling is whatever the ``holdings`` view says right
now, and that is the view's answer to give. What the repository does with an
oversell on its own is tested in ``test_holdings_repository`` — the point here is
that this layer never lets it get there.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import psycopg
import pytest
from psycopg.rows import DictRow

from bogle import format as fmt
from bogle.domain.assets import AssetType
from bogle.domain.errors import InsufficientSharesError
from bogle.repositories.assets import AssetRepository
from bogle.repositories.transactions import TransactionRepository
from bogle.sales import available_shares, resolve_sale_shares

BUY = datetime(2026, 1, 5, 12, tzinfo=UTC)
SELL = datetime(2026, 6, 20, 12, tzinfo=UTC)


@pytest.fixture
def held(repo: AssetRepository, trepo: TransactionRepository) -> None:
    """Ten shares of PETR4, and a registered asset nobody ever bought."""
    repo.add("PETR4", Decimal("0.4"), asset_type=AssetType.STOCK)
    trepo.add_buy("PETR4", BUY, Decimal("10"), Decimal("20"))
    repo.add("MXRF11", Decimal("0.1"), asset_type=AssetType.FII)


class TestAvailable:
    def test_it_reads_the_open_position(self, conn: psycopg.Connection[DictRow], held: None) -> None:
        assert available_shares(conn, "PETR4") == Decimal("10")

    def test_a_partial_sale_lowers_it(
        self, conn: psycopg.Connection[DictRow], trepo: TransactionRepository, held: None
    ) -> None:
        trepo.add_sale("PETR4", SELL, Decimal("4"), Decimal("22"))
        assert available_shares(conn, "PETR4") == Decimal("6")

    def test_a_registered_asset_never_bought_has_nothing(self, conn: psycopg.Connection[DictRow], held: None) -> None:
        assert available_shares(conn, "MXRF11") == Decimal("0")

    def test_an_unknown_ticker_has_nothing(self, conn: psycopg.Connection[DictRow], held: None) -> None:
        assert available_shares(conn, "NOPE11") == Decimal("0")

    def test_the_ticker_is_matched_case_insensitively(self, conn: psycopg.Connection[DictRow], held: None) -> None:
        assert available_shares(conn, "petr4") == Decimal("10")


class TestResolve:
    def test_a_quantity_within_the_position_passes_through(self, conn: psycopg.Connection[DictRow], held: None) -> None:
        assert resolve_sale_shares(conn, "PETR4", Decimal("4")) == Decimal("4")

    def test_the_whole_position_is_allowed(self, conn: psycopg.Connection[DictRow], held: None) -> None:
        assert resolve_sale_shares(conn, "PETR4", Decimal("10")) == Decimal("10")

    def test_one_share_too_many_is_refused(self, conn: psycopg.Connection[DictRow], held: None) -> None:
        with pytest.raises(InsufficientSharesError) as excinfo:
            resolve_sale_shares(conn, "PETR4", Decimal("11"))
        assert excinfo.value.held == Decimal("10")
        assert excinfo.value.requested == Decimal("11")
        assert "tem 10 cotas" in str(excinfo.value)
        assert "pede 11" in str(excinfo.value)

    def test_selling_what_was_never_bought_says_there_is_no_position(
        self, conn: psycopg.Connection[DictRow], held: None
    ) -> None:
        with pytest.raises(InsufficientSharesError, match="Nao ha posicao aberta em 'MXRF11'"):
            resolve_sale_shares(conn, "MXRF11", Decimal("1"))

    def test_nothing_is_written_by_the_refusal(
        self, conn: psycopg.Connection[DictRow], trepo: TransactionRepository, held: None
    ) -> None:
        # A checagem e uma leitura: recusar nao pode deixar rastro no ledger.
        with pytest.raises(InsufficientSharesError):
            resolve_sale_shares(conn, "PETR4", Decimal("11"))
        assert len(trepo.list("PETR4")) == 1

    def test_the_message_follows_the_configured_separator(
        self, conn: psycopg.Connection[DictRow], repo: AssetRepository, trepo: TransactionRepository
    ) -> None:
        repo.add("VALE3", Decimal("0.1"), asset_type=AssetType.STOCK)
        trepo.add_buy("VALE3", BUY, Decimal("1500.5"), Decimal("20"))
        fmt.configure(",")
        with pytest.raises(InsufficientSharesError) as excinfo:
            resolve_sale_shares(conn, "VALE3", Decimal("2000"))
        assert "1.500,5 cotas" in str(excinfo.value)

    def test_the_quantities_are_masked_with_every_other_amount(
        self, conn: psycopg.Connection[DictRow], held: None
    ) -> None:
        # Uma cota vezes um preco publico e o patrimonio: uma mensagem de erro nao
        # e um contorno do modo privacidade.
        fmt.hide_amounts(True)
        with pytest.raises(InsufficientSharesError) as excinfo:
            resolve_sale_shares(conn, "PETR4", Decimal("11"))
        assert "10" not in str(excinfo.value)
        assert fmt.MASK in str(excinfo.value)


class TestFullExit:
    def test_no_quantity_means_the_whole_position(self, conn: psycopg.Connection[DictRow], held: None) -> None:
        assert resolve_sale_shares(conn, "PETR4") == Decimal("10")

    def test_it_reads_what_is_left_after_a_partial_sale(
        self, conn: psycopg.Connection[DictRow], trepo: TransactionRepository, held: None
    ) -> None:
        # O ponto de resolver no banco, e nao na tela que ofereceu: "tudo" e o que
        # o ledger tem agora, nao o que a tela viu quando abriu.
        trepo.add_sale("PETR4", SELL, Decimal("4"), Decimal("22"))
        assert resolve_sale_shares(conn, "PETR4") == Decimal("6")

    def test_there_is_nothing_to_exit_from_a_closed_position(
        self, conn: psycopg.Connection[DictRow], trepo: TransactionRepository, held: None
    ) -> None:
        trepo.add_sale("PETR4", SELL, Decimal("10"), Decimal("22"))
        with pytest.raises(InsufficientSharesError, match="Nao ha posicao aberta em 'PETR4'"):
            resolve_sale_shares(conn, "PETR4")
