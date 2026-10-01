"""Tests for the aporte suggestion engine (issue #23)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from bogle.domain.assets import AssetType
from bogle.domain.errors import MissingPriceError, ValidationError
from bogle.position import PortfolioSummary, Position
from bogle.rebalancing import suggest_allocation

_ZERO = Decimal("0")


def make_position(
    ticker: str,
    price: str | None,
    value: str | None,
    target: str,
    asset_type: AssetType = AssetType.ETF,
    total: str = "0",
) -> Position:
    market_value = Decimal(value) if value is not None else None
    total_value = Decimal(total)
    current = market_value / total_value if market_value is not None and total_value > 0 else None
    return Position(
        ticker=ticker,
        asset_type=asset_type,
        quantity=Decimal("1"),
        total_invested=market_value if market_value is not None else Decimal("1"),
        target_weight=Decimal(target),
        dividends=_ZERO,
        price=Decimal(price) if price is not None else None,
        market_value=market_value,
        current_weight=current,
        drift=current - Decimal(target) if current is not None else None,
    )


def make_pending(
    ticker: str,
    price: str | None,
    target: str,
    asset_type: AssetType = AssetType.ETF,
    total: str = "0",
) -> Position:
    """A target with no position behind it, as ``get_allocation_summary`` builds it.

    Quantity zero and market value zero is what says "only an intention" — and
    ``price=None`` is the one the provider could not quote.
    """
    total_value = Decimal(total)
    current = _ZERO if total_value > 0 else None
    return Position(
        ticker=ticker,
        asset_type=asset_type,
        quantity=_ZERO,
        total_invested=_ZERO,
        target_weight=Decimal(target),
        dividends=_ZERO,
        price=Decimal(price) if price is not None else None,
        market_value=_ZERO,
        current_weight=current,
        drift=-Decimal(target) if current is not None else None,
    )


def make_summary(*positions: Position) -> PortfolioSummary:
    total = sum((p.market_value for p in positions if p.market_value is not None), _ZERO)
    invested = sum((p.total_invested for p in positions if p.total_invested is not None), _ZERO)
    return PortfolioSummary(list(positions), total, invested, _ZERO, _ZERO)


def total_drift(summary: PortfolioSummary) -> Decimal:
    return sum(
        (abs(p.drift) for p in summary.positions if p.drift is not None),
        _ZERO,
    )


class TestIssueExample:
    """Carteira 64/36 com targets 70/30 e aporte de 10.000 (exemplo da issue)."""

    def summary(self) -> PortfolioSummary:
        return make_summary(
            make_position("VWRA11", "100", "64000", "0.70", total="100000"),
            make_position("B5P211", "90", "36000", "0.30", total="100000"),
        )

    def test_everything_goes_to_the_laggard(self) -> None:
        suggestion = suggest_allocation(self.summary(), Decimal("10000"))
        vwra = next(item for item in suggestion.items if item.ticker == "VWRA11")
        b5p2 = next(item for item in suggestion.items if item.ticker == "B5P211")
        assert vwra.quantity == Decimal("100")  # 10000 / 100
        assert vwra.effective_cost == Decimal("10000")
        assert b5p2.effective_cost == _ZERO
        assert suggestion.total_allocated == Decimal("10000")  # o aporte inteiro vira cota

    def test_weights_move_toward_targets(self) -> None:
        suggestion = suggest_allocation(self.summary(), Decimal("10000"))
        vwra = next(item for item in suggestion.items if item.ticker == "VWRA11")
        b5p2 = next(item for item in suggestion.items if item.ticker == "B5P211")
        # 74k/110k e 36k/110k: mais perto de 70/30 do que 64/36.
        assert Decimal("0.64") < vwra.weight_after < Decimal("0.70")
        assert Decimal("0.30") < b5p2.weight_after < Decimal("0.36")

    def test_aggregate_drift_shrinks(self) -> None:
        summary = self.summary()
        suggestion = suggest_allocation(summary, Decimal("10000"))
        drift_after = sum(
            (abs(item.weight_after - item.target_weight) for item in suggestion.items),
            _ZERO,
        )
        assert drift_after < total_drift(summary)


class TestAllocationBranches:
    def test_needs_covered_leaves_leftover_in_cash(self) -> None:
        # future = 120k: A precisa de 18k, B de nada -> sobram 2k em caixa.
        summary = make_summary(
            make_position("AAAA11", "1", "30000", "0.40", total="100000"),
            make_position("BBBB11", "1", "70000", "0.30", total="100000"),
        )
        suggestion = suggest_allocation(summary, Decimal("20000"))
        aaaa = next(item for item in suggestion.items if item.ticker == "AAAA11")
        assert aaaa.effective_cost == Decimal("18000")
        assert aaaa.weight_after == Decimal("0.40")  # exatamente no target do patrimonio futuro
        assert suggestion.leftover == Decimal("1994.60")  # 2k menos a taxa B3 dos 18k

    def test_proportional_when_needs_exceed_amount(self) -> None:
        # future = 120: A precisa 20, B precisa 6, total 26 > 20 -> proporcional.
        summary = make_summary(
            make_position("AAAA11", "9", "40", "0.50", total="100"),
            make_position("BBBB11", "2", "30", "0.30", total="100"),
            make_position("CCCC11", "1", "30", "0.05", total="100"),  # acima do target, nao recebe
        )
        suggestion = suggest_allocation(summary, Decimal("20"))
        aaaa = next(item for item in suggestion.items if item.ticker == "AAAA11")
        bbbb = next(item for item in suggestion.items if item.ticker == "BBBB11")
        # A: alocacao 15.38 -> 1 cota de 9; B: alocacao 4.61 -> 2 cotas de 2,
        # residual compra +1 cota de B (limite = necessidade de 6).
        assert aaaa.quantity == Decimal("1")
        assert bbbb.quantity == Decimal("3")
        assert bbbb.effective_cost == Decimal("6")
        assert bbbb.weight_after == Decimal("0.3")  # nunca passa do target
        assert suggestion.total_allocated == Decimal("15")
        assert suggestion.leftover == Decimal("5")

    def test_portfolio_at_target_stays_at_target(self) -> None:
        summary = make_summary(
            make_position("AAAA11", "1", "70000", "0.70", total="100000"),
            make_position("BBBB11", "1", "30000", "0.30", total="100000"),
        )
        suggestion = suggest_allocation(summary, Decimal("1000"))
        # Carteira nos targets: o aporte inteiro entra 70/30 e os pesos nao mudam.
        assert suggestion.total_allocated == Decimal("1000")
        for item in suggestion.items:
            assert item.weight_after == item.target_weight


class TestFixedIncome:
    def test_private_fixed_income_gets_exact_value_and_warning(self) -> None:
        summary = make_summary(
            make_position("CDB01", "40", "40", "0.50", asset_type=AssetType.CDB, total="100"),
            make_position("BBBB11", "10", "60", "0.50", total="100"),
        )
        suggestion = suggest_allocation(summary, Decimal("20"))
        cdb = next(item for item in suggestion.items if item.ticker == "CDB01")
        assert cdb.quantity is None
        assert cdb.effective_cost == Decimal("20.00")  # future 120 * 0.5 - 40 = 20, sem floor
        assert suggestion.leftover == _ZERO
        assert any("CDB01" in w and "novo contrato" in w for w in suggestion.warnings)

    def test_tesouro_gets_exact_value_without_warning(self) -> None:
        summary = make_summary(
            make_position("TESOURO SELIC 2029", "100", "40", "0.50", asset_type=AssetType.TESOURO, total="100"),
            make_position("BBBB11", "10", "60", "0.50", total="100"),
        )
        suggestion = suggest_allocation(summary, Decimal("20"))
        tesouro = next(item for item in suggestion.items if item.ticker == "TESOURO SELIC 2029")
        assert tesouro.quantity is None
        assert tesouro.effective_cost == Decimal("20.00")
        assert suggestion.warnings == []


class TestTargetsWithoutAPosition:
    """Um ativo que so existe como target concorre ao aporte como qualquer outro."""

    def summary(self) -> PortfolioSummary:
        # Dois ativos comprados e ja nos seus targets (600/400 de 1000, para 50% e
        # 30%), e um terceiro cadastrado com 20% que nunca foi comprado — a
        # situacao em que a sugestao nao dizia nada sobre o terceiro.
        return make_summary(
            make_position("AAAA11", "10", "600", "0.50", total="1000"),
            make_position("BBBB11", "10", "400", "0.30", total="1000"),
            make_pending("CCCC11", "10", "0.20", total="1000"),
        )

    def test_the_untouched_ticker_gets_the_money(self) -> None:
        suggestion = suggest_allocation(self.summary(), Decimal("200"))
        cccc = next(item for item in suggestion.items if item.ticker == "CCCC11")
        # future = 1200; precisa de 1200 * 0.2 = 240, e e o unico abaixo do target.
        assert cccc.quantity == Decimal("20")
        assert cccc.effective_cost == Decimal("200")
        assert suggestion.total_allocated == Decimal("200")

    def test_it_reports_where_it_starts_and_where_it_lands(self) -> None:
        suggestion = suggest_allocation(self.summary(), Decimal("200"))
        cccc = next(item for item in suggestion.items if item.ticker == "CCCC11")
        assert cccc.current_weight == _ZERO
        assert cccc.weight_after == Decimal("200") / Decimal("1200")
        # Ainda falta chegar ao target: um aporte de 200 nao cobre os 240.
        assert cccc.drift_after < _ZERO
        assert cccc.drift_after == cccc.weight_after - Decimal("0.20")

    def test_the_holders_are_not_disturbed(self) -> None:
        suggestion = suggest_allocation(self.summary(), Decimal("200"))
        # Quem ja esta no target (ou acima) apenas nao recebe: politica no-sell.
        assert [item.effective_cost for item in suggestion.items if item.ticker != "CCCC11"] == [_ZERO, _ZERO]

    def test_a_portfolio_that_is_only_targets_still_works(self) -> None:
        # O primeiro aporte de quem acabou de cadastrar a carteira: nada comprado,
        # patrimonio zero, e a sugestao e a carteira inteira.
        summary = make_summary(
            make_pending("AAAA11", "10", "0.70"),
            make_pending("BBBB11", "20", "0.30"),
        )
        suggestion = suggest_allocation(summary, Decimal("1000"))
        costs = {item.ticker: item.effective_cost for item in suggestion.items}
        assert costs == {"AAAA11": Decimal("700"), "BBBB11": Decimal("300")}
        # Peso atual nao existe sobre patrimonio zero; o peso final existe.
        assert all(item.current_weight is None for item in suggestion.items)
        assert {item.weight_after for item in suggestion.items} == {Decimal("0.7"), Decimal("0.3")}

    def test_an_unquotable_target_sits_this_one_out_with_a_warning(self) -> None:
        # Um ticker que o provedor nao cota nao pode derrubar a tela inteira: sem
        # preco nao da para dizer quantas cotas o dinheiro compra, e so isso.
        summary = make_summary(
            make_position("AAAA11", "10", "600", "0.50", total="600"),
            make_pending("XPTO11", None, "0.50", total="600"),
        )
        suggestion = suggest_allocation(summary, Decimal("100"))
        assert [item.ticker for item in suggestion.items] == ["AAAA11"]
        assert any("XPTO11" in warning and "Sem cotacao" in warning for warning in suggestion.warnings)
        assert suggestion.leftover == Decimal("100")  # o dinheiro fica em caixa

    def test_the_unquoted_target_comes_back_as_data(self) -> None:
        # So o texto do aviso nao basta: o frontend precisa de uma linha onde
        # oferecer o preco que traz o ticker de volta.
        summary = make_summary(
            make_position("AAAA11", "10", "600", "0.50", total="600"),
            make_pending("XPTO11", None, "0.50", total="600"),
        )
        [target] = suggest_allocation(summary, Decimal("100")).unquoted
        assert target.ticker == "XPTO11"
        assert target.asset_type is AssetType.ETF
        assert target.current_weight == _ZERO
        assert target.target_weight == Decimal("0.50")
        assert target.weight_after == _ZERO
        assert target.drift_after == Decimal("-0.50")  # o target inteiro, em aberto

    def test_the_warning_does_not_name_one_frontend(self) -> None:
        # O aviso aparece na CLI e na TUI: dizer "--price" dentro da interface
        # seria mandar fechar a tela para fazer o que a tecla p faz.
        summary = make_summary(
            make_position("AAAA11", "10", "600", "0.50", total="600"),
            make_pending("XPTO11", None, "0.50", total="600"),
        )
        [warning] = suggest_allocation(summary, Decimal("100")).warnings
        assert "--price" not in warning
        assert "CLI" not in warning

    def test_an_open_position_without_a_price_still_aborts(self) -> None:
        # A regra que nao muda: sem cotacao de algo que existe, todos os pesos
        # estao errados, e uma sugestao errada e pior que nenhuma.
        summary = make_summary(
            make_position("AAAA11", None, None, "0.50", total="600"),
            make_pending("BBBB11", "10", "0.50", total="600"),
        )
        with pytest.raises(MissingPriceError, match="AAAA11"):
            suggest_allocation(summary, Decimal("100"))

    def test_an_informed_price_brings_it_back_in(self) -> None:
        summary = make_summary(
            make_position("AAAA11", "10", "600", "0.50", total="600"),
            make_pending("XPTO11", None, "0.50", total="600"),
        )
        suggestion = suggest_allocation(summary, Decimal("100"), prices={"XPTO11": Decimal("25")})
        xpto = next(item for item in suggestion.items if item.ticker == "XPTO11")
        assert xpto.quantity == Decimal("4")
        assert xpto.effective_cost == Decimal("100")
        assert xpto.is_manual_price
        assert not any("Sem cotacao" in warning for warning in suggestion.warnings)
        assert suggestion.unquoted == []


class TestB3Fee:
    def test_the_fee_closes_the_account_of_a_real_purchase(self) -> None:
        # 102 cotas de MUND11 a 100.01: 10,201.02 de papel, 0.03% de taxa.
        summary = make_summary(make_pending("MUND11", "100.01", "1"))
        suggestion = suggest_allocation(summary, Decimal("10205"))
        assert suggestion.items[0].quantity == Decimal("102")
        assert suggestion.total_allocated == Decimal("10201.02")
        assert suggestion.estimated_fees == Decimal("3.06")
        assert suggestion.total_with_fees == Decimal("10204.08")
        assert suggestion.leftover == Decimal("0.92")

    def test_fixed_income_pays_no_fee(self) -> None:
        summary = make_summary(
            make_position("CDB01", "40", "40", "0.50", asset_type=AssetType.CDB, total="100"),
            make_position("TESOURO SELIC 2029", "100", "60", "0.50", asset_type=AssetType.TESOURO, total="100"),
        )
        suggestion = suggest_allocation(summary, Decimal("20"))
        assert suggestion.total_allocated == Decimal("20.00")
        assert suggestion.estimated_fees == _ZERO
        assert suggestion.leftover == _ZERO

    def test_only_the_variable_income_share_is_charged(self) -> None:
        summary = make_summary(
            make_pending("AAAA11", "10", "0.50", asset_type=AssetType.FII),
            make_pending("CDB01", "1", "0.50", asset_type=AssetType.CDB),
        )
        suggestion = suggest_allocation(summary, Decimal("2000"))
        assert suggestion.total_allocated == Decimal("2000")
        assert suggestion.estimated_fees == Decimal("0.30")  # sobre os 1000 do FII, nao sobre os 2000

    def test_a_fee_that_does_not_fit_leaves_the_cash_negative(self) -> None:
        # O floor nao deixou sobra nenhuma: a taxa sai de um dinheiro que nao ha,
        # e a sobra negativa e o tamanho do que falta.
        summary = make_summary(make_pending("AAAA11", "10", "1"))
        suggestion = suggest_allocation(summary, Decimal("1000"))
        assert suggestion.total_allocated == Decimal("1000")
        assert suggestion.leftover == Decimal("-0.30")

    def test_the_fee_rounds_half_up_to_the_cent(self) -> None:
        summary = make_summary(make_pending("AAAA11", "50", "1"))
        suggestion = suggest_allocation(summary, Decimal("50"))
        assert suggestion.estimated_fees == Decimal("0.02")  # 0.015


class TestInvariants:
    @pytest.mark.parametrize("amount", ["10", "97", "1000", "12345.67"])
    def test_never_allocates_more_than_amount(self, amount: str) -> None:
        summary = make_summary(
            make_position("AAAA11", "7", "40", "0.50", total="100"),
            make_position("BBBB11", "13", "30", "0.30", total="100"),
            make_position("CDB01", "30", "30", "0.20", asset_type=AssetType.CDB, total="100"),
        )
        suggestion = suggest_allocation(summary, Decimal(amount))
        assert suggestion.total_allocated <= Decimal(amount)
        assert suggestion.total_allocated + suggestion.estimated_fees + suggestion.leftover == Decimal(amount)
        # Quem recebe aporte nunca passa do target; quem ja estava acima (no-sell) segue acima.
        for item in suggestion.items:
            if item.effective_cost > 0:
                assert item.weight_after <= item.target_weight + Decimal("1E-12")

    def test_items_sorted_by_cost_desc(self) -> None:
        summary = make_summary(
            make_position("AAAA11", "1", "40", "0.50", total="100"),
            make_position("BBBB11", "1", "30", "0.30", total="100"),
        )
        suggestion = suggest_allocation(summary, Decimal("20"))
        costs = [item.effective_cost for item in suggestion.items]
        assert costs == sorted(costs, reverse=True)


class TestErrors:
    def test_missing_price_aborts(self) -> None:
        summary = make_summary(
            make_position("AAAA11", "1", "50", "0.50", total="50"),
            make_position("CDB01", None, None, "0.50", asset_type=AssetType.CDB, total="50"),
        )
        with pytest.raises(MissingPriceError, match="CDB01"):
            suggest_allocation(summary, Decimal("100"))

    def test_non_positive_amount(self) -> None:
        summary = make_summary(make_position("AAAA11", "1", "50", "0.50", total="50"))
        with pytest.raises(ValidationError, match="positivo"):
            suggest_allocation(summary, Decimal("0"))

    def test_empty_portfolio(self) -> None:
        with pytest.raises(ValidationError, match="Nenhuma posicao"):
            suggest_allocation(make_summary(), Decimal("100"))


class TestManualPrice:
    """O preco de uma ordem limitada: vale na compra, nao na marcacao."""

    def summary(self) -> PortfolioSummary:
        return make_summary(
            make_position("VWRA11", "100", "6400", "0.70", total="10000"),
            make_position("B5P211", "90", "3600", "0.30", total="10000"),
        )

    def test_a_lower_price_buys_more_shares_for_the_same_money(self) -> None:
        market = suggest_allocation(self.summary(), Decimal("1000"))
        limited = suggest_allocation(self.summary(), Decimal("1000"), prices={"VWRA11": Decimal("80")})
        at_market = next(item for item in market.items if item.ticker == "VWRA11")
        at_limit = next(item for item in limited.items if item.ticker == "VWRA11")
        assert at_market.quantity == Decimal("10")  # 1000 / 100
        assert at_limit.quantity == Decimal("12")  # 1000 / 80, cotas inteiras
        assert at_limit.effective_cost == Decimal("960")
        assert at_limit.price == Decimal("80")
        assert at_limit.quoted_price == Decimal("100")
        assert at_limit.is_manual_price

    def test_the_split_itself_does_not_move(self) -> None:
        # O ponto do escopo "so na compra": quanto cada ticker recebe sai do valor
        # de mercado, entao dizer que se quer pagar menos nao redireciona dinheiro.
        market = suggest_allocation(self.summary(), Decimal("1000"))
        limited = suggest_allocation(self.summary(), Decimal("1000"), prices={"VWRA11": Decimal("80")})
        assert [item.allocation for item in limited.items] == [item.allocation for item in market.items]

    def test_the_untouched_ticker_keeps_the_quote(self) -> None:
        suggestion = suggest_allocation(self.summary(), Decimal("1000"), prices={"VWRA11": Decimal("80")})
        other = next(item for item in suggestion.items if item.ticker == "B5P211")
        assert other.price == Decimal("90")
        assert not other.is_manual_price

    def test_a_warning_says_the_numbers_assume_the_order_executes(self) -> None:
        suggestion = suggest_allocation(self.summary(), Decimal("1000"), prices={"VWRA11": Decimal("80")})
        assert any("VWRA11" in warning and "executa" in warning for warning in suggestion.warnings)

    def test_the_ticker_is_matched_case_insensitively(self) -> None:
        suggestion = suggest_allocation(self.summary(), Decimal("1000"), prices={"vwra11": Decimal("80")})
        assert next(item for item in suggestion.items if item.ticker == "VWRA11").price == Decimal("80")

    def test_no_prices_leaves_everything_as_it_was(self) -> None:
        suggestion = suggest_allocation(self.summary(), Decimal("1000"), prices={})
        assert [item.price for item in suggestion.items] == [Decimal("100"), Decimal("90")]
        assert not any(item.is_manual_price for item in suggestion.items)
        assert suggestion.warnings == []

    def test_a_ticker_outside_the_portfolio_is_refused(self) -> None:
        # Um ticker errado calado devolveria a sugestao a preco de mercado, com o
        # usuario lendo ela como se fosse ao preco dele.
        with pytest.raises(ValidationError, match="fora da carteira: XPTO11"):
            suggest_allocation(self.summary(), Decimal("1000"), prices={"XPTO11": Decimal("80")})

    def test_fixed_income_is_refused(self) -> None:
        summary = make_summary(
            make_position("AAAA11", "1", "50", "0.50", total="100"),
            make_position("CDB01", "50", "50", "0.50", asset_type=AssetType.CDB, total="100"),
        )
        with pytest.raises(ValidationError, match="renda variavel"):
            suggest_allocation(summary, Decimal("100"), prices={"CDB01": Decimal("49")})

    def test_a_non_positive_price_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="positivo"):
            suggest_allocation(self.summary(), Decimal("1000"), prices={"VWRA11": Decimal("0")})
