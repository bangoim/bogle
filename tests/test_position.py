"""Tests for the on-the-fly position. Runs against bogle_test; market data comes
from a real PriceDispatcher wired to fake clients (no network).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import DictRow

from bogle.data.cache import DiskCache
from bogle.data.dispatcher import PriceDispatcher
from bogle.data.fixed_income import present_value
from bogle.data.models import HistPoint, Quote, SeriesPoint
from bogle.domain.assets import AssetType, Indexer
from bogle.domain.errors import NetworkError, QuoteNotFoundError
from bogle.position import get_allocation_summary, get_portfolio_summary, price_provenance
from bogle.repositories.assets import AssetRepository
from bogle.repositories.transactions import TransactionRepository

_DT = datetime(2026, 7, 20, tzinfo=UTC)
BUY = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)  # noon UTC -> same calendar day in America/Sao_Paulo
DIV = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
SELL = datetime(2026, 1, 20, 12, 0, tzinfo=UTC)
ON_DATE = date(2026, 2, 1)


class FakeBrapi:
    def __init__(self, prices: dict[str, Any] | None = None, index_prices: dict[str, Decimal] | None = None) -> None:
        self.prices = prices or {}
        self.index_prices = index_prices or {}

    def get_quote(self, symbol: str) -> Quote:
        value = self.prices.get(symbol)
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise QuoteNotFoundError(symbol, provider="fake")
        return Quote(symbol=symbol, requested_symbol=symbol, price=value, currency="BRL", time=_DT)

    def get_index_quote(self, index: str) -> Quote:
        value = self.index_prices.get(index)
        if value is None:
            raise QuoteNotFoundError(index, provider="fake")
        return Quote(symbol=index, requested_symbol=index, price=value, currency="BRL", time=_DT)


class FakeYF:
    def __init__(self, quotes: dict[str, Decimal] | None = None, history: dict[str, list[HistPoint]] | None = None):
        self.quotes = quotes or {}
        self.history = history or {}

    def get_quote(self, symbol: str) -> Quote:
        value = self.quotes.get(symbol)
        if value is None:
            raise QuoteNotFoundError(symbol, provider="fake")
        return Quote(symbol=symbol, requested_symbol=symbol, price=value, currency="BRL", time=_DT)

    def get_history(self, symbol: str, **_kwargs: Any) -> list[HistPoint]:
        return list(self.history.get(symbol, []))


class FakeTesouro:
    def get_quote(self, title: str) -> Any:
        raise QuoteNotFoundError(title, provider="tesouro")


class FakeBcb:
    def __init__(self, cdi: Any = ()) -> None:
        self.cdi = list(cdi)

    def get_cdi(self, start: date | None = None, end: date | None = None) -> list[SeriesPoint]:
        return list(self.cdi)

    def get_selic(self, start: date | None = None, end: date | None = None) -> list[SeriesPoint]:
        return []

    def get_ipca(self, start: date | None = None, end: date | None = None) -> list[SeriesPoint]:
        return []


def cdi_series() -> list[SeriesPoint]:
    points, day = [], date(2026, 1, 5)
    while day < ON_DATE:
        if day.weekday() < 5:
            points.append(SeriesPoint(day, Decimal("0.0004")))
        day += timedelta(days=1)
    return points


def petr4_history() -> list[HistPoint]:
    def bar(day: date, close: str) -> HistPoint:
        c = Decimal(close)
        return HistPoint(
            date=datetime(day.year, day.month, day.day, tzinfo=UTC), open=c, high=c, low=c, close=c, volume=0
        )

    return [bar(date(2026, 1, 5), "20"), bar(ON_DATE, "22")]


def make_dispatcher(
    tmp_path: Path, *, brapi: FakeBrapi, yf: FakeYF | None = None, bcb: FakeBcb | None = None
) -> PriceDispatcher:
    return PriceDispatcher(
        brapi=brapi,
        yfinance=yf or FakeYF(history={"PETR4.SA": petr4_history()}),
        tesouro=FakeTesouro(),
        bcb=bcb or FakeBcb(cdi=cdi_series()),
        quote_cache=DiskCache("quotes", base_dir=tmp_path),
        clock=lambda: ON_DATE,
    )


def seed_portfolio(repo: AssetRepository, trepo: TransactionRepository) -> None:
    repo.add("PETR4", Decimal("0.4"), asset_type=AssetType.STOCK)
    trepo.add_buy("PETR4", BUY, Decimal("10"), Decimal("20"))
    trepo.add_dividend("PETR4", DIV, Decimal("5"))
    repo.add(
        "CDB01",
        Decimal("0.4"),
        asset_type=AssetType.CDB,
        issuer="Banco Teste",
        indexer=Indexer.CDI,
        rate=Decimal("1.10"),
        is_prefixed=False,
        daily_liquidity=True,
        purchase_date=BUY,
    )
    trepo.add_buy("CDB01", BUY, Decimal("1"), Decimal("1000"))


class TestPortfolioSummary:
    def test_variable_income_position(
        self, conn: psycopg.Connection[DictRow], repo: AssetRepository, trepo: TransactionRepository, tmp_path: Path
    ) -> None:
        seed_portfolio(repo, trepo)
        summary = get_portfolio_summary(
            conn, make_dispatcher(tmp_path, brapi=FakeBrapi({"PETR4": Decimal("22")})), on_date=ON_DATE
        )
        petr4 = next(p for p in summary.positions if p.ticker == "PETR4")
        assert petr4.quantity == Decimal("10")
        assert petr4.price == Decimal("22")
        assert petr4.market_value == Decimal("220")
        assert petr4.total_invested == Decimal("200")
        assert petr4.pnl == Decimal("20")
        assert petr4.pnl_percent == Decimal("0.1")
        assert petr4.dividends == Decimal("5")
        assert petr4.twr is not None

    def test_fixed_income_position_uses_present_value(
        self, conn: psycopg.Connection[DictRow], repo: AssetRepository, trepo: TransactionRepository, tmp_path: Path
    ) -> None:
        seed_portfolio(repo, trepo)
        summary = get_portfolio_summary(
            conn, make_dispatcher(tmp_path, brapi=FakeBrapi({"PETR4": Decimal("22")})), on_date=ON_DATE
        )
        cdb = next(p for p in summary.positions if p.ticker == "CDB01")
        expected = present_value(
            Decimal("1000"), indexer=Indexer.CDI, rate=Decimal("1.10"), is_prefixed=False,
            purchase_date=date(2026, 1, 5), on_date=ON_DATE, cdi=cdi_series(),
        )  # fmt: skip
        assert cdb.market_value == expected
        assert cdb.price == expected  # quantity == 1
        assert cdb.twr is not None

    def test_average_price_folds_in_the_purchase_fees(
        self, conn: psycopg.Connection[DictRow], repo: AssetRepository, trepo: TransactionRepository, tmp_path: Path
    ) -> None:
        # Corretagem e emolumentos compoem o custo (regra da RFB), entao o preco
        # medio nao e o preco pago por cota.
        seed_portfolio(repo, trepo)
        repo.add("VALE3", Decimal("0.2"))
        trepo.add_buy("VALE3", BUY, Decimal("10"), Decimal("20"), fees=Decimal("5"))
        summary = get_portfolio_summary(
            conn, make_dispatcher(tmp_path, brapi=FakeBrapi({"PETR4": Decimal("22")})), on_date=ON_DATE
        )
        assert next(p for p in summary.positions if p.ticker == "PETR4").average_price == Decimal("20")
        assert next(p for p in summary.positions if p.ticker == "VALE3").average_price == Decimal("20.5")

    def test_average_price_is_the_cost_of_what_is_left_after_a_sale(
        self, conn: psycopg.Connection[DictRow], repo: AssetRepository, trepo: TransactionRepository, tmp_path: Path
    ) -> None:
        # A conta ingenua (`total_invested / quantity`) desanda aqui: a view de
        # holdings desconta o produto da venda do capital investido, entao ela
        # passa a medir outra coisa. O preco medio das cotas que ficaram nao muda
        # com a venda (regra da RFB).
        seed_portfolio(repo, trepo)
        trepo.add_sale("PETR4", SELL, shares=Decimal("4"), unit_price=Decimal("30"))
        summary = get_portfolio_summary(
            conn, make_dispatcher(tmp_path, brapi=FakeBrapi({"PETR4": Decimal("22")})), on_date=ON_DATE
        )
        petr4 = next(p for p in summary.positions if p.ticker == "PETR4")
        assert petr4.quantity == Decimal("6")
        assert petr4.average_price == Decimal("20")
        naive = petr4.total_invested / petr4.quantity
        assert naive != petr4.average_price  # 80 / 6, que nao e preco de nada

    def test_weights_sum_to_one(
        self, conn: psycopg.Connection[DictRow], repo: AssetRepository, trepo: TransactionRepository, tmp_path: Path
    ) -> None:
        seed_portfolio(repo, trepo)
        summary = get_portfolio_summary(
            conn, make_dispatcher(tmp_path, brapi=FakeBrapi({"PETR4": Decimal("22")})), on_date=ON_DATE
        )
        total = sum((p.current_weight for p in summary.positions if p.current_weight is not None), Decimal("0"))
        assert abs(total - Decimal("1")) < Decimal("1e-9")

    def test_drift_is_current_minus_target(
        self, conn: psycopg.Connection[DictRow], repo: AssetRepository, trepo: TransactionRepository, tmp_path: Path
    ) -> None:
        seed_portfolio(repo, trepo)
        summary = get_portfolio_summary(
            conn, make_dispatcher(tmp_path, brapi=FakeBrapi({"PETR4": Decimal("22")})), on_date=ON_DATE
        )
        for p in summary.positions:
            if p.current_weight is None:
                assert p.drift is None
            else:
                assert p.drift == p.current_weight - p.target_weight

    def test_totals(
        self, conn: psycopg.Connection[DictRow], repo: AssetRepository, trepo: TransactionRepository, tmp_path: Path
    ) -> None:
        seed_portfolio(repo, trepo)
        summary = get_portfolio_summary(
            conn, make_dispatcher(tmp_path, brapi=FakeBrapi({"PETR4": Decimal("22")})), on_date=ON_DATE
        )
        cdb_value = next(p.market_value for p in summary.positions if p.ticker == "CDB01")
        assert cdb_value is not None
        assert summary.total_value == Decimal("220") + cdb_value
        assert summary.total_invested == Decimal("1200")
        assert summary.total_dividends == Decimal("5")
        assert summary.total_pnl == (Decimal("220") - Decimal("200")) + (cdb_value - Decimal("1000"))


class TestGracefulDegradation:
    def test_empty_portfolio_no_division_by_zero(self, conn: psycopg.Connection[DictRow], tmp_path: Path) -> None:
        summary = get_portfolio_summary(conn, make_dispatcher(tmp_path, brapi=FakeBrapi()), on_date=ON_DATE)
        assert summary.positions == []
        assert summary.total_value == Decimal("0")
        assert summary.total_pnl == Decimal("0")

    def test_price_failure_degrades_to_none(
        self, conn: psycopg.Connection[DictRow], repo: AssetRepository, trepo: TransactionRepository, tmp_path: Path
    ) -> None:
        seed_portfolio(repo, trepo)
        # brapi raises and yfinance has no quote/history for PETR4 -> unpriced.
        brapi = FakeBrapi({"PETR4": NetworkError("down")})
        dispatcher = make_dispatcher(tmp_path, brapi=brapi, yf=FakeYF())
        summary = get_portfolio_summary(conn, dispatcher, on_date=ON_DATE)
        petr4 = next(p for p in summary.positions if p.ticker == "PETR4")
        cdb = next(p for p in summary.positions if p.ticker == "CDB01")
        assert petr4.price is None
        assert petr4.market_value is None
        assert petr4.current_weight is None
        assert petr4.pnl is None
        assert petr4.twr is None  # no history -> no valuator
        # The priced position still carries the whole weight.
        assert cdb.current_weight == Decimal("1")


class TestAllocationSummary:
    """A visao do aporte: a posicao mais os targets que ainda nao viraram posicao."""

    def test_a_target_never_bought_comes_back_priced_and_worth_nothing(
        self, conn: psycopg.Connection[DictRow], repo: AssetRepository, trepo: TransactionRepository, tmp_path: Path
    ) -> None:
        seed_portfolio(repo, trepo)
        repo.add("VALE3", Decimal("0.2"))  # cadastrado, nunca comprado
        dispatcher = make_dispatcher(tmp_path, brapi=FakeBrapi({"PETR4": Decimal("22"), "VALE3": Decimal("60")}))
        summary = get_allocation_summary(conn, dispatcher, on_date=ON_DATE)
        vale3 = next(p for p in summary.positions if p.ticker == "VALE3")
        assert vale3.quantity == Decimal("0")
        assert vale3.market_value == Decimal("0")
        assert vale3.price == Decimal("60")  # cotado: e o que diz quantas cotas o aporte compra
        assert vale3.current_weight == Decimal("0")
        assert vale3.drift == Decimal("-0.2")
        assert vale3.total_invested == Decimal("0")

    def test_the_totals_are_the_ones_of_what_is_actually_held(
        self, conn: psycopg.Connection[DictRow], repo: AssetRepository, trepo: TransactionRepository, tmp_path: Path
    ) -> None:
        # Uma posicao que vale zero nao pode inflar patrimonio, capital investido
        # nem PnL: a mesma carteira, com um ticker a mais concorrendo ao aporte.
        seed_portfolio(repo, trepo)
        repo.add("VALE3", Decimal("0.2"))
        dispatcher = make_dispatcher(tmp_path, brapi=FakeBrapi({"PETR4": Decimal("22"), "VALE3": Decimal("60")}))
        held = get_portfolio_summary(conn, dispatcher, on_date=ON_DATE)
        allocation = get_allocation_summary(conn, dispatcher, on_date=ON_DATE)
        assert allocation.total_value == held.total_value
        assert allocation.total_invested == held.total_invested
        assert allocation.total_pnl == held.total_pnl
        assert allocation.total_dividends == held.total_dividends
        assert [p.ticker for p in allocation.positions] == ["CDB01", "PETR4", "VALE3"]

    def test_a_position_sold_down_to_zero_comes_back_only_with_a_target(
        self, conn: psycopg.Connection[DictRow], repo: AssetRepository, trepo: TransactionRepository, tmp_path: Path
    ) -> None:
        # Com o target zerado pela venda (bogle.closeout) este caso nao acontece
        # sozinho; acontece quando o usuario reverte, e ai ele quer o peso de volta.
        seed_portfolio(repo, trepo)
        trepo.add_sale("PETR4", SELL, Decimal("10"), Decimal("22"))
        dispatcher = make_dispatcher(tmp_path, brapi=FakeBrapi({"PETR4": Decimal("22")}))
        assert [p.ticker for p in get_portfolio_summary(conn, dispatcher, on_date=ON_DATE).positions] == ["CDB01"]
        petr4 = next(
            p for p in get_allocation_summary(conn, dispatcher, on_date=ON_DATE).positions if p.ticker == "PETR4"
        )
        assert petr4.quantity == Decimal("0")
        assert petr4.target_weight == Decimal("0.4")

    def test_a_target_of_zero_is_not_in_the_running(
        self, conn: psycopg.Connection[DictRow], repo: AssetRepository, trepo: TransactionRepository, tmp_path: Path
    ) -> None:
        # O que "zerar o target" significa: o ativo continua cadastrado, com o
        # historico inteiro, e fora do aporte.
        seed_portfolio(repo, trepo)
        repo.add("VALE3", Decimal("0.2"))
        repo.update_weight("VALE3", Decimal("0"))
        dispatcher = make_dispatcher(tmp_path, brapi=FakeBrapi({"PETR4": Decimal("22"), "VALE3": Decimal("60")}))
        summary = get_allocation_summary(conn, dispatcher, on_date=ON_DATE)
        assert "VALE3" not in [p.ticker for p in summary.positions]

    def test_an_unquotable_target_keeps_the_rest_of_the_portfolio(
        self, conn: psycopg.Connection[DictRow], repo: AssetRepository, trepo: TransactionRepository, tmp_path: Path
    ) -> None:
        # Sem cotacao o ticker vem sem preco, e nao com uma excecao: e o motor de
        # aporte que decide o que fazer com ele (deixar de fora, com aviso).
        seed_portfolio(repo, trepo)
        repo.add("XPTO11", Decimal("0.2"))
        dispatcher = make_dispatcher(tmp_path, brapi=FakeBrapi({"PETR4": Decimal("22")}), yf=FakeYF())
        summary = get_allocation_summary(conn, dispatcher, on_date=ON_DATE)
        xpto = next(p for p in summary.positions if p.ticker == "XPTO11")
        assert xpto.price is None
        assert xpto.market_value == Decimal("0")

    def test_a_fixed_income_target_is_worth_zero_without_asking_the_bcb(
        self, conn: psycopg.Connection[DictRow], repo: AssetRepository, trepo: TransactionRepository, tmp_path: Path
    ) -> None:
        # Um contrato que ainda nao existe nao tem valor presente. Pedir a serie
        # do BCB para um principal zero seria rede gasta para chegar a zero.
        seed_portfolio(repo, trepo)
        repo.add(
            "CDB02",
            Decimal("0.2"),
            asset_type=AssetType.CDB,
            issuer="Banco Teste",
            indexer=Indexer.CDI,
            rate=Decimal("1.05"),
            is_prefixed=False,
            daily_liquidity=True,
            purchase_date=BUY,
        )
        bcb = FakeBcb(cdi=cdi_series())
        dispatcher = make_dispatcher(tmp_path, brapi=FakeBrapi({"PETR4": Decimal("22")}), bcb=bcb)
        summary = get_allocation_summary(conn, dispatcher, on_date=ON_DATE)
        cdb02 = next(p for p in summary.positions if p.ticker == "CDB02")
        assert cdb02.price == Decimal("0")
        assert cdb02.price_source is None

    def test_nothing_bought_at_all_is_a_portfolio_of_pure_intention(
        self, conn: psycopg.Connection[DictRow], repo: AssetRepository, tmp_path: Path
    ) -> None:
        repo.add("PETR4", Decimal("0.6"))
        repo.add("VALE3", Decimal("0.4"))
        dispatcher = make_dispatcher(tmp_path, brapi=FakeBrapi({"PETR4": Decimal("22"), "VALE3": Decimal("60")}))
        summary = get_allocation_summary(conn, dispatcher, on_date=ON_DATE)
        assert [p.ticker for p in summary.positions] == ["PETR4", "VALE3"]
        assert summary.total_value == Decimal("0")
        # Peso sobre patrimonio zero nao existe — e um drift sobre ele tampouco.
        assert all(p.current_weight is None and p.drift is None for p in summary.positions)


class TestPriceProvenance:
    """O rodape que diz de onde e de quando o preco veio (Posicao e Aporte)."""

    def test_sources_are_deduplicated_and_sorted(self) -> None:
        rows = [("brapi", None), ("calculado", None), ("brapi", None)]
        assert price_provenance(rows).sources == ["brapi", "calculado"]

    def test_the_latest_timestamp_wins(self) -> None:
        older = datetime(2026, 8, 21, 17, 7, tzinfo=UTC)
        newer = datetime(2026, 8, 21, 17, 13, tzinfo=UTC)
        assert price_provenance([("brapi", older), ("brapi", newer)]).latest == newer

    def test_a_computed_value_has_no_timestamp(self) -> None:
        # Renda fixa privada e calculada, nao cotada: nao ha "cotacao mais
        # recente" para mostrar, e um None nao pode virar max().
        provenance = price_provenance([("calculado", None)])
        assert provenance.sources == ["calculado"]
        assert provenance.latest is None

    def test_nothing_priced_gives_nothing_to_show(self) -> None:
        assert price_provenance([(None, None)]) == price_provenance([])

    def test_a_generator_is_consumed_once(self) -> None:
        # Os chamadores passam uma expressao geradora; ler duas vezes devolveria
        # a segunda leitura vazia.
        rows = ((source, None) for source in ("brapi", "yfinance"))
        assert price_provenance(rows).sources == ["brapi", "yfinance"]
