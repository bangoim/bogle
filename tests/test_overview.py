"""Tests for ``bogle.reports.overview`` (issue #73): the four headline numbers
the TUI opens with, measured at a reference close (D-1).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from psycopg.rows import DictRow

from bogle.data.cache import DiskCache
from bogle.data.dispatcher import PriceDispatcher
from bogle.data.models import HistPoint, Quote, TesouroQuote
from bogle.domain.assets import AssetType, Indexer
from bogle.domain.errors import NetworkError, QuoteNotFoundError
from bogle.domain.transactions import Transaction, TransactionType
from bogle.reports.overview import compute_current_overview, compute_overview, invested_at, pending_after
from bogle.reports.valuation import NOTHING_RETURNED
from bogle.repositories.assets import AssetRepository
from bogle.repositories.transactions import TransactionRepository
from tests.test_valuation import FakeBcb, FakeYfinance, bar, make_dispatcher

AS_OF = date(2026, 7, 20)

HISTORY = {
    "PETR4.SA": [
        bar("2025-01-06", "20"),  # compra
        bar("2025-07-18", "22"),  # ~12m antes da referencia
        bar("2026-07-17", "25"),  # ultima barra antes da referencia
    ]
}


@pytest.fixture
def seeded(conn: psycopg.Connection[DictRow]) -> None:
    AssetRepository(conn).add("PETR4", Decimal("0.5"))
    # Meio-dia UTC: a sessao le TIMESTAMPTZ em America/Sao_Paulo e meia-noite UTC
    # regrediria a data local para o dia anterior a primeira barra do fake.
    TransactionRepository(conn).add_buy(
        "PETR4", shares=Decimal("10"), unit_price=Decimal("20"), date=datetime(2025, 1, 6, 12, tzinfo=UTC)
    )


class TestComputeOverview:
    def test_patrimony_variation_and_returns(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        dispatcher = make_dispatcher(tmp_path, yfinance=FakeYfinance(dict(HISTORY)))
        overview = compute_overview(conn, dispatcher, as_of=AS_OF)

        assert overview.as_of == AS_OF
        assert overview.inception == date(2025, 1, 6)
        assert overview.invested == Decimal("200")  # 10 x 20, sem fees
        assert overview.patrimony == Decimal("250")  # 10 x 25 (fechamento de 17/jul)
        assert overview.variation == Decimal("50")
        assert overview.variation_percent == Decimal("0.25")
        assert overview.twr_total == Decimal("0.25")  # 20 -> 25
        assert overview.twr_12m == Decimal("25") / Decimal("22") - 1  # 22 -> 25
        assert overview.excluded == []
        assert not overview.is_empty

    def test_empty_ledger_has_no_numbers(self, conn: psycopg.Connection[DictRow], tmp_path: Any) -> None:
        overview = compute_overview(conn, make_dispatcher(tmp_path), as_of=AS_OF)
        assert overview.is_empty
        assert overview.inception is None
        assert overview.patrimony is None
        assert overview.variation is None
        assert overview.variation_percent is None
        assert overview.twr_12m is None
        assert overview.twr_total is None

    def test_reference_older_than_the_first_transaction_has_no_close(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        # Primeira transacao em 2025-01-06: um D-1 anterior a isso nao tem o que avaliar.
        overview = compute_overview(conn, make_dispatcher(tmp_path), as_of=date(2024, 12, 31))
        assert not overview.is_empty
        assert overview.patrimony is None
        assert overview.twr_total is None

    def test_ticker_without_history_is_excluded_from_every_number(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        # TESOURO nao tem serie historica gratuita (issue #17): fica fora do
        # patrimonio E do capital investido, para a variacao seguir comparavel.
        AssetRepository(conn).add(
            "TESOURO-IPCA-2035",
            Decimal("0.3"),
            asset_type=AssetType.TESOURO,
            indexer=Indexer.IPCA_PLUS,
            rate=Decimal("0.065"),
            is_prefixed=False,
            purchase_date=datetime(2025, 2, 3, 12, tzinfo=UTC),
            maturity_date=datetime(2035, 5, 15, 12, tzinfo=UTC),
        )
        TransactionRepository(conn).add_buy(
            "TESOURO-IPCA-2035",
            shares=Decimal("1"),
            unit_price=Decimal("5000"),
            date=datetime(2025, 2, 3, 12, tzinfo=UTC),
        )
        dispatcher = make_dispatcher(tmp_path, yfinance=FakeYfinance(dict(HISTORY)))
        overview = compute_overview(conn, dispatcher, as_of=AS_OF)

        assert overview.excluded == ["TESOURO-IPCA-2035"]
        assert overview.invested == Decimal("200")  # so PETR4, nao os 5000 do titulo
        assert overview.patrimony == Decimal("250")

    def test_a_late_series_is_out_of_the_returns_but_inside_the_patrimonio(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        # Serie que comeca depois da posicao (listagem nova): o TWR nao pode
        # caminhar de janeiro, mas o fechamento da referencia existe. Excluir do
        # patrimonio esconderia dinheiro que o provedor precifica — e nenhuma
        # tentativa do usuario mudaria isso, ja que o provedor nao tem mais.
        AssetRepository(conn).add("VWRA11", Decimal("0.3"))
        TransactionRepository(conn).add_buy(
            "VWRA11", shares=Decimal("10"), unit_price=Decimal("100"), date=datetime(2025, 6, 2, 12, tzinfo=UTC)
        )
        history = dict(HISTORY) | {"VWRA11.SA": [bar("2026-07-01", "110"), bar("2026-07-17", "115")]}
        overview = compute_overview(conn, make_dispatcher(tmp_path, yfinance=FakeYfinance(history)), as_of=AS_OF)

        assert overview.excluded == []
        assert overview.excluded_from_returns == ["VWRA11"]
        assert overview.patrimony == Decimal("250") + Decimal("1150")
        assert overview.invested == Decimal("200") + Decimal("1000")
        assert overview.twr_total == Decimal("0.25")  # so PETR4: 20 -> 25
        assert not overview.is_partial  # patrimonio esta inteiro
        assert overview.returns_are_partial  # as rentabilidades nao
        assert "2026-07-01" in overview.returns_reasons["VWRA11"]
        assert overview.all_reasons == overview.returns_reasons

    def test_a_buy_dated_after_the_reference_is_not_in_the_invested_base(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        # O caso mais comum: registrar um aporte hoje e voltar para a Home. Se o
        # capital investido viesse da view holdings (que soma o ledger inteiro),
        # o dinheiro entraria na base sem as cotas entrarem no patrimonio D-1 e a
        # Home mostraria uma perda do tamanho do aporte.
        TransactionRepository(conn).add_buy(
            "PETR4", shares=Decimal("10"), unit_price=Decimal("25"), date=datetime(2026, 7, 21, 12, tzinfo=UTC)
        )
        dispatcher = make_dispatcher(tmp_path, yfinance=FakeYfinance(dict(HISTORY)))
        overview = compute_overview(conn, dispatcher, as_of=AS_OF)  # 2026-07-20, antes da compra
        assert overview.invested == Decimal("200")  # so a compra de 2025
        assert overview.patrimony == Decimal("250")
        assert overview.variation == Decimal("50")

    def test_a_sale_dated_after_the_reference_does_not_shrink_the_base(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        # Espelho do caso acima: a venda ainda nao aconteceu na data de
        # referencia, entao nem o caixa dela sai do investido nem as cotas saem
        # do patrimonio.
        TransactionRepository(conn).add_sale(
            "PETR4", shares=Decimal("4"), unit_price=Decimal("25"), date=datetime(2026, 7, 21, 12, tzinfo=UTC)
        )
        dispatcher = make_dispatcher(tmp_path, yfinance=FakeYfinance(dict(HISTORY)))
        overview = compute_overview(conn, dispatcher, as_of=AS_OF)
        assert overview.invested == Decimal("200")  # nao desconta os 100 da venda
        assert overview.patrimony == Decimal("250")  # ainda as 10 cotas

    def test_a_position_closed_after_the_reference_still_counts_at_it(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        # A avaliacao partia das posicoes abertas *agora*: zerar o ticker depois
        # da referencia tirava ele ate das datas em que ainda era mantido, e o
        # resumo saia vazio (#84). O ledger diz que ele estava la.
        TransactionRepository(conn).add_sale(
            "PETR4", shares=Decimal("10"), unit_price=Decimal("25"), date=datetime(2026, 7, 21, 12, tzinfo=UTC)
        )
        dispatcher = make_dispatcher(tmp_path, yfinance=FakeYfinance(dict(HISTORY)))
        overview = compute_overview(conn, dispatcher, as_of=AS_OF)
        assert overview.patrimony == Decimal("250")
        assert overview.invested == Decimal("200")
        assert overview.twr_total == Decimal("0.25")
        assert overview.pending_entries == 1

    def test_a_ticker_sold_inside_the_window_stays_in_the_returns(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        # O vies de sobrevivencia da #84: VALE3 caiu 10% e foi vendida; medir so
        # o que sobrou (PETR4, +25%) seria a rentabilidade de quem ficou.
        AssetRepository(conn).add("VALE3", Decimal("0.3"))
        transactions = TransactionRepository(conn)
        transactions.add_buy(
            "VALE3", shares=Decimal("10"), unit_price=Decimal("20"), date=datetime(2025, 1, 6, 12, tzinfo=UTC)
        )
        transactions.add_sale(
            "VALE3", shares=Decimal("10"), unit_price=Decimal("18"), date=datetime(2025, 7, 18, 12, tzinfo=UTC)
        )
        history = {**HISTORY, "VALE3.SA": [bar("2025-01-06", "20"), bar("2025-07-18", "18")]}
        overview = compute_overview(conn, make_dispatcher(tmp_path, yfinance=FakeYfinance(history)), as_of=AS_OF)
        # 06/01 -> 18/07/2025: 400 viram 220 + 180 = 400 (0%); dai em diante so
        # PETR4, 220 -> 250.
        assert overview.twr_total == Decimal("250") / Decimal("220") - 1
        assert overview.patrimony == Decimal("250")  # VALE3 vale zero na referencia
        assert overview.invested == Decimal("200")  # e nao custa nada
        assert overview.excluded == []
        assert overview.sold_excluded == []

    def test_a_sold_ticker_without_history_is_named_apart(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        # Fora das rentabilidades, mas nao "dentro do patrimonio": na referencia
        # ele nao vale nada, e dizer o contrario seria falso.
        AssetRepository(conn).add("VALE3", Decimal("0.3"))
        transactions = TransactionRepository(conn)
        transactions.add_buy(
            "VALE3", shares=Decimal("10"), unit_price=Decimal("20"), date=datetime(2025, 1, 6, 12, tzinfo=UTC)
        )
        transactions.add_sale(
            "VALE3", shares=Decimal("10"), unit_price=Decimal("18"), date=datetime(2025, 7, 18, 12, tzinfo=UTC)
        )
        overview = compute_overview(conn, make_dispatcher(tmp_path, yfinance=FakeYfinance(dict(HISTORY))), as_of=AS_OF)
        assert overview.sold_excluded == ["VALE3"]
        assert overview.returns_reasons == {"VALE3": NOTHING_RETURNED}
        assert overview.excluded == []
        assert overview.excluded_from_returns == []
        assert overview.returns_are_partial
        assert not overview.is_partial  # o patrimonio esta inteiro
        assert overview.twr_total == Decimal("0.25")  # so PETR4

    def test_twelve_month_window_anchors_on_inception_and_says_so(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        dispatcher = make_dispatcher(tmp_path, yfinance=FakeYfinance(dict(HISTORY)))
        # Referencia menos de 12 meses depois da primeira transacao (2025-01-06).
        overview = compute_overview(conn, dispatcher, as_of=date(2025, 7, 18))
        assert overview.twr_12m_start == date(2025, 1, 6)
        assert overview.twr_12m_is_shorter
        assert overview.twr_12m == overview.twr_total

    def test_full_twelve_month_window_is_not_flagged(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        dispatcher = make_dispatcher(tmp_path, yfinance=FakeYfinance(dict(HISTORY)))
        overview = compute_overview(conn, dispatcher, as_of=AS_OF)
        assert overview.twr_12m_start == date(2025, 7, 20)
        assert not overview.twr_12m_is_shorter

    def test_excluded_ticker_makes_the_reading_partial(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        overview = compute_overview(conn, make_dispatcher(tmp_path), as_of=AS_OF)
        assert overview.is_partial  # PETR4 sem historico no fake

    def test_no_position_with_history_leaves_patrimony_null(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        # Fake sem historico para PETR4: nada avaliavel, mas nao e carteira vazia.
        overview = compute_overview(conn, make_dispatcher(tmp_path), as_of=AS_OF)
        assert overview.excluded == ["PETR4"]
        assert overview.patrimony is None
        assert overview.variation is None
        assert overview.twr_total is None
        assert not overview.is_empty


class TestStalePrices:
    """O provedor nao publicou a barra do dia de referencia para algum ticker."""

    def test_a_ticker_without_the_reference_close_is_named_with_the_date_used(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        # A ultima barra do fake e de 17/jul e a referencia e 20/jul: o preco
        # entra no patrimonio (e o melhor disponivel), mas nao e do dia que o
        # painel anuncia. Sem dizer isso, a Home e a tela de Posicao — que tem a
        # cotacao do dia que falta — mostram patrimonios diferentes sem motivo
        # visivel, que foi exatamente a duvida que trouxe isto.
        dispatcher = make_dispatcher(tmp_path, yfinance=FakeYfinance(dict(HISTORY)))
        overview = compute_overview(conn, dispatcher, as_of=AS_OF)

        assert overview.stale_prices == {"PETR4": date(2026, 7, 17)}
        assert overview.has_stale_prices
        assert overview.patrimony == Decimal("250")  # dentro do numero, so nao do dia
        assert overview.excluded == []  # atrasado nao e excluido

    def test_a_series_that_reaches_the_reference_reports_nothing(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        history = {"PETR4.SA": [*HISTORY["PETR4.SA"], bar("2026-07-20", "26")]}
        overview = compute_overview(conn, make_dispatcher(tmp_path, yfinance=FakeYfinance(history)), as_of=AS_OF)
        assert overview.stale_prices == {}
        assert not overview.has_stale_prices
        assert overview.patrimony == Decimal("260")

    def test_only_the_lagging_tickers_are_named(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        # Um ticker com a barra do dia e outro sem e a situacao normal: cada
        # provedor publica no seu tempo. So o atrasado entra na nota.
        AssetRepository(conn).add("VWRA11", Decimal("0.3"))
        TransactionRepository(conn).add_buy(
            "VWRA11", shares=Decimal("10"), unit_price=Decimal("100"), date=datetime(2025, 1, 6, 12, tzinfo=UTC)
        )
        history = dict(HISTORY) | {"VWRA11.SA": [bar("2025-01-06", "100"), bar("2026-07-20", "115")]}
        overview = compute_overview(conn, make_dispatcher(tmp_path, yfinance=FakeYfinance(history)), as_of=AS_OF)
        assert overview.stale_prices == {"PETR4": date(2026, 7, 17)}

    def test_a_ticker_nothing_can_price_is_excluded_not_stale(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        overview = compute_overview(conn, make_dispatcher(tmp_path), as_of=AS_OF)
        assert overview.excluded == ["PETR4"]
        assert overview.stale_prices == {}


class TestVariationIsUnrealized:
    """Valor investido pelo custo medio (#85): a variacao e so o ganho nao realizado."""

    def test_a_partial_sale_takes_its_gain_out_of_the_variation(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        # Pelo capital liquido (200 - 225 de venda) o investido ficava negativo e
        # a variacao carregava o ganho da venda. Pelo custo medio sobra 1 cota a
        # 20: o ganho realizado (9 x 5) e do `bogle profit`, nao daqui.
        TransactionRepository(conn).add_sale(
            "PETR4", shares=Decimal("9"), unit_price=Decimal("25"), date=datetime(2026, 7, 17, 12, tzinfo=UTC)
        )
        dispatcher = make_dispatcher(tmp_path, yfinance=FakeYfinance(dict(HISTORY)))
        overview = compute_overview(conn, dispatcher, as_of=AS_OF)
        assert overview.invested == Decimal("20")
        assert overview.patrimony == Decimal("25")
        assert overview.variation == Decimal("5")
        assert overview.variation_percent == Decimal("0.25")  # igual a de antes da venda


def txn(
    kind: TransactionType,
    on: str,
    *,
    ticker: str = "PETR4",
    shares: str = "0",
    price: str = "0",
    fees: str = "0",
    amount: str | None = None,
) -> Transaction:
    """A ledger row, with the same field semantics the repository writes."""
    gross = Decimal(amount) if amount is not None else Decimal(shares) * Decimal(price)
    return Transaction(
        id=1,
        ticker=ticker,
        transaction_type=kind,
        date=datetime.fromisoformat(on).replace(tzinfo=UTC),
        shares=Decimal(shares),
        unit_price=Decimal(price),
        total_investment=gross,
        fees=Decimal(fees),
        total_cost=gross + Decimal(fees) if kind is TransactionType.BUY else Decimal(fees),
        tax_withheld=Decimal("0"),
    )


class TestInvestedAt:
    """Average cost of the positions held at a date (#85)."""

    def test_counts_buys_with_their_fees(self) -> None:
        buy = txn(TransactionType.BUY, "2026-01-05", shares="10", price="20", fees="5")
        assert invested_at([buy], date(2026, 3, 1)) == Decimal("205")

    def test_ignores_transactions_after_the_date(self) -> None:
        buy = txn(TransactionType.BUY, "2026-05-05", shares="10", price="20")
        assert invested_at([buy], date(2026, 3, 1)) == Decimal("0")

    def test_a_sale_takes_out_the_cost_of_what_it_sold(self) -> None:
        # E nao o produto da venda: os 20 de ganho sao realizados e ficam fora.
        txns = [
            txn(TransactionType.BUY, "2026-01-05", shares="10", price="20"),
            txn(TransactionType.SELL, "2026-02-05", shares="4", price="25"),
        ]
        assert invested_at(txns, date(2026, 3, 1)) == Decimal("120")  # 6 x 20

    def test_a_buy_after_a_partial_sale_averages_over_what_was_left(self) -> None:
        # Replay da RFB: 6 cotas a 20 + 4 a 30 = 240 em 10 cotas. A formula
        # agregada (compras / cotas compradas) daria 14 cotas a 22,86.
        txns = [
            txn(TransactionType.BUY, "2026-01-05", shares="10", price="20"),
            txn(TransactionType.SELL, "2026-02-05", shares="4", price="25"),
            txn(TransactionType.BUY, "2026-03-05", shares="4", price="30"),
        ]
        assert invested_at(txns, date(2026, 4, 1)) == Decimal("240")

    def test_the_fees_of_a_sale_do_not_touch_the_cost(self) -> None:
        txns = [
            txn(TransactionType.BUY, "2026-01-05", shares="10", price="20", fees="10"),
            txn(TransactionType.SELL, "2026-02-05", shares="5", price="25", fees="3"),
        ]
        assert invested_at(txns, date(2026, 3, 1)) == Decimal("105")  # 5 x 21

    def test_a_ticker_the_replay_refuses_is_left_out(self) -> None:
        # Venda maior que a posicao na data: a avaliacao ja exclui o ticker, com
        # o motivo; somar um custo que o replay recusa seria inventar um numero.
        txns = [
            txn(TransactionType.SELL, "2026-01-02", ticker="MXRF11", shares="10", price="10"),
            txn(TransactionType.BUY, "2026-01-05", ticker="MXRF11", shares="20", price="9"),
            txn(TransactionType.BUY, "2026-01-05", ticker="PETR4", shares="10", price="20"),
        ]
        assert invested_at(txns, date(2026, 3, 1)) == Decimal("200")  # so PETR4

    def test_a_closed_position_leaves_the_base_entirely(self) -> None:
        # Posicao zerada nao custa nada: o lucro realizado nao vira capital.
        txns = [
            txn(TransactionType.BUY, "2026-01-05", shares="10", price="20"),
            txn(TransactionType.SELL, "2026-02-05", shares="10", price="30"),
        ]
        assert invested_at(txns, date(2026, 3, 1)) == Decimal("0")

    def test_income_is_neutral(self) -> None:
        txns = [
            txn(TransactionType.BUY, "2026-01-05", shares="10", price="20"),
            txn(TransactionType.DIVIDEND, "2026-02-05", amount="50"),
        ]
        assert invested_at(txns, date(2026, 3, 1)) == Decimal("200")

    def test_only_the_tickers_still_held_count(self) -> None:
        txns = [
            txn(TransactionType.BUY, "2026-01-05", ticker="PETR4", shares="10", price="20"),
            txn(TransactionType.BUY, "2026-01-05", ticker="MXRF11", shares="100", price="9"),
            txn(TransactionType.SELL, "2026-02-05", ticker="MXRF11", shares="100", price="10"),
        ]
        assert invested_at(txns, date(2026, 3, 1)) == Decimal("200")  # so PETR4


class TestPendingAfterTheReference:
    """Lancamentos com data posterior ao fechamento de referencia."""

    def test_a_buy_registered_today_is_counted_with_its_cost(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        # O caso que trouxe isto: registrar uma compra, voltar para a Home e ver
        # o mesmo numero. Esta certo (a referencia e um fechamento passado) e nao
        # se explica sozinho.
        TransactionRepository(conn).add_buy(
            "PETR4", shares=Decimal("19"), unit_price=Decimal("110.69"), date=datetime(2026, 7, 21, 12, tzinfo=UTC)
        )
        overview = compute_overview(conn, make_dispatcher(tmp_path, yfinance=FakeYfinance(dict(HISTORY))), as_of=AS_OF)
        assert overview.pending_entries == 1
        assert overview.pending_invested == Decimal("2103.11")
        assert overview.has_pending
        assert overview.patrimony == Decimal("250")  # segue o fechamento, sem a compra
        assert overview.invested == Decimal("200")

    def test_nothing_after_the_reference_has_nothing_pending(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        overview = compute_overview(conn, make_dispatcher(tmp_path, yfinance=FakeYfinance(dict(HISTORY))), as_of=AS_OF)
        assert overview.pending_entries == 0
        assert overview.pending_invested == Decimal("0")
        assert not overview.has_pending

    def test_a_whole_portfolio_bought_after_the_reference_is_all_pending(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        # Referencia anterior a primeira transacao: o resumo sai vazio, e o motivo
        # e justamente que tudo esta pendente.
        overview = compute_overview(conn, make_dispatcher(tmp_path), as_of=date(2024, 12, 31))
        assert overview.patrimony is None
        assert overview.pending_entries == 1
        assert overview.pending_invested == Decimal("200")

    def test_income_is_counted_but_moves_no_capital(self) -> None:
        # Um provento nao entra no patrimonio nem no capital investido: aparece na
        # contagem, e um valor ao lado dele seria uma conta que ninguem fez.
        txns = [txn(TransactionType.DIVIDEND, "2026-08-01", amount="50")]
        assert pending_after(txns, date(2026, 7, 20)) == (1, Decimal("0"))

    def test_a_sale_takes_out_the_cost_of_what_it_sold(self) -> None:
        # Mesma convencao de invested_at, para o valor ser comparavel com a base
        # em que ele vai entrar: 205 de compra, menos 4 cotas a 20,50.
        txns = [
            txn(TransactionType.BUY, "2026-08-01", shares="10", price="20", fees="5"),
            txn(TransactionType.SELL, "2026-08-02", shares="4", price="25"),
        ]
        assert pending_after(txns, date(2026, 7, 20)) == (2, Decimal("123"))

    def test_a_pending_sale_of_a_position_held_at_the_reference(self) -> None:
        txns = [
            txn(TransactionType.BUY, "2026-07-01", shares="10", price="20"),
            txn(TransactionType.SELL, "2026-08-02", shares="10", price="25"),
        ]
        assert pending_after(txns, date(2026, 7, 20)) == (1, Decimal("-200"))

    def test_the_reference_day_itself_is_not_pending(self) -> None:
        txns = [txn(TransactionType.BUY, "2026-07-20", shares="1", price="10")]
        assert pending_after(txns, date(2026, 7, 20)) == (0, Decimal("0"))


class FakeBrapi:
    """brapi's D-0 quote, and nothing else (no store, so no history is asked)."""

    def __init__(self, quotes: dict[str, Quote] | None = None, *, fail: bool = False) -> None:
        self.quotes = quotes or {}
        self.fail = fail
        self.quote_calls: list[str] = []

    def get_quote(self, symbol: str) -> Quote:
        self.quote_calls.append(symbol)
        if self.fail:
            raise NetworkError("fake", "fora do ar")
        if symbol not in self.quotes:
            raise QuoteNotFoundError(symbol, provider="fake")
        return self.quotes[symbol]

    def get_index_quote(self, index: str) -> Quote:
        raise QuoteNotFoundError(index, provider="fake")

    def get_history(self, symbol: str, **_kwargs: Any) -> list[HistPoint]:
        raise QuoteNotFoundError(symbol, provider="fake")


class NoTesouro:
    def get_quote(self, title: str) -> TesouroQuote:
        raise QuoteNotFoundError(title, provider="fake")


class TestCurrentOverview:
    """The Home summary: D-0 with brapi's quote of today, or the last close."""

    TODAY = date(2026, 7, 20)  # segunda-feira; o ultimo fechamento e o de sexta, 17/07

    def dispatcher(
        self, tmp_path: Any, brapi: FakeBrapi, *, today: date | None = None, history: dict[str, Any] | None = None
    ) -> PriceDispatcher:
        return PriceDispatcher(
            brapi=brapi,
            yfinance=FakeYfinance(history if history is not None else dict(HISTORY)),
            tesouro=NoTesouro(),
            bcb=FakeBcb(),
            quote_cache=DiskCache("quotes", base_dir=tmp_path),
            clock=lambda: today or self.TODAY,
        )

    @staticmethod
    def quote(price: str, when: datetime) -> FakeBrapi:
        return FakeBrapi({"PETR4": Quote("PETR4", Decimal(price), "BRL", when, "PETR4")})

    def test_a_quote_from_today_makes_the_summary_d0(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        brapi = self.quote("26", datetime(2026, 7, 20, 17, 7, tzinfo=UTC))
        overview = compute_current_overview(conn, self.dispatcher(tmp_path, brapi), today=self.TODAY)
        assert overview.is_live
        assert overview.as_of == self.TODAY
        assert overview.patrimony == Decimal("260")  # 10 x 26, a cotacao de agora
        assert overview.twr_total == Decimal("0.3")  # 20 -> 26
        # Horario local: 17:07 UTC sao 14:07 em Sao Paulo.
        assert overview.quote_time is not None and f"{overview.quote_time:%H:%M}" == "14:07"
        assert overview.stale_prices == {}

    def test_a_purchase_made_today_is_inside_a_d0_summary(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        TransactionRepository(conn).add_buy(
            "PETR4", shares=Decimal("10"), unit_price=Decimal("25.50"), date=datetime(2026, 7, 20, 15, tzinfo=UTC)
        )
        brapi = self.quote("26", datetime(2026, 7, 20, 17, 7, tzinfo=UTC))
        overview = compute_current_overview(conn, self.dispatcher(tmp_path, brapi), today=self.TODAY)
        assert overview.pending_entries == 0
        assert overview.invested == Decimal("455")  # 200 + 255
        assert overview.patrimony == Decimal("520")  # 20 x 26

    def test_a_sold_ticker_is_not_quoted_today(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        # Vendido nao vale nada hoje: pedir a cotacao dele seria uma chamada a
        # toa, e a falha dela derrubaria um resumo D-0 perfeitamente bom para D-1.
        AssetRepository(conn).add("VALE3", Decimal("0"))
        transactions = TransactionRepository(conn)
        transactions.add_buy(
            "VALE3", shares=Decimal("10"), unit_price=Decimal("20"), date=datetime(2025, 1, 6, 12, tzinfo=UTC)
        )
        transactions.add_sale(
            "VALE3", shares=Decimal("10"), unit_price=Decimal("21"), date=datetime(2025, 7, 18, 12, tzinfo=UTC)
        )
        brapi = self.quote("26", datetime(2026, 7, 20, 17, 7, tzinfo=UTC))
        history = {**HISTORY, "VALE3.SA": [bar("2025-01-06", "20"), bar("2025-07-18", "21")]}
        overview = compute_current_overview(conn, self.dispatcher(tmp_path, brapi, history=history), today=self.TODAY)
        assert overview.is_live
        assert brapi.quote_calls == ["PETR4"]
        assert overview.quote_failed == []

    def test_brapi_down_falls_back_to_the_last_close_and_says_why(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        overview = compute_current_overview(conn, self.dispatcher(tmp_path, FakeBrapi(fail=True)), today=self.TODAY)
        assert not overview.is_live
        assert overview.as_of == date(2026, 7, 17)
        assert overview.patrimony == Decimal("250")
        assert overview.quote_failed == ["PETR4"]

    def test_before_the_session_opens_it_is_the_last_close_with_nothing_to_explain(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        brapi = self.quote("25", datetime(2026, 7, 17, 21, 0, tzinfo=UTC))  # o fechamento de sexta
        overview = compute_current_overview(conn, self.dispatcher(tmp_path, brapi), today=self.TODAY)
        assert not overview.is_live
        assert overview.as_of == date(2026, 7, 17)
        assert overview.quote_failed == []

    def test_on_a_weekend_brapi_is_not_even_asked(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: Any
    ) -> None:
        saturday = date(2026, 7, 18)
        brapi = self.quote("26", datetime(2026, 7, 17, 21, 0, tzinfo=UTC))
        overview = compute_current_overview(conn, self.dispatcher(tmp_path, brapi, today=saturday), today=saturday)
        assert overview.as_of == date(2026, 7, 17)
        assert brapi.quote_calls == []
