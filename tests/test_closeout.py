"""Tests for the target weight of a position that closed (bogle.closeout).

Runs against bogle_test: the question is always "does the ``holdings`` view still
have a row for this ticker", and that is the view's answer to give.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import psycopg
import pytest
from psycopg.rows import DictRow

from bogle import format as fmt
from bogle.closeout import AssetRoster, clear_closed_target, cleared_notice, split_closed
from bogle.domain.assets import AssetType
from bogle.repositories.assets import AssetRepository
from bogle.repositories.holdings import HoldingRepository
from bogle.repositories.transactions import TransactionRepository

BUY = datetime(2026, 1, 5, 12, tzinfo=UTC)
SELL = datetime(2026, 6, 20, 12, tzinfo=UTC)


@pytest.fixture
def held(repo: AssetRepository, trepo: TransactionRepository) -> None:
    """Ten shares of PETR4 with a 40% target, and a neighbour to be left alone."""
    repo.add("PETR4", Decimal("0.4"), asset_type=AssetType.STOCK)
    trepo.add_buy("PETR4", BUY, Decimal("10"), Decimal("20"))
    repo.add("MXRF11", Decimal("0.1"), asset_type=AssetType.FII)


class TestClearing:
    def test_a_total_sale_clears_the_target(
        self, conn: psycopg.Connection[DictRow], repo: AssetRepository, trepo: TransactionRepository, held: None
    ) -> None:
        trepo.add_sale("PETR4", SELL, Decimal("10"), Decimal("22"))
        cleared = clear_closed_target(conn, "PETR4")
        assert cleared is not None
        assert cleared.previous_target == Decimal("0.4")
        asset = repo.get("PETR4")
        assert asset is not None and asset.target_weight == Decimal("0")

    def test_the_asset_and_its_history_survive(
        self, conn: psycopg.Connection[DictRow], repo: AssetRepository, trepo: TransactionRepository, held: None
    ) -> None:
        # O que o IR e o `bogle profit` precisam: a compra e a venda continuam la,
        # e so a intencao de ter o ativo saiu.
        trepo.add_sale("PETR4", SELL, Decimal("10"), Decimal("22"))
        clear_closed_target(conn, "PETR4")
        assert repo.get("PETR4") is not None
        assert len(trepo.list("PETR4")) == 2

    def test_a_partial_sale_leaves_the_target_alone(
        self, conn: psycopg.Connection[DictRow], repo: AssetRepository, trepo: TransactionRepository, held: None
    ) -> None:
        trepo.add_sale("PETR4", SELL, Decimal("4"), Decimal("22"))
        assert clear_closed_target(conn, "PETR4") is None
        asset = repo.get("PETR4")
        assert asset is not None and asset.target_weight == Decimal("0.4")

    def test_the_neighbours_keep_their_weights(
        self, conn: psycopg.Connection[DictRow], repo: AssetRepository, trepo: TransactionRepository, held: None
    ) -> None:
        trepo.add_sale("PETR4", SELL, Decimal("10"), Decimal("22"))
        clear_closed_target(conn, "PETR4")
        mxrf = repo.get("MXRF11")
        assert mxrf is not None and mxrf.target_weight == Decimal("0.1")

    def test_clearing_twice_is_not_a_change(
        self, conn: psycopg.Connection[DictRow], trepo: TransactionRepository, held: None
    ) -> None:
        # Idempotente: o segundo passe nao tem nada para contar, e o dialogo nao
        # pode reaparecer dizendo que removeu um target que ja estava em zero.
        trepo.add_sale("PETR4", SELL, Decimal("10"), Decimal("22"))
        assert clear_closed_target(conn, "PETR4") is not None
        assert clear_closed_target(conn, "PETR4") is None

    def test_a_ticker_that_is_not_registered_is_not_an_error(
        self, conn: psycopg.Connection[DictRow], held: None
    ) -> None:
        assert clear_closed_target(conn, "NOPE11") is None

    def test_the_ticker_is_matched_case_insensitively(
        self, conn: psycopg.Connection[DictRow], repo: AssetRepository, trepo: TransactionRepository, held: None
    ) -> None:
        trepo.add_sale("PETR4", SELL, Decimal("10"), Decimal("22"))
        cleared = clear_closed_target(conn, "petr4")
        assert cleared is not None and cleared.ticker == "PETR4"


class TestNotice:
    def test_it_names_the_ticker_the_weight_and_the_reason(
        self, conn: psycopg.Connection[DictRow], trepo: TransactionRepository, held: None
    ) -> None:
        trepo.add_sale("PETR4", SELL, Decimal("10"), Decimal("22"))
        cleared = clear_closed_target(conn, "PETR4")
        assert cleared is not None
        notice = cleared_notice(cleared)
        assert "PETR4" in notice
        assert "40.00%" in notice
        assert "aporte" in notice  # o porque: senao o dinheiro iria para la

    def test_it_follows_the_configured_separator(
        self, conn: psycopg.Connection[DictRow], trepo: TransactionRepository, held: None
    ) -> None:
        # A mesma regra de exibicao do resto da interface: o aviso nao inventa um
        # formato de numero proprio.
        trepo.add_sale("PETR4", SELL, Decimal("10"), Decimal("22"))
        cleared = clear_closed_target(conn, "PETR4")
        assert cleared is not None
        fmt.configure(",")
        assert "40,00%" in cleared_notice(cleared)


def roster_of(conn: psycopg.Connection[DictRow]) -> AssetRoster:
    held = {holding.ticker for holding in HoldingRepository(conn).list()}
    return split_closed(AssetRepository(conn).list(), held)


def tickers(assets: list) -> list[str]:
    return [asset.ticker for asset in assets]


class TestSplit:
    def test_a_sold_out_position_is_closed(
        self, conn: psycopg.Connection[DictRow], trepo: TransactionRepository, held: None
    ) -> None:
        trepo.add_sale("PETR4", SELL, Decimal("10"), Decimal("22"))
        clear_closed_target(conn, "PETR4")
        roster = roster_of(conn)
        assert tickers(roster.in_plan) == ["MXRF11"]
        assert tickers(roster.closed) == ["PETR4"]

    def test_a_target_with_no_position_yet_stays_in_the_plan(
        self, conn: psycopg.Connection[DictRow], held: None
    ) -> None:
        # MXRF11 nunca foi comprado, mas tem 10%: e o proximo aporte que vai la.
        # "Posicao zerada" sozinha o esconderia.
        assert "MXRF11" in tickers(roster_of(conn).in_plan)

    def test_a_position_with_no_target_stays_in_the_plan(
        self, conn: psycopg.Connection[DictRow], repo: AssetRepository, held: None
    ) -> None:
        # Target zero com cotas na mao: o ativo esta saindo aos poucos, e o drift
        # precisa continuar visivel.
        repo.update_weight("PETR4", Decimal("0"))
        assert tickers(roster_of(conn).closed) == []

    def test_a_target_put_back_brings_it_back_to_the_plan(
        self, conn: psycopg.Connection[DictRow], repo: AssetRepository, trepo: TransactionRepository, held: None
    ) -> None:
        trepo.add_sale("PETR4", SELL, Decimal("10"), Decimal("22"))
        clear_closed_target(conn, "PETR4")
        repo.update_weight("PETR4", Decimal("0.3"))
        roster = roster_of(conn)
        assert tickers(roster.in_plan) == ["MXRF11", "PETR4"]
        assert roster.closed == []

    def test_each_side_keeps_the_order_it_was_given(
        self, conn: psycopg.Connection[DictRow], repo: AssetRepository, trepo: TransactionRepository, held: None
    ) -> None:
        repo.add("AUVP11", Decimal("0.2"), asset_type=AssetType.ETF)
        trepo.add_buy("AUVP11", BUY, Decimal("3"), Decimal("80"))
        for ticker, shares in (("PETR4", "10"), ("AUVP11", "3")):
            trepo.add_sale(ticker, SELL, Decimal(shares), Decimal("22"))
            clear_closed_target(conn, ticker)
        assert tickers(roster_of(conn).closed) == ["AUVP11", "PETR4"]
