from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from bogle.domain.assets import AssetType
from bogle.repositories.assets import AssetRepository
from bogle.repositories.holdings import HoldingRepository
from bogle.repositories.transactions import TransactionRepository

D1 = datetime(2025, 1, 15, tzinfo=UTC)
D2 = datetime(2025, 2, 15, tzinfo=UTC)
D3 = datetime(2025, 3, 15, tzinfo=UTC)


@pytest.fixture
def petr4(repo: AssetRepository) -> None:
    repo.add("PETR4", Decimal("0.15"))


class TestBuysOnly:
    def test_single_buy(self, hrepo: HoldingRepository, trepo: TransactionRepository, petr4: None) -> None:
        trepo.add_buy("PETR4", D1, Decimal("100"), Decimal("30"), fees=Decimal("10"))
        h = hrepo.get("PETR4")
        assert h is not None
        assert h.ticker == "PETR4"
        assert h.target_weight == Decimal("0.1500")
        assert h.asset_type is AssetType.STOCK
        assert h.total_shares == Decimal("100")
        assert h.total_invested == Decimal("3010")  # 100 * 30 + 10 de fees

    def test_multiple_buys_aggregate(self, hrepo: HoldingRepository, trepo: TransactionRepository, petr4: None) -> None:
        trepo.add_buy("PETR4", D1, Decimal("100"), Decimal("30"), fees=Decimal("10"))
        trepo.add_buy("PETR4", D2, Decimal("50"), Decimal("32"), fees=Decimal("5"))
        h = hrepo.get("PETR4")
        assert h is not None
        assert h.total_shares == Decimal("150")
        assert h.total_invested == Decimal("4615")  # 3010 + 1605


class TestSells:
    def test_partial_sell_reduces_position(
        self, hrepo: HoldingRepository, trepo: TransactionRepository, petr4: None
    ) -> None:
        trepo.add_buy("PETR4", D1, Decimal("100"), Decimal("10"))
        trepo.add_sale("PETR4", D2, Decimal("40"), Decimal("12"))
        h = hrepo.get("PETR4")
        assert h is not None
        assert h.total_shares == Decimal("60")
        assert h.total_invested == Decimal("520")  # 1000 - 480 (produto bruto da venda)

    def test_sale_fees_and_tax_do_not_affect_invested(
        self, hrepo: HoldingRepository, trepo: TransactionRepository, petr4: None
    ) -> None:
        # total_invested subtrai o produto BRUTO da venda; fees e IR retido
        # da venda ficam de fora por definicao (vao para o PnL realizado).
        trepo.add_buy("PETR4", D1, Decimal("100"), Decimal("10"), fees=Decimal("7"))
        trepo.add_sale("PETR4", D2, Decimal("40"), Decimal("12"), fees=Decimal("3"), tax_withheld=Decimal("0.02"))
        h = hrepo.get("PETR4")
        assert h is not None
        assert h.total_invested == Decimal("527")  # 1007 - 480; nem 530, nem 524

    def test_total_sell_removes_from_view(
        self, hrepo: HoldingRepository, trepo: TransactionRepository, petr4: None
    ) -> None:
        trepo.add_buy("PETR4", D1, Decimal("100"), Decimal("10"))
        trepo.add_sale("PETR4", D2, Decimal("100"), Decimal("12"))
        assert hrepo.get("PETR4") is None
        assert hrepo.list() == []

    def test_multiple_buys_and_sells(self, hrepo: HoldingRepository, trepo: TransactionRepository, petr4: None) -> None:
        trepo.add_buy("PETR4", D1, Decimal("100"), Decimal("10"))  # +1000
        trepo.add_sale("PETR4", D2, Decimal("30"), Decimal("12"))  # -360
        trepo.add_buy("PETR4", D2, Decimal("50"), Decimal("11"), fees=Decimal("2"))  # +552
        trepo.add_sale("PETR4", D3, Decimal("20"), Decimal("13"))  # -260
        h = hrepo.get("PETR4")
        assert h is not None
        assert h.total_shares == Decimal("100")  # 100 - 30 + 50 - 20
        assert h.total_invested == Decimal("932")  # 1000 - 360 + 552 - 260

    def test_profitable_sale_makes_invested_negative(
        self, hrepo: HoldingRepository, trepo: TransactionRepository, petr4: None
    ) -> None:
        # Capital liquido investido pode ficar negativo com a posicao ativa:
        # as vendas ja devolveram mais caixa do que entrou.
        trepo.add_buy("PETR4", D1, Decimal("100"), Decimal("10"))  # +1000
        trepo.add_sale("PETR4", D2, Decimal("60"), Decimal("20"))  # -1200
        h = hrepo.get("PETR4")
        assert h is not None
        assert h.total_shares == Decimal("40")
        assert h.total_invested == Decimal("-200")

    def test_oversell_hides_position(self, hrepo: HoldingRepository, trepo: TransactionRepository, petr4: None) -> None:
        # Vender mais do que possui deixa a soma negativa; a view oculta.
        # Validacao de oversell no add_sale esta fora do escopo (issue #9).
        trepo.add_buy("PETR4", D1, Decimal("10"), Decimal("10"))
        trepo.add_sale("PETR4", D2, Decimal("15"), Decimal("10"))
        assert hrepo.get("PETR4") is None


