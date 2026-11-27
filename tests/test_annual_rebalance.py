"""Tests for the annual rebalance of the retirement phase (issue #81): the phase, the
sales back to the band, the IR of each, the purchases with the net — plus what
:mod:`bogle.annual_rebalance` reads from and writes to the database.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timedelta
from typing import Any

import psycopg
import pytest
from psycopg.rows import DictRow

from bogle.annual_rebalance import load_annual_state, plan_annual_rebalance, record_annual_rebalance
from bogle.domain.assets import AssetType
from bogle.domain.errors import MissingPriceError, ValidationError
from bogle.position import PortfolioSummary, Position
from bogle.rebalancing import (
    AnnualRebalance,
    Recommendation,
    SaleReason,
    annual_rebalance_notice,
    annual_rebalance_phase,
    annual_rebalance_refusal,
    annual_rebalance_state,
    retirement_phase,
    suggest_annual_rebalance,
)
from bogle.repositories.assets import AssetRepository
from bogle.repositories.transactions import TransactionRepository
from bogle.settings import (
    BIRTH_YEAR,
    LAST_ANNUAL_REBALANCE_YEAR,
    LAST_REBALANCE_DATE,
    RETIREMENT_AGE,
    WEIGHT_DRIFT_THRESHOLD,
    get_setting,
    set_value,
)
from tests.test_withdraw import D, by_ticker, position, summary_of

YEAR = 2061
TODAY = date(YEAR, 1, 5)
THRESHOLD = D("0.05")


def plan(summary: PortfolioSummary, exiting: set[str] | None = None, **kwargs: Any) -> AnnualRebalance:
    options: dict[str, Any] = {"year": YEAR, "threshold": THRESHOLD, "today": TODAY}
    options.update(kwargs)
    return suggest_annual_rebalance(summary, exiting or set(), **options)


def drifted_portfolio() -> PortfolioSummary:
    """R$100.000 em 2061: MUND11 15 p.p. acima do alvo, NB1011 (em saida) 5 p.p.
    acima, B5P211 20 p.p. abaixo."""
    return summary_of(
        position("MUND11", price="100", quantity="450", target="0.30", average="80"),
        position("NB1011", price="50", quantity="200", target="0.05", average="40"),
        position("B5P211", price="100", quantity="450", target="0.65", average="90"),
    )


def pending_target(ticker: str, *, price: str | None, target: str) -> Position:
    """Um alvo sem posicao, como :func:`~bogle.position.get_allocation_summary` traz."""
    return Position(
        ticker=ticker,
        asset_type=AssetType.ETF,
        quantity=D("0"),
        total_invested=None,
        target_weight=D(target),
        dividends=D("0"),
        price=D(price) if price is not None else None,
        market_value=D("0") if price is not None else None,
        current_weight=D("0") if price is not None else None,
        drift=-D(target) if price is not None else None,
    )


class TestPhase:
    def test_the_annual_rebalance_starts_the_year_after_the_retirement_age(self) -> None:
        # 2000 + 60: a aposentadoria e em 2060, a primeira venda anual em 01/01/2061.
        assert annual_rebalance_phase(2000, 60, 2060) is False
        assert annual_rebalance_phase(2000, 60, 2061) is True
        assert annual_rebalance_phase(None, 60, 2061) is None

    def test_the_withdrawal_phase_is_untouched(self) -> None:
        # O saque continua com `>=`: em 2060 ja e aposentadoria para ele.
        assert retirement_phase(2000, 60, 2060) is True

    def test_before_the_phase_the_state_says_when_it_starts(self) -> None:
        state = annual_rebalance_state(2000, 60, None, 2040)
        assert state is not None
        assert (state.year, state.in_phase, state.pending) == (2061, False, False)

    def test_pending_until_the_year_is_recorded(self) -> None:
        pending = annual_rebalance_state(2000, 60, None, 2061)
        done = annual_rebalance_state(2000, 60, 2061, 2061)
        assert pending is not None and pending.pending
        assert done is not None and done.done and not done.pending

    def test_once_a_year(self) -> None:
        # O de 2061 feito nao vale para 2062.
        state = annual_rebalance_state(2000, 60, 2061, 2062)
        assert state is not None
        assert state.pending
        assert state.year == 2062

    def test_without_birth_year_there_is_no_state(self) -> None:
        assert annual_rebalance_state(None, 60, None, 2061) is None

    def test_the_notice_text(self) -> None:
        assert (
            annual_rebalance_notice(annual_rebalance_state(2000, 60, None, 2061))
            == "rebalanceamento anual de 2061 pendente (bogle rebalance)"
        )
        assert annual_rebalance_notice(annual_rebalance_state(2000, 60, 2061, 2061)) is None
        assert annual_rebalance_notice(annual_rebalance_state(2000, 60, None, 2060)) is None
        assert annual_rebalance_notice(None) is None

    def test_the_refusals(self) -> None:
        assert annual_rebalance_refusal(2000, 60, 2061) is None
        assert annual_rebalance_refusal(2000, 60, 2060) == (
            "Antes da aposentadoria: o rebalanceamento anual começa em 2061."
        )
        refusal = annual_rebalance_refusal(None, 60, 2061)
        assert refusal is not None and refusal.startswith("Sem ano de nascimento")


class TestSales:
    def test_out_of_the_band_sells_to_the_edge_and_exiting_to_the_target(self) -> None:
        # Primeira passada, sobre os 100.000: MUND11 vende 100 cotas (ate 35%) e
        # NB1011 100 (ate 5%), com 450 de IR. Sobre os 99.550 que ficam, as vendas
        # sobem para 102 e 101 cotas, e o IR para 457,50; mais uma passada nao muda
        # nada. Sem isso, o MUND11 terminaria em 35,16% do patrimonio pos-IR.
        rebalance = plan(drifted_portfolio(), {"NB1011"})
        sales = by_ticker(rebalance.sales)
        assert sales["MUND11"].quantity == D("102")
        assert sales["MUND11"].value == D("10200")
        assert sales["MUND11"].sale_reason is SaleReason.DRIFT
        assert sales["NB1011"].quantity == D("101")
        assert sales["NB1011"].value == D("5050")
        assert sales["NB1011"].sale_reason is SaleReason.EXITING
        # Pesos sobre 99.542,50 (100.000 - IR): dentro da faixa, e no alvo.
        assert sales["MUND11"].weight_after <= D("0.35")
        assert sales["NB1011"].weight_after <= D("0.05")
        assert "NB1011 em saída na trajetória: vendido até o alvo" in rebalance.warnings

    def test_the_smallest_sale_that_gets_back_to_the_band(self) -> None:
        # Uma cota a menos e o MUND11 ficaria fora da faixa.
        rebalance = plan(drifted_portfolio(), {"NB1011"})
        mund = by_ticker(rebalance.sales)["MUND11"]
        base = D("100000") - rebalance.total_tax
        assert (D("45000") - (mund.value - D("100"))) / base > D("0.35")

    def test_within_the_tolerance_nothing_sells(self) -> None:
        summary = summary_of(
            position("MUND11", price="100", quantity="340", target="0.30", average="80"),
            position("B5P211", price="100", quantity="660", target="0.70", average="90"),
        )
        rebalance = plan(summary)
        assert rebalance.sales == []
        assert rebalance.purchases == []
        assert [item.recommendation for item in rebalance.items] == [Recommendation.HOLD, Recommendation.HOLD]
        assert rebalance.warnings == ["Nada a vender: nenhum ticker acima da faixa"]
        assert rebalance.leftover == D("0")

    def test_exactly_at_the_tolerance_is_still_inside(self) -> None:
        summary = summary_of(
            position("MUND11", price="100", quantity="350", target="0.30", average="80"),
            position("B5P211", price="100", quantity="650", target="0.70", average="90"),
        )
        assert plan(summary).sales == []

    def test_an_exiting_ticker_sells_with_no_tolerance(self) -> None:
        # NB1011 a 1 p.p. do alvo: sem a saida da trajetoria ficaria, com ela vende.
        summary = summary_of(
            position("MUND11", price="100", quantity="300", target="0.30", average="80"),
            position("NB1011", price="50", quantity="120", target="0.05", average="40"),
            position("B5P211", price="100", quantity="640", target="0.65", average="90"),
        )
        assert plan(summary).sales == []
        [nb] = plan(summary, {"NB1011"}).sales
        assert nb.ticker == "NB1011"
        assert nb.weight_after <= D("0.05")

    def test_an_exiting_ticker_at_or_below_its_target_stays(self) -> None:
        summary = summary_of(
            position("MUND11", price="100", quantity="320", target="0.30", average="80"),
            position("NB1011", price="50", quantity="80", target="0.05", average="40"),
            position("B5P211", price="100", quantity="640", target="0.65", average="90"),
        )
        assert plan(summary, {"NB1011"}).sales == []

    def test_an_exiting_ticker_at_zero_sells_everything_it_has(self) -> None:
        summary = summary_of(
            position("MUND11", price="100", quantity="300", target="0.30", average="80"),
            position("NB1011", price="33", quantity="100", target="0", average="40"),
            position("B5P211", price="100", quantity="667", target="0.70", average="90"),
        )
        [nb] = plan(summary, {"NB1011"}).sales
        assert nb.quantity == D("100")  # o que tem, nem uma cota a mais
        assert nb.weight_after == D("0")
        assert nb.income_tax == D("0")  # vendido com prejuizo

    def test_whole_shares_round_up(self) -> None:
        # 45.000 - 35% de 100.000 = 10.000 a 30 por cota: 333,3 -> 334.
        summary = summary_of(
            position("MUND11", price="30", quantity="1500", target="0.30", average="30"),
            position("B5P211", price="100", quantity="550", target="0.70", average="100"),
        )
        [mund] = plan(summary).sales
        assert mund.quantity == D("334")
        assert mund.income_tax == D("0")  # sem ganho, base fixa em 100.000

    def test_fixed_income_sells_exact_cents(self) -> None:
        summary = summary_of(
            position(
                "TESOURO-IPCA-2035",
                price="3000",
                quantity="15.123",
                target="0.30",
                asset_type=AssetType.TESOURO,
                average="3000",
            ),
            position("B5P211", price="100", quantity="546.31", target="0.70", average="100", asset_type=AssetType.ETF),
        )
        rebalance = plan(summary, held_since={"TESOURO-IPCA-2035": TODAY - timedelta(days=900)})
        [tesouro] = rebalance.sales
        assert tesouro.quantity is None
        assert tesouro.value == tesouro.value.quantize(D("0.01"))
        assert tesouro.weight_after <= D("0.35")

    def test_a_private_fixed_income_sale_asks_to_check_the_liquidity(self) -> None:
        summary = summary_of(
            position(
                "CDB-XP-2027", price="1000", quantity="45", target="0.30", asset_type=AssetType.CDB, average="900"
            ),
            position("MUND11", price="100", quantity="550", target="0.70", average="80"),
        )
        rebalance = plan(summary, held_since={"CDB-XP-2027": date(2050, 1, 1)})
        assert "CDB-XP-2027 é renda fixa privada: confira a liquidez antes de vender" in rebalance.warnings


class TestIncomeTax:
    def test_tax_per_sale_and_total(self) -> None:
        rebalance = plan(drifted_portfolio(), {"NB1011"})
        sales = by_ticker(rebalance.sales)
        assert sales["MUND11"].income_tax == D("306.00")  # 102 x (100 - 80) x 15%
        assert sales["NB1011"].income_tax == D("151.50")  # 101 x (50 - 40) x 15%
        assert rebalance.total_sold == D("15250")
        assert rebalance.total_tax == D("457.50")
        assert rebalance.available == D("14792.50")

    def test_stocks_count_the_month_for_the_exemption(self) -> None:
        summary = summary_of(
            position("PETR4", price="40", quantity="1000", target="0.30", average="30", asset_type=AssetType.STOCK),
            position("B5P211", price="100", quantity="600", target="0.70", average="100"),
        )
        exempt = plan(summary).sales[0]
        taxed = plan(summary, month_stock_sales=D("19000")).sales[0]
        assert exempt.income_tax == D("0")
        assert taxed.income_tax is not None and taxed.income_tax > 0

    def test_without_an_average_cost_the_tax_is_unknown_and_said(self) -> None:
        summary = summary_of(
            position("MUND11", price="100", quantity="450", target="0.30"),
            position("B5P211", price="100", quantity="550", target="0.70", average="90"),
        )
        rebalance = plan(summary)
        [mund] = rebalance.sales
        assert mund.income_tax is None
        assert rebalance.total_tax == D("0")
        assert rebalance.available == rebalance.total_sold
        assert "Sem custo médio de MUND11: IR não estimado" in rebalance.warnings


class TestPurchases:
    def test_the_net_buys_what_is_below_target(self) -> None:
        # 14.792,50 liquidos, sobre 99.542,50 depois do IR: B5P211 precisa de
        # 19.702,63 e leva 147 cotas; a taxa da B3 e a do aporte.
        rebalance = plan(drifted_portfolio(), {"NB1011"})
        [b5] = rebalance.purchases
        assert b5.ticker == "B5P211"
        assert b5.recommendation is Recommendation.BUY
        assert b5.quantity == D("147")
        assert b5.value == D("14700")
        assert b5.income_tax is None
        assert rebalance.estimated_fees == D("4.71")  # 14.700 x 0,032%, para cima
        assert rebalance.leftover == D("87.79")
        assert rebalance.available == rebalance.total_bought + rebalance.estimated_fees + rebalance.leftover

    @pytest.mark.parametrize(
        "quantities",
        [
            ("450", "200", "450"),
            ("600", "100", "300"),
            ("700", "250", "50"),
            ("300", "900", "250"),
        ],
    )
    def test_no_purchase_goes_past_a_target(self, quantities: tuple[str, str, str]) -> None:
        mund, nb, b5 = quantities
        total = str(D(mund) * 100 + D(nb) * 50 + D(b5) * 100)
        summary = summary_of(
            position("MUND11", price="100", quantity=mund, target="0.30", average="80", total=total),
            position("NB1011", price="50", quantity=nb, target="0.05", average="40", total=total),
            position("B5P211", price="100", quantity=b5, target="0.65", average="90", total=total),
            pending_target("VWRA11", price="120", target="0"),
        )
        rebalance = plan(summary, {"NB1011"})
        for item in rebalance.purchases:
            assert item.weight_after <= item.target_weight, item.ticker
        for item in rebalance.sales:
            limit = item.target_weight if item.sale_reason is SaleReason.EXITING else item.target_weight + THRESHOLD
            assert item.weight_after <= limit, item.ticker

    def test_a_ticker_that_sells_buys_nothing_back(self) -> None:
        # O Tesouro vende ate o alvo, arredondado para cima no centavo: termina uma
        # fracao de centavo abaixo dele, e fica fora das compras de qualquer jeito.
        summary = summary_of(
            position(
                "TESOURO-IPCA-2035",
                price="3000",
                quantity="3.3333",
                target="0.05",
                asset_type=AssetType.TESOURO,
                average="3000",
                total="99999.9",
            ),
            position("B5P211", price="100", quantity="900", target="0.95", average="100", total="99999.9"),
        )
        rebalance = plan(summary, {"TESOURO-IPCA-2035"})
        assert [item.ticker for item in rebalance.sales] == ["TESOURO-IPCA-2035"]
        assert "TESOURO-IPCA-2035" not in by_ticker(rebalance.purchases)

    def test_a_target_with_no_position_receives_the_purchase(self) -> None:
        # A trajetoria trouxe o VWRA11 este ano: ele concorre como no aporte.
        summary = summary_of(
            position("MUND11", price="100", quantity="600", target="0.30", average="80"),
            position("B5P211", price="100", quantity="400", target="0.40", average="90"),
            pending_target("VWRA11", price="120", target="0.30"),
        )
        rebalance = plan(summary)
        bought = by_ticker(rebalance.purchases)
        assert "VWRA11" in bought
        assert bought["VWRA11"].current_weight == D("0")

    def test_a_target_the_provider_cannot_quote_sits_it_out(self) -> None:
        summary = summary_of(
            position("MUND11", price="100", quantity="600", target="0.30", average="80"),
            position("B5P211", price="100", quantity="400", target="0.40", average="90"),
            pending_target("VWRA11", price=None, target="0.30"),
        )
        rebalance = plan(summary)
        assert "VWRA11" not in by_ticker(rebalance.items)
        assert "Sem cotação para VWRA11" in rebalance.warnings

    def test_the_order_is_sales_purchases_then_the_rest(self) -> None:
        summary = summary_of(
            position("MUND11", price="100", quantity="450", target="0.30", average="80"),
            position("NB1011", price="50", quantity="200", target="0.05", average="40"),
            position("AAAA11", price="10", quantity="1000", target="0.10", average="10"),
            position("B5P211", price="100", quantity="350", target="0.55", average="90"),
        )
        rebalance = plan(summary, {"NB1011"})
        assert [(item.ticker, item.recommendation) for item in rebalance.items] == [
            ("MUND11", Recommendation.SELL),
            ("NB1011", Recommendation.SELL),
            ("B5P211", Recommendation.BUY),
            ("AAAA11", Recommendation.HOLD),
        ]
        [hold] = [item for item in rebalance.items if item.recommendation is Recommendation.HOLD]
        assert hold.quantity is None
        assert hold.value == D("0")


class TestRefusals:
    def test_an_empty_portfolio(self) -> None:
        with pytest.raises(ValidationError, match="Nenhuma posição aberta"):
            plan(summary_of())

    def test_every_open_position_must_be_priced(self) -> None:
        unpriced = Position(
            ticker="VWRA11",
            asset_type=AssetType.ETF,
            quantity=D("10"),
            total_invested=D("1000"),
            target_weight=D("0.1"),
            dividends=D("0"),
        )
        summary = summary_of(*drifted_portfolio().positions, unpriced)
        with pytest.raises(MissingPriceError):
            plan(summary)


# ---- O que se le do banco (plan_annual_rebalance) e o registro do ano


@pytest.fixture
def clean(conn: psycopg.Connection[DictRow]) -> Iterator[psycopg.Connection[DictRow]]:
    """``conn``, with the trajectory and the settings gone after the test too (see test_withdraw)."""
    yield conn
    conn.execute("TRUNCATE glide_path_points, user_settings")
    conn.commit()


class TestPlanAnnualRebalance:
    @pytest.fixture
    def seeded(self, clean: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch) -> None:
        repo = AssetRepository(clean)
        repo.add("MUND11", D("0.30"), asset_type=AssetType.ETF)
        repo.add("NB1011", D("0.05"), asset_type=AssetType.ETF)
        repo.add("B5P211", D("0.65"), asset_type=AssetType.ETF)
        trepo = TransactionRepository(clean)
        trepo.add_buy("MUND11", datetime(2040, 1, 5, 12), D("450"), D("80"))
        trepo.add_buy("NB1011", datetime(2040, 1, 5, 12), D("200"), D("40"))
        trepo.add_buy("B5P211", datetime(2040, 1, 5, 12), D("450"), D("90"))
        # A trajetoria leva o NB1011 a 0 no ultimo ponto: em saida.
        clean.execute(
            "INSERT INTO glide_path_points (age, ticker, target_weight) VALUES "
            "(39, 'MUND11', 0.70), (39, 'NB1011', 0.25), (39, 'B5P211', 0.05), "
            "(71, 'MUND11', 0.30), (71, 'B5P211', 0.70)"
        )
        set_value(clean, BIRTH_YEAR, 2000)
        set_value(clean, RETIREMENT_AGE, 60)
        clean.commit()
        summary = drifted_portfolio()
        monkeypatch.setattr("bogle.annual_rebalance.get_allocation_summary", lambda conn, dispatcher, **_: summary)

    def test_reads_the_exiting_tickers_and_the_threshold(
        self, seeded: None, clean: psycopg.Connection[DictRow]
    ) -> None:
        rebalance = plan_annual_rebalance(clean, None, today=TODAY)  # type: ignore[arg-type]
        assert rebalance.year == YEAR
        assert rebalance.threshold == D("0.05")
        sales = by_ticker(rebalance.sales)
        assert sales["NB1011"].sale_reason is SaleReason.EXITING
        assert sales["MUND11"].quantity == D("102")

    def test_the_threshold_setting_moves_the_band(self, seeded: None, clean: psycopg.Connection[DictRow]) -> None:
        set_value(clean, WEIGHT_DRIFT_THRESHOLD, D("0.10"))
        rebalance = plan_annual_rebalance(clean, None, today=TODAY)  # type: ignore[arg-type]
        mund = by_ticker(rebalance.sales)["MUND11"]
        assert mund.weight_after <= D("0.40")
        assert mund.weight_after > D("0.39")

    def test_before_the_phase_it_refuses(self, seeded: None, clean: psycopg.Connection[DictRow]) -> None:
        with pytest.raises(ValidationError, match="começa em 2061"):
            plan_annual_rebalance(clean, None, today=date(2060, 6, 1))  # type: ignore[arg-type]

    def test_without_birth_year_it_refuses(self, seeded: None, clean: psycopg.Connection[DictRow]) -> None:
        clean.execute("DELETE FROM user_settings WHERE key = 'birth_year'")
        clean.commit()
        with pytest.raises(ValidationError, match="Sem ano de nascimento"):
            plan_annual_rebalance(clean, None, today=TODAY)  # type: ignore[arg-type]

    def test_seeing_the_plan_records_nothing(self, seeded: None, clean: psycopg.Connection[DictRow]) -> None:
        plan_annual_rebalance(clean, None, today=TODAY)  # type: ignore[arg-type]
        assert get_setting(clean, LAST_ANNUAL_REBALANCE_YEAR) is None
        assert get_setting(clean, LAST_REBALANCE_DATE) is None
        assert len(TransactionRepository(clean).list()) == 3

    def test_recording_the_year_ends_the_pendency(self, seeded: None, clean: psycopg.Connection[DictRow]) -> None:
        state = load_annual_state(clean, TODAY)
        assert state is not None and state.pending
        assert record_annual_rebalance(clean, today=TODAY) == YEAR
        assert get_setting(clean, LAST_ANNUAL_REBALANCE_YEAR) == YEAR
        state = load_annual_state(clean, TODAY)
        assert state is not None and state.done
        # No ano seguinte volta a ficar pendente.
        next_year = load_annual_state(clean, date(YEAR + 1, 1, 2))
        assert next_year is not None and next_year.pending

    def test_recording_before_the_phase_is_refused(self, seeded: None, clean: psycopg.Connection[DictRow]) -> None:
        with pytest.raises(ValidationError, match="Antes da aposentadoria"):
            record_annual_rebalance(clean, today=date(2060, 6, 1))
        assert get_setting(clean, LAST_ANNUAL_REBALANCE_YEAR) is None
