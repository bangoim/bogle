"""Tests for the withdrawal suggestion (issue #80): the order of sale, whole shares
rounded up, the IR of each sale and the phase warning — plus what
:mod:`bogle.withdrawal` reads from the database around the pure engine.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from psycopg.rows import DictRow

from bogle.domain.assets import AssetType
from bogle.domain.errors import MissingPriceError, ValidationError
from bogle.domain.transactions import Transaction, TransactionType
from bogle.glide_path import ActiveGlidePath, exiting_tickers
from bogle.position import PortfolioSummary, Position
from bogle.rebalancing import WithdrawalStage, retirement_phase, suggest_withdrawal
from bogle.repositories.assets import AssetRepository
from bogle.repositories.transactions import TransactionRepository
from bogle.settings import BIRTH_YEAR, LAST_REBALANCE_DATE, RETIREMENT_AGE, get_setting, set_value
from bogle.withdrawal import month_stock_sales, oldest_lot, plan_withdrawal

D = Decimal
TODAY = date(2026, 11, 27)


def position(
    ticker: str,
    *,
    price: str,
    quantity: str,
    target: str,
    average: str | None = None,
    asset_type: AssetType = AssetType.ETF,
    total: str = "100000",
) -> Position:
    value = D(price) * D(quantity)
    weight = value / D(total)
    return Position(
        ticker=ticker,
        asset_type=asset_type,
        quantity=D(quantity),
        total_invested=D(average) * D(quantity) if average is not None else None,
        target_weight=D(target),
        dividends=D("0"),
        average_price=D(average) if average is not None else None,
        price=D(price),
        market_value=value,
        current_weight=weight,
        drift=weight - D(target),
    )


def summary_of(*positions: Position) -> PortfolioSummary:
    total = sum((p.market_value or D("0") for p in positions), D("0"))
    return PortfolioSummary(
        positions=list(positions),
        total_value=total,
        total_invested=D("0"),
        total_pnl=D("0"),
        total_dividends=D("0"),
    )


def retirement_portfolio() -> PortfolioSummary:
    """R$100.000: MUND11 2.000 above its target, NB1011 (on its way out) 5.000
    above, B5P211 7.000 below."""
    return summary_of(
        position("MUND11", price="100", quantity="620", target="0.60", average="80"),
        position("NB1011", price="50", quantity="300", target="0.10", average="40"),
        position("B5P211", price="100", quantity="230", target="0.30", average="90"),
    )


def by_ticker(items: list[Any]) -> dict[str, Any]:
    return {item.ticker: item for item in items}


class TestOrderOfSale:
    def test_the_exiting_ticker_sells_before_any_other(self) -> None:
        # Sobre os 97.000 que ficam, MUND11 tem o maior excesso (9.800 contra
        # 5.300), e ainda assim o NB1011, que a trajetoria esta tirando, paga o
        # saque inteiro.
        summary = summary_of(
            position("MUND11", price="100", quantity="680", target="0.60", average="80"),
            position("NB1011", price="50", quantity="300", target="0.10", average="40"),
            position("B5P211", price="100", quantity="170", target="0.30", average="90"),
        )
        suggestion = suggest_withdrawal(summary, D("3000"), {"NB1011"})
        assert [item.ticker for item in suggestion.items] == ["NB1011"]
        [nb] = suggestion.items
        assert nb.quantity == D("60")
        assert nb.stages == (WithdrawalStage.EXITING,)

    def test_the_exiting_ticker_goes_down_to_the_target_of_the_year_then_the_excess(self) -> None:
        # Os alvos valem sobre o que fica (94.000). Etapa 1 leva o NB1011 aos
        # 10% disso (5.600 de excesso); etapa 2 tira o resto do MUND11, que esta
        # acima do alvo. O B5P211, abaixo, fica.
        suggestion = suggest_withdrawal(retirement_portfolio(), D("6000"), {"NB1011"})
        assert [item.ticker for item in suggestion.items] == ["NB1011", "MUND11"]
        items = by_ticker(suggestion.items)
        assert items["NB1011"].quantity == D("112")
        assert items["NB1011"].gross == D("5600")
        assert items["NB1011"].weight_after == D("0.1")  # exatamente o alvo
        assert items["NB1011"].stages == (WithdrawalStage.EXITING,)
        assert items["MUND11"].quantity == D("4")
        assert items["MUND11"].stages == (WithdrawalStage.EXCESS,)
        assert suggestion.residual == D("0")
        assert "NB1011 em saída na trajetória: vendido primeiro" in suggestion.warnings

    def test_the_largest_excess_sells_first_and_never_below_the_target(self) -> None:
        # Sobre os 93.000 que ficam, A tem 6.500 de excesso e B 2.400: o saque
        # de 7.000 leva A ate o alvo (e nao abaixo) e so entao tira 500 de B.
        summary = summary_of(
            position("BBBB11", price="10", quantity="2100", target="0.20", average="10"),
            position("AAAA11", price="10", quantity="5300", target="0.50", average="10"),
            position("CCCC11", price="10", quantity="2600", target="0.30", average="10"),
        )
        suggestion = suggest_withdrawal(summary, D("7000"))
        assert [item.ticker for item in suggestion.items] == ["AAAA11", "BBBB11"]
        items = by_ticker(suggestion.items)
        assert items["AAAA11"].gross == D("6500")  # 53.000 -> 46.500, 50% de 93.000
        assert items["AAAA11"].weight_after == D("0.5")
        assert items["BBBB11"].gross == D("500")
        assert all(item.stages == (WithdrawalStage.EXCESS,) for item in suggestion.items)

    def test_a_ticker_below_its_target_today_can_be_above_it_after(self) -> None:
        # O saque encolhe o patrimonio: o B5P211, a 23% de 100.000, passa do
        # alvo de 30% sobre 75.000 (22.500) e entra na etapa 2 com os outros.
        suggestion = suggest_withdrawal(retirement_portfolio(), D("25000"), {"NB1011"})
        items = by_ticker(suggestion.items)
        assert items["B5P211"].stages == (WithdrawalStage.EXCESS,)
        assert all(item.weight_after == item.target_weight for item in suggestion.items)

    @pytest.mark.parametrize("amount", ["1000", "6000", "10000", "20000"])
    def test_no_ticker_goes_below_its_target_while_another_has_excess(self, amount: str) -> None:
        # Quem vende para no alvo do patrimonio pos-saque, e o B5P211 (abaixo do
        # alvo) nao vende enquanto os outros cobrem o saque.
        suggestion = suggest_withdrawal(retirement_portfolio(), D(amount), {"NB1011"})
        assert "B5P211" not in by_ticker(suggestion.items)
        for item in suggestion.items:
            assert item.weight_after is not None
            assert item.weight_after >= item.target_weight, item.ticker
        assert not any(WithdrawalStage.PROPORTIONAL in item.stages for item in suggestion.items)

    def test_targets_below_one_hundred_percent_still_leave_excess_enough(self) -> None:
        # A parte sem alvo e excesso de todo mundo: o saque continua coberto nas
        # etapas 1 e 2, e a 3 nao entra.
        summary = summary_of(
            position("MUND11", price="100", quantity="620", target="0.50", average="80"),
            position("NB1011", price="50", quantity="300", target="0.10", average="40"),
            position("B5P211", price="100", quantity="230", target="0.30", average="90"),
        )
        suggestion = suggest_withdrawal(summary, D("30000"), {"NB1011"})
        assert suggestion.total_gross == D("30000")
        assert not any(WithdrawalStage.PROPORTIONAL in item.stages for item in suggestion.items)

    def test_the_proportional_safeguard_covers_what_the_excess_did_not(self) -> None:
        # Alvos somando 110% (o repositorio recusa, a funcao pura recebe): sobre
        # os 90.000 que ficam, NB1011 tem 6.000 de excesso e MUND11 3.500. Os
        # 500 que faltam saem de todos pelo peso do que ficou (9.000 / 58.500 /
        # 23.000 de 90.500).
        summary = summary_of(
            position("MUND11", price="100", quantity="620", target="0.65", average="80"),
            position("NB1011", price="50", quantity="300", target="0.10", average="40"),
            position("B5P211", price="100", quantity="230", target="0.35", average="90"),
        )
        suggestion = suggest_withdrawal(summary, D("10000"), {"NB1011"})
        assert [item.ticker for item in suggestion.items] == ["NB1011", "MUND11", "B5P211"]
        items = by_ticker(suggestion.items)
        assert items["NB1011"].stages == (WithdrawalStage.EXITING, WithdrawalStage.PROPORTIONAL)
        assert items["MUND11"].stages == (WithdrawalStage.EXCESS, WithdrawalStage.PROPORTIONAL)
        assert items["B5P211"].stages == (WithdrawalStage.PROPORTIONAL,)
        # 6.049,72 / 50 = 120,99 -> 121; 3.823,20 / 100 -> 39; 127,07 / 100 -> 2
        assert items["NB1011"].quantity == D("121")
        assert items["MUND11"].quantity == D("39")
        assert items["B5P211"].quantity == D("2")
        assert suggestion.total_gross == D("10150")
        assert "Excesso sobre os alvos não cobre o saque: o resto sai de todos, pelo peso" in suggestion.warnings

    def test_the_proportional_safeguard_keeps_the_weights_where_they_were(self) -> None:
        # Sem excesso nenhum (alvos somando 120%), so a etapa 3: todos vendem a
        # mesma fracao, e os pesos ficam onde estavam.
        summary = summary_of(
            position("MUND11", price="100", quantity="620", target="0.70", average="80"),
            position("NB1011", price="50", quantity="300", target="0.20", average="40"),
            position("B5P211", price="100", quantity="230", target="0.30", average="90"),
        )
        suggestion = suggest_withdrawal(summary, D("10000"))
        assert {item.ticker: item.quantity for item in suggestion.items} == {
            "MUND11": D("62"),
            "B5P211": D("23"),
            "NB1011": D("30"),
        }
        for item in suggestion.items:
            assert item.stages == (WithdrawalStage.PROPORTIONAL,)
            assert item.weight_after == item.current_weight

    def test_without_exiting_tickers_the_excess_comes_first(self) -> None:
        suggestion = suggest_withdrawal(retirement_portfolio(), D("1000"))
        [first] = suggestion.items
        assert first.ticker == "NB1011"  # o maior excesso, agora so na etapa 2
        assert first.stages == (WithdrawalStage.EXCESS,)
        assert not any("em saída" in warning for warning in suggestion.warnings)

    def test_an_exiting_ticker_sells_only_its_excess(self) -> None:
        # NB1011 no alvo de hoje: sobre os 99.000 que ficam, o excesso dele e
        # 100 (2 cotas), e o resto sai do MUND11.
        summary = summary_of(
            position("MUND11", price="100", quantity="620", target="0.60", average="80"),
            position("NB1011", price="50", quantity="200", target="0.10", average="40"),
            position("B5P211", price="100", quantity="280", target="0.30", average="90"),
        )
        suggestion = suggest_withdrawal(summary, D("1000"), {"NB1011"})
        assert [(item.ticker, item.quantity) for item in suggestion.items] == [
            ("NB1011", D("2")),
            ("MUND11", D("9")),
        ]


class TestWholeShares:
    def test_variable_income_rounds_up_and_reports_the_residual(self) -> None:
        summary = summary_of(
            position("MUND11", price="30", quantity="2000", target="0.50", average="20", total="80000"),
            position("B5P211", price="100", quantity="200", target="0.50", average="90", total="80000"),
        )
        # 20.000 de excesso no MUND11; 1.000 / 30 = 33,3 -> 34 cotas = 1.020.
        suggestion = suggest_withdrawal(summary, D("1000"))
        [item] = suggestion.items
        assert item.quantity == D("34")
        assert item.allocation == D("1000")
        assert item.gross == D("1020")
        assert suggestion.residual == D("20")

    def test_fixed_income_sells_the_exact_value(self) -> None:
        summary = summary_of(
            position("TESOURO-IPCA-2035", price="3000", quantity="10", target="0.20", asset_type=AssetType.TESOURO),
            position("MUND11", price="100", quantity="300", target="0.80", average="80"),
        )
        suggestion = suggest_withdrawal(summary, D("1234.56"), held_since={"TESOURO-IPCA-2035": date(2020, 1, 1)})
        [item] = suggestion.items
        assert item.ticker == "TESOURO-IPCA-2035"
        assert item.quantity is None
        assert item.gross == D("1234.56")
        assert suggestion.residual == D("0")

    def test_the_whole_portfolio_can_be_withdrawn(self) -> None:
        suggestion = suggest_withdrawal(retirement_portfolio(), D("100000"), {"NB1011"})
        assert suggestion.total_gross == D("100000")
        assert all(item.weight_after is None for item in suggestion.items)


class TestIncomeTax:
    def test_tax_per_sale_net_and_the_net_against_the_amount(self) -> None:
        suggestion = suggest_withdrawal(retirement_portfolio(), D("6000"), {"NB1011"})
        items = by_ticker(suggestion.items)
        assert items["NB1011"].income_tax == D("168.00")  # (5.600 - 4.480) x 15%
        assert items["NB1011"].net == D("5432.00")
        assert items["MUND11"].income_tax == D("12.00")  # (400 - 320) x 15%
        assert suggestion.total_tax == D("180.00")
        assert suggestion.total_net == D("5820.00")
        assert suggestion.net_vs_amount == D("-180.00")

    def test_a_loss_owes_nothing(self) -> None:
        summary = summary_of(
            position("MUND11", price="100", quantity="100", target="0.5", average="120", total="10000")
        )
        [item] = suggest_withdrawal(summary, D("1000")).items
        assert item.income_tax == D("0")

    def test_fii_pays_twenty_percent(self) -> None:
        summary = summary_of(
            position("MXRF11", price="10", quantity="1000", target="0.5", average="8", asset_type=AssetType.FII)
        )
        [item] = suggest_withdrawal(summary, D("1000")).items
        assert item.income_tax == D("40.00")  # (1.000 - 800) x 20%

    def test_stocks_are_exempt_up_to_twenty_thousand_in_the_month(self) -> None:
        summary = summary_of(
            position("PETR4", price="40", quantity="1000", target="0.5", average="30", asset_type=AssetType.STOCK)
        )
        [exempt] = suggest_withdrawal(summary, D("2000")).items
        assert exempt.income_tax == D("0")

    def test_the_stock_sales_of_the_month_count_for_the_exemption(self) -> None:
        # 19.000 ja vendidos no mes + os 2.000 sugeridos passam dos 20.000: o
        # ganho inteiro e tributado.
        summary = summary_of(
            position("PETR4", price="40", quantity="1000", target="0.5", average="30", asset_type=AssetType.STOCK)
        )
        [taxed] = suggest_withdrawal(summary, D("2000"), month_stock_sales=D("19000")).items
        assert taxed.income_tax == D("75.00")  # (2.000 - 1.500) x 15%

    def test_fixed_income_follows_the_regressive_table_from_the_oldest_lot(self) -> None:
        summary = summary_of(
            position(
                "TESOURO-IPCA-2035",
                price="120",
                quantity="100",
                target="0",
                asset_type=AssetType.TESOURO,
                average="100",
            )
        )
        long_held = suggest_withdrawal(
            summary, D("1200"), held_since={"TESOURO-IPCA-2035": TODAY - timedelta(days=1000)}, today=TODAY
        )
        recent = suggest_withdrawal(
            summary, D("1200"), held_since={"TESOURO-IPCA-2035": TODAY - timedelta(days=100)}, today=TODAY
        )
        assert long_held.items[0].income_tax == D("30.00")  # 200 de rendimento x 15%
        assert recent.items[0].income_tax == D("45.00")  # x 22,5%

    def test_lci_is_exempt(self) -> None:
        summary = summary_of(
            position("LCI-X", price="1200", quantity="1", target="0", asset_type=AssetType.LCI, average="1000")
        )
        [item] = suggest_withdrawal(summary, D("600"), held_since={"LCI-X": date(2024, 1, 1)}).items
        assert item.income_tax == D("0")

    def test_without_an_average_cost_the_tax_is_unknown_and_said(self) -> None:
        summary = summary_of(
            position("MUND11", price="100", quantity="620", target="0.60", average="80"),
            position("NB1011", price="50", quantity="300", target="0.10"),
            position("B5P211", price="100", quantity="230", target="0.30", average="90"),
        )
        suggestion = suggest_withdrawal(summary, D("6000"), {"NB1011"})
        items = by_ticker(suggestion.items)
        assert items["NB1011"].income_tax is None
        assert items["NB1011"].net is None
        assert suggestion.total_tax == D("12.00")  # so o MUND11
        assert "Sem custo médio de NB1011: IR não estimado" in suggestion.warnings


class TestPhase:
    def test_retirement_starts_in_the_year_of_the_age(self) -> None:
        assert retirement_phase(1956, 70, 2026) is True
        assert retirement_phase(1957, 70, 2026) is False
        assert retirement_phase(None, 70, 2026) is None

    def test_in_retirement_there_is_no_phase_warning(self) -> None:
        suggestion = suggest_withdrawal(retirement_portfolio(), D("1000"), retired=True)
        assert suggestion.warnings == []

    def test_before_retirement_it_still_suggests_but_warns(self) -> None:
        suggestion = suggest_withdrawal(retirement_portfolio(), D("1000"), retired=False)
        assert suggestion.items
        assert suggestion.warnings[0] == "Antes da aposentadoria: na acumulação a política é não vender"

    def test_without_birth_year_it_is_treated_as_accumulation(self) -> None:
        suggestion = suggest_withdrawal(retirement_portfolio(), D("1000"), retired=None)
        assert suggestion.retired is None
        assert (
            suggestion.warnings[0] == "Sem ano de nascimento: tratado como acumulação, quando a política é não vender"
        )


class TestRefusals:
    @pytest.mark.parametrize("amount", ["0", "-10"])
    def test_the_amount_must_be_positive(self, amount: str) -> None:
        with pytest.raises(ValidationError, match="Valor do saque deve ser positivo"):
            suggest_withdrawal(retirement_portfolio(), D(amount))

    def test_not_more_than_the_portfolio(self) -> None:
        with pytest.raises(ValidationError, match=r"maior que o patrimônio de 100000\.00"):
            suggest_withdrawal(retirement_portfolio(), D("100000.01"))

    def test_every_position_must_be_priced(self) -> None:
        unpriced = Position(
            ticker="VWRA11",
            asset_type=AssetType.ETF,
            quantity=D("10"),
            total_invested=D("1000"),
            target_weight=D("0.1"),
            dividends=D("0"),
        )
        summary = retirement_portfolio()
        summary = summary_of(*summary.positions, unpriced)
        with pytest.raises(MissingPriceError):
            suggest_withdrawal(summary, D("1000"))

    def test_an_empty_portfolio(self) -> None:
        with pytest.raises(ValidationError, match="Nenhuma posição aberta"):
            suggest_withdrawal(summary_of(), D("1000"))

    def test_a_private_fixed_income_sale_asks_to_check_the_liquidity(self) -> None:
        summary = summary_of(
            position("CDB-XP-2027", price="1100", quantity="10", target="0", asset_type=AssetType.CDB, average="1000"),
            position("MUND11", price="100", quantity="890", target="1", average="80"),
        )
        suggestion = suggest_withdrawal(summary, D("1000"), held_since={"CDB-XP-2027": date(2024, 1, 1)})
        assert "CDB-XP-2027 é renda fixa privada: confira a liquidez antes de vender" in suggestion.warnings


GLIDE_POINTS = {
    39: {"MUND11": D("0.70"), "NB1011": D("0.25"), "B5P211": D("0.05")},
    71: {"MUND11": D("0.30"), "B5P211": D("0.70")},
}


class TestExitingTickers:
    def test_a_target_of_zero_at_the_last_point_is_on_its_way_out(self) -> None:
        glide = ActiveGlidePath(points=GLIDE_POINTS, birth_year=1986)
        assert exiting_tickers(glide, ["MUND11", "NB1011", "B5P211"]) == {"NB1011"}

    def test_a_ticker_outside_the_trajectory_is_too(self) -> None:
        glide = ActiveGlidePath(points=GLIDE_POINTS, birth_year=1986)
        assert exiting_tickers(glide, ["MUND11", "PETR4"]) == {"PETR4"}

    def test_without_a_trajectory_nothing_is(self) -> None:
        assert exiting_tickers(None, ["MUND11", "NB1011"]) == frozenset()


def trade(kind: TransactionType, ticker: str, day: date, shares: str, txn_id: int, price: str = "10") -> Transaction:
    gross = D(shares) * D(price)
    return Transaction(
        id=txn_id,
        ticker=ticker,
        transaction_type=kind,
        date=datetime(day.year, day.month, day.day, 12),
        shares=D(shares),
        unit_price=D(price),
        total_investment=gross,
        fees=D("0"),
        total_cost=gross if kind is TransactionType.BUY else D("0"),
        tax_withheld=D("0"),
    )


class TestLedgerReads:
    def test_the_oldest_lot_is_first_in_first_out(self) -> None:
        txns = [
            trade(TransactionType.BUY, "T", date(2020, 1, 10), "10", 1),
            trade(TransactionType.BUY, "T", date(2022, 5, 3), "10", 2),
            trade(TransactionType.SELL, "T", date(2023, 1, 2), "10", 3),
        ]
        assert oldest_lot(txns) == date(2022, 5, 3)  # a venda levou o lote de 2020 inteiro
        assert oldest_lot(txns[:2]) == date(2020, 1, 10)
        assert oldest_lot([*txns, trade(TransactionType.SELL, "T", date(2023, 2, 1), "10", 4)]) is None

    def test_the_month_counts_only_stock_sales_of_this_month(self) -> None:
        types = {"PETR4": AssetType.STOCK, "MXRF11": AssetType.FII}
        txns = [
            trade(TransactionType.SELL, "PETR4", date(2026, 11, 3), "100", 1, price="30"),
            trade(TransactionType.SELL, "PETR4", date(2026, 10, 30), "100", 2, price="30"),
            trade(TransactionType.SELL, "MXRF11", date(2026, 11, 3), "100", 3),
            trade(TransactionType.BUY, "PETR4", date(2026, 11, 4), "100", 4, price="30"),
        ]
        assert month_stock_sales(txns, types, TODAY) == D("3000")


@pytest.fixture
def clean(conn: psycopg.Connection[DictRow]) -> Iterator[psycopg.Connection[DictRow]]:
    """``conn``, with the trajectory and the settings gone after the test too.

    Other tests run the CLI against bogle_test without truncating it first; a
    trajectory left behind would be applied (and announced) there.
    """
    yield conn
    conn.execute("TRUNCATE glide_path_points, user_settings")
    conn.commit()


class TestPlanWithdrawal:
    """What :func:`plan_withdrawal` reads around the engine, with the prices injected."""

    @pytest.fixture
    def seeded(self, clean: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch) -> None:
        repo = AssetRepository(clean)
        repo.add("PETR4", D("0.5"), asset_type=AssetType.STOCK)
        repo.add("MUND11", D("0.5"), asset_type=AssetType.ETF)
        trepo = TransactionRepository(clean)
        trepo.add_buy("PETR4", datetime(2026, 1, 5, 12), D("1000"), D("20"))
        trepo.add_sale("PETR4", datetime(2026, 11, 3, 12), D("500"), D("38"))  # 19.000 no mes
        trepo.add_buy("MUND11", datetime(2026, 1, 5, 12), D("100"), D("100"))
        clean.execute(
            "INSERT INTO glide_path_points (age, ticker, target_weight) VALUES (39, 'MUND11', 1), (71, 'MUND11', 1)"
        )
        set_value(clean, BIRTH_YEAR, 1950)
        clean.commit()
        summary = summary_of(
            position(
                "PETR4",
                price="40",
                quantity="500",
                target="0.5",
                average="20",
                asset_type=AssetType.STOCK,
                total="30000",
            ),
            position("MUND11", price="100", quantity="100", target="0.5", average="100", total="30000"),
        )
        monkeypatch.setattr("bogle.withdrawal.get_portfolio_summary", lambda conn, dispatcher, **_: summary)

    def test_reads_the_exiting_tickers_the_phase_and_the_month(
        self, seeded: None, clean: psycopg.Connection[DictRow]
    ) -> None:
        suggestion = plan_withdrawal(clean, None, D("2000"), today=TODAY)
        [petr] = suggestion.items
        # PETR4 esta fora da trajetoria: em saida, vendido primeiro.
        assert petr.ticker == "PETR4"
        assert petr.stages == (WithdrawalStage.EXITING,)
        # 19.000 ja vendidos no mes + 2.000: passa da isencao, (2.000 - 1.000) x 15%.
        assert petr.income_tax == D("150.00")
        assert suggestion.retired is True  # 76 anos, aposentadoria aos 70 (default)

    def test_the_retirement_age_moves_the_phase(self, seeded: None, clean: psycopg.Connection[DictRow]) -> None:
        set_value(clean, RETIREMENT_AGE, 80)
        assert plan_withdrawal(clean, None, D("2000"), today=TODAY).retired is False

    def test_records_nothing(self, seeded: None, clean: psycopg.Connection[DictRow]) -> None:
        plan_withdrawal(clean, None, D("2000"), today=TODAY)
        assert get_setting(clean, LAST_REBALANCE_DATE) is None
        assert len(TransactionRepository(clean).list()) == 3