class TestIncomeNeutrality:
    def test_income_does_not_change_position(
        self, hrepo: HoldingRepository, trepo: TransactionRepository, petr4: None
    ) -> None:
        trepo.add_buy("PETR4", D1, Decimal("100"), Decimal("10"))
        before = hrepo.get("PETR4")
        trepo.add_dividend("PETR4", D2, Decimal("50"))
        trepo.add_jcp("PETR4", D2, Decimal("30"), Decimal("4.5"))
        trepo.add_interest("PETR4", D2, Decimal("12"))
        after = hrepo.get("PETR4")
        assert before is not None and after is not None
        assert after.total_shares == before.total_shares
        assert after.total_invested == before.total_invested

    def test_income_only_ticker_is_not_an_active_position(
        self, hrepo: HoldingRepository, trepo: TransactionRepository, repo: AssetRepository
    ) -> None:
        # Sem compras, nao ha posicao — o provento orfao nao aparece na view
        # (na 003 ele aparecia com avg NULL; a 004 filtra por shares > 0).
        repo.add("MXRF11", Decimal("0.05"))
        trepo.add_rendimento("MXRF11", D1, Decimal("80"))
        assert hrepo.get("MXRF11") is None

    def test_income_only_ticker_does_not_break_portfolio_listing(
        self, hrepo: HoldingRepository, trepo: TransactionRepository, repo: AssetRepository
    ) -> None:
        repo.add("PETR4", Decimal("0.15"))
        repo.add("MXRF11", Decimal("0.05"))
        trepo.add_buy("PETR4", D1, Decimal("100"), Decimal("30"))
        trepo.add_dividend("MXRF11", D2, Decimal("10"))
        assert [h.ticker for h in hrepo.list()] == ["PETR4"]


class TestViewMembership:
    def test_asset_without_transactions_is_absent(self, hrepo: HoldingRepository, repo: AssetRepository) -> None:
        repo.add("PETR4", Decimal("0.15"))
        assert hrepo.get("PETR4") is None
        assert hrepo.list() == []

    def test_list_ordered_by_ticker(
        self, hrepo: HoldingRepository, trepo: TransactionRepository, repo: AssetRepository
    ) -> None:
        repo.add("VALE3", Decimal("0.1"))
        repo.add("ITUB4", Decimal("0.1"))
        trepo.add_buy("VALE3", D1, Decimal("10"), Decimal("60"))
        trepo.add_buy("ITUB4", D1, Decimal("20"), Decimal("25"))
        assert [h.ticker for h in hrepo.list()] == ["ITUB4", "VALE3"]

    def test_get_ticker_uppercased(self, hrepo: HoldingRepository, trepo: TransactionRepository, petr4: None) -> None:
        trepo.add_buy("PETR4", D1, Decimal("100"), Decimal("30"))
        assert hrepo.get("petr4") is not None

    def test_get_returns_the_requested_ticker(
        self, hrepo: HoldingRepository, trepo: TransactionRepository, repo: AssetRepository
    ) -> None:
        # Com mais de uma posicao ativa, get() filtra pelo ticker pedido
        # (mata o mutante "SELECT sem WHERE devolve a primeira linha").
        repo.add("ITUB4", Decimal("0.1"))
        repo.add("VALE3", Decimal("0.1"))
        trepo.add_buy("ITUB4", D1, Decimal("20"), Decimal("25"))
        trepo.add_buy("VALE3", D1, Decimal("10"), Decimal("60"))
        h = hrepo.get("VALE3")
        assert h is not None
        assert h.ticker == "VALE3"
        assert h.total_shares == Decimal("10")
        assert h.total_invested == Decimal("600")

    def test_asset_type_exposed(
        self, hrepo: HoldingRepository, trepo: TransactionRepository, repo: AssetRepository
    ) -> None:
        repo.add("MXRF11", Decimal("0.05"), asset_type=AssetType.FII)
        trepo.add_buy("MXRF11", D1, Decimal("100"), Decimal("10"))
        h = hrepo.get("MXRF11")
        assert h is not None
        assert h.asset_type is AssetType.FII


class TestFixedIncomeConvention:
    def test_application_as_single_unit(
        self, hrepo: HoldingRepository, trepo: TransactionRepository, repo: AssetRepository
    ) -> None:
        # Convencao: aplicacao = BUY shares=1, unit_price=valor aplicado.
        repo.add(
            "CDB-XP-2027",
            Decimal("0.1"),
            asset_type=AssetType.CDB,
            issuer="XP Investimentos",
            indexer=None,
            is_prefixed=True,
            daily_liquidity=False,
            purchase_date=D1,
            maturity_date=datetime(2027, 4, 1, tzinfo=UTC),
        )
        trepo.add_buy("CDB-XP-2027", D1, Decimal("1"), Decimal("5000"))
        h = hrepo.get("CDB-XP-2027")
        assert h is not None
        assert h.total_shares == Decimal("1")
        assert h.total_invested == Decimal("5000")

        # Resgate total: SELL shares=1 — a posicao some da view.
        trepo.add_sale("CDB-XP-2027", D2, Decimal("1"), Decimal("5310"))
        assert hrepo.get("CDB-XP-2027") is None
