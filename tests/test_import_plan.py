"""Tests for planning and writing a B3 import (issue #34).

``build_plan`` is pure and tested on statements built in memory; ``apply_import``
runs against ``bogle_test``, starting from the real export.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import ROUND_UP, Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
import pytest
from psycopg.rows import DictRow

from bogle import format as fmt
from bogle.domain.assets import Asset, AssetType
from bogle.domain.errors import ImportStaleError, ValidationError
from bogle.domain.transactions import Transaction, TransactionType
from bogle.imports.b3 import RowError, Statement, Trade, read_statement
from bogle.imports.plan import (
    Divergent,
    ImportPlan,
    Outcome,
    TargetToReview,
    Uncovered,
    allot,
    apply_import,
    build_plan,
    plan_import,
    report_lines,
    summary,
    ticker_outcomes,
)
from bogle.rebalancing import B3_FEE_RATE, b3_fee
from bogle.repositories.assets import AssetRepository
from bogle.repositories.transactions import TransactionRepository

FIXTURE = Path(__file__).parent / "fixtures" / "b3_negociacao.xlsx"
SAO_PAULO = ZoneInfo("America/Sao_Paulo")
BUY, SELL = TransactionType.BUY, TransactionType.SELL
PORTFOLIO = ("AUVP11", "B5P211", "MUND11", "NB1011", "VWRA11")
FEE_RATE = Decimal("0.00032")


def at(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=SAO_PAULO)


def trade(row: int, ticker: str, when: datetime, shares: str, price: str, kind: TransactionType = BUY) -> Trade:
    return Trade(row=row, when=when, kind=kind, ticker=ticker, shares=Decimal(shares), unit_price=Decimal(price))


def statement(
    *trades: Trade, errors: tuple[RowError, ...] = (), ignored: tuple[tuple[str, int], ...] = ()
) -> Statement:
    rows = len(trades) + len(errors) + sum(count for _, count in ignored)
    return Statement(trades=trades, ignored_markets=ignored, errors=errors, rows=rows)


def asset(ticker: str, weight: str = "0.2") -> Asset:
    return Asset(ticker=ticker, target_weight=Decimal(weight), asset_type=AssetType.ETF)


def assets(*tickers: str, weight: str = "0.2") -> list[Asset]:
    return [asset(ticker, weight) for ticker in tickers]


def txn(
    ticker: str,
    when: datetime,
    shares: str,
    price: str = "10",
    kind: TransactionType = BUY,
    *,
    id: int = 1,
    fees: str = "0",
    tax: str = "0",
) -> Transaction:
    gross = Decimal(shares) * Decimal(price)
    return Transaction(
        id=id,
        ticker=ticker,
        transaction_type=kind,
        date=when,
        shares=Decimal(shares),
        unit_price=Decimal(price),
        total_investment=gross,
        fees=Decimal(fees),
        total_cost=gross + Decimal(fees) if kind is BUY else Decimal(fees),
        tax_withheld=Decimal(tax),
    )


def plan_of(
    stmt: Statement, *, ledger: list[Transaction] | None = None, fee_rate: Decimal | None = None, **kw: Any
) -> ImportPlan:
    registered = kw.get("registered", assets(*PORTFOLIO))
    return build_plan(stmt, assets=registered, ledger=ledger or [], fee_rate=fee_rate, replace=kw.get("replace", False))


def rows_accounted(plan: ImportPlan) -> int:
    """New + duplicated + ignored + rows of skipped tickers + error rows without a ticker."""
    skipped = sum(
        len(item.rows) for group in (plan.unregistered, plan.divergent, plan.uncovered, plan.errored) for item in group
    )
    ignored = sum(count for _, count in plan.statement.ignored_markets)
    orphan_errors = sum(1 for error in plan.statement.errors if error.ticker is None)
    return len(plan.inserts) + len(plan.duplicates) + ignored + skipped + orphan_errors


# ------------------------------------------------------------------ puro


class TestRealExport:
    def test_every_row_is_new_on_an_empty_ledger(self) -> None:
        plan = plan_of(read_statement(FIXTURE))
        assert len(plan.inserts) == 21
        assert plan.duplicates == ()
        assert rows_accounted(plan) == plan.statement.rows == 21

    def test_writing_order_is_by_date_purchases_first(self) -> None:
        inserts = plan_of(read_statement(FIXTURE)).inserts
        keys = [(t.when, t.kind is SELL, t.row) for t in inserts]
        assert keys == sorted(keys)
        assert inserts[0].when == at(2026, 1, 9)
        assert inserts[-1].when == at(2026, 10, 6)

    def test_the_report_of_a_first_import(self) -> None:
        assert report_lines(plan_of(read_statement(FIXTURE))) == [
            "Novas: 21 (AUVP11 2, B5P211 7, MUND11 2, NB1011 2, VWRA11 8)",
            "Posição zerada com target: AUVP11 20.00%, VWRA11 20.00% (bogle update AUVP11 --weight 0 tira do plano)",
            "21 operações importadas sem taxas",
            "3 vendas importadas sem IR retido",
        ]


class TestDeduplication:
    def test_a_consolidated_order_matches_its_executions(self) -> None:
        # 18 digitadas de uma vez contra 5 + 13 no arquivo: o total do dia bate.
        day = at(2026, 3, 10)
        stmt = statement(trade(2, "VWRA11", day, "5", "112"), trade(3, "VWRA11", day, "13", "112.5"))
        plan = plan_of(stmt, ledger=[txn("VWRA11", day, "18", "112.36")])
        assert plan.inserts == ()
        assert len(plan.duplicates) == 2
        assert report_lines(plan)[:2] == ["Novas: 0", "Duplicadas: 2 (VWRA11 2)"]

    def test_two_identical_executions_are_two_rows(self) -> None:
        day = at(2026, 3, 10)
        plan = plan_of(statement(trade(2, "MUND11", day, "10", "30.00"), trade(3, "MUND11", day, "10", "30.00")))
        assert [(t.row, t.shares) for t in plan.inserts] == [(2, Decimal("10")), (3, Decimal("10"))]

    def test_a_day_is_compared_in_sao_paulo(self) -> None:
        # 01:00 UTC de 10/03 ainda e 09/03 em Sao Paulo.
        stmt = statement(trade(2, "MUND11", at(2026, 3, 9), "10", "30"))
        plan = plan_of(stmt, ledger=[txn("MUND11", datetime(2026, 3, 10, 1, tzinfo=UTC), "10", "30")])
        assert plan.inserts == ()

    def test_a_different_total_skips_the_whole_ticker(self) -> None:
        stmt = read_statement(FIXTURE)
        ledger = [txn("VWRA11", at(2026, 1, 9), "70", "117.35")]
        plan = plan_of(stmt, ledger=ledger)
        assert not [t for t in plan.inserts if t.ticker == "VWRA11"]
        assert {t.ticker for t in plan.inserts} == {"AUVP11", "B5P211", "MUND11", "NB1011"}
        [divergent] = plan.divergent
        assert divergent == Divergent("VWRA11", date(2026, 1, 9), BUY, Decimal("72"), Decimal("70"), divergent.rows)
        assert len(divergent.rows) == 8
        assert "Total do dia diverge do ledger: VWRA11 (2026-01-09 compra: arquivo 72 × ledger 70)" in report_lines(  # noqa: RUF001
            plan
        )
        assert rows_accounted(plan) == 21

    def test_the_first_divergent_day_is_named(self) -> None:
        stmt = statement(
            trade(2, "MUND11", at(2026, 5, 2), "4", "30", SELL),
            trade(3, "MUND11", at(2026, 3, 1), "10", "30"),
        )
        ledger = [txn("MUND11", at(2026, 3, 1), "9"), txn("MUND11", at(2026, 5, 2), "3", kind=SELL)]
        [divergent] = plan_of(stmt, ledger=ledger).divergent
        assert (divergent.day, divergent.kind) == (date(2026, 3, 1), BUY)

    def test_new_days_go_in_next_to_duplicated_ones(self) -> None:
        stmt = statement(trade(2, "MUND11", at(2026, 4, 1), "5", "31"), trade(3, "MUND11", at(2026, 3, 1), "10", "30"))
        plan = plan_of(stmt, ledger=[txn("MUND11", at(2026, 3, 1), "10", "30")])
        assert [t.row for t in plan.inserts] == [2]
        assert [t.row for t in plan.duplicates] == [3]
        [outcome] = ticker_outcomes(plan)
        assert (outcome.outcome, outcome.new, outcome.duplicated) == (Outcome.NEW, 1, 1)

    def test_income_in_the_ledger_is_not_a_trade(self) -> None:
        dividend = txn("MUND11", at(2026, 3, 1), "0", kind=TransactionType.DIVIDEND)
        plan = plan_of(statement(trade(2, "MUND11", at(2026, 3, 1), "10", "30")), ledger=[dividend])
        assert len(plan.inserts) == 1


class TestSkippedTickers:
    def test_an_unregistered_ticker_is_listed_with_the_command(self) -> None:
        stmt = statement(
            trade(2, "XPTO11", at(2026, 3, 1), "1", "10"),
            trade(3, "ABCD3", at(2026, 3, 1), "1", "10"),
            trade(4, "MUND11", at(2026, 3, 1), "1", "10"),
        )
        plan = plan_of(stmt)
        assert [t.ticker for t in plan.inserts] == ["MUND11"]
        assert [item.ticker for item in plan.unregistered] == ["ABCD3", "XPTO11"]
        assert (
            "Sem cadastro: ABCD3, XPTO11 (bogle add ABCD3 --type <STOCK|FII|ETF|BDR> --weight <peso>)"
            in report_lines(plan)
        )

    def test_an_uncovered_sale_skips_its_ticker_and_not_the_others(self) -> None:
        stmt = statement(
            trade(2, "B5P211", at(2026, 9, 28), "96", "94.74", SELL),
            trade(3, "B5P211", at(2026, 8, 21), "91", "92.98"),
            trade(4, "MUND11", at(2026, 8, 21), "10", "95"),
        )
        plan = plan_of(stmt)
        assert [t.ticker for t in plan.inserts] == ["MUND11"]
        assert plan.uncovered == (Uncovered("B5P211", date(2026, 9, 28), Decimal("5"), (2, 3)),)
        assert (
            "Venda descoberta: B5P211 (faltam 5 em 2026-09-28): registre a posição de abertura com bogle buy --date e reimporte"
            in report_lines(plan)
        )

    def test_the_ledger_covers_an_imported_sale(self) -> None:
        stmt = statement(trade(2, "B5P211", at(2026, 9, 28), "96", "94.74", SELL))
        plan = plan_of(stmt, ledger=[txn("B5P211", at(2019, 11, 1), "100")])
        assert [t.kind for t in plan.inserts] == [SELL]

    def test_a_row_with_an_error_skips_its_whole_ticker(self) -> None:
        stmt = statement(
            trade(2, "VWRA11", at(2026, 1, 9), "20", "117.37"),
            trade(4, "MUND11", at(2026, 1, 9), "10", "95"),
            errors=(RowError(3, "VWRA11", "quantidade não numérica"), RowError(5, None, "código de negociação vazio")),
        )
        plan = plan_of(stmt)
        assert [t.ticker for t in plan.inserts] == ["MUND11"]
        assert [(item.ticker, item.rows) for item in plan.errored] == [("VWRA11", (2, 3))]
        assert (
            "Linhas com erro: 3 (quantidade não numérica), 5 (código de negociação vazio); ticker pulado: VWRA11"
            in report_lines(plan)
        )
        assert rows_accounted(plan) == stmt.rows == 4

    def test_each_ticker_falls_in_one_category(self) -> None:
        stmt = statement(
            trade(2, "XPTO11", at(2026, 1, 9), "1", "10"),
            trade(3, "VWRA11", at(2026, 1, 9), "20", "117.37"),
            errors=(RowError(4, "XPTO11", "data inválida"),),
            ignored=(("Mercado Fracionário", 2),),
        )
        plan = plan_of(stmt, ledger=[txn("VWRA11", at(2026, 1, 9), "70")])
        assert [(o.ticker, o.outcome) for o in ticker_outcomes(plan)] == [
            ("VWRA11", Outcome.DIVERGENT),
            ("XPTO11", Outcome.ERRORED),
        ]
        assert plan.unregistered == ()  # o erro de linha vem primeiro
        assert rows_accounted(plan) == stmt.rows == 5


class TestFees:
    def test_without_a_rate_fees_are_zero_and_said_once(self) -> None:
        plan = plan_of(read_statement(FIXTURE))
        assert {t.fees for t in plan.inserts} == {Decimal("0")}
        assert sum("sem taxas" in line for line in report_lines(plan)) == 1

    def test_a_zero_rate_is_no_rate(self) -> None:
        plan = plan_of(read_statement(FIXTURE), fee_rate=Decimal("0"))
        assert "21 operações importadas sem taxas" in report_lines(plan)

    def test_a_rate_is_charged_per_execution_rounded_up(self) -> None:
        plan = plan_of(read_statement(FIXTURE), fee_rate=FEE_RATE)
        by_row = {t.row: t for t in plan.inserts}
        assert by_row[2].fees == Decimal("2.47")  # 81 x 95,27 x 0,032% = 2,4694
        assert sum((t.fees for t in plan.inserts), Decimal("0")) == Decimal("54.67")
        assert all(t.fees > 0 for t in plan.inserts if t.kind is SELL)
        assert not [line for line in report_lines(plan) if "sem taxas" in line]

    def test_b3_fee_is_the_rebalancing_rule(self) -> None:
        gross = Decimal("7716.87")
        assert b3_fee(gross) == (gross * B3_FEE_RATE).quantize(Decimal("0.01"), rounding=ROUND_UP)
        assert b3_fee(Decimal("100"), Decimal("0.0001")) == Decimal("0.01")
        assert b3_fee(Decimal("100.01"), Decimal("0.0001")) == Decimal("0.02")


class TestTargets:
    def test_tickers_left_at_zero_with_a_target_are_listed(self) -> None:
        plan = plan_of(read_statement(FIXTURE))
        assert plan.targets == (
            TargetToReview("AUVP11", Decimal("0.2")),
            TargetToReview("VWRA11", Decimal("0.2")),
        )

    def test_a_zero_target_is_not_listed(self) -> None:
        registered = [*assets("B5P211", "MUND11", "NB1011", "VWRA11"), asset("AUVP11", "0")]
        plan = plan_of(read_statement(FIXTURE), registered=registered)
        assert [item.ticker for item in plan.targets] == ["VWRA11"]

    def test_a_ticker_without_inserts_is_not_listed(self) -> None:
        stmt = statement(
            trade(2, "AUVP11", at(2026, 3, 10), "9", "95.95"), trade(3, "AUVP11", at(2026, 9, 2), "9", "96.14", SELL)
        )
        ledger = [txn("AUVP11", at(2026, 3, 10), "9"), txn("AUVP11", at(2026, 9, 2), "9", kind=SELL)]
        assert plan_of(stmt, ledger=ledger).targets == ()


class TestReport:
    def test_singulars(self) -> None:
        stmt = statement(
            trade(2, "MUND11", at(2026, 3, 1), "10", "30"), trade(3, "MUND11", at(2026, 3, 2), "4", "31", SELL)
        )
        lines = report_lines(plan_of(stmt))
        assert "1 venda importada sem IR retido" in lines
        stmt = statement(trade(2, "MUND11", at(2026, 3, 1), "10", "30"))
        assert report_lines(plan_of(stmt))[-1] == "1 operação importada sem taxas"

    def test_ignored_markets_by_name(self) -> None:
        stmt = statement(ignored=(("Mercado Fracionário", 2), ("Opção de Compra", 1)))
        assert report_lines(plan_of(stmt)) == [
            "Novas: 0",
            "Ignoradas por mercado: 3 (Mercado Fracionário 2, Opção de Compra 1)",
        ]

    def test_quantities_are_masked_with_the_amounts(self) -> None:
        fmt.hide_amounts(True)
        plan = plan_of(read_statement(FIXTURE), ledger=[txn("VWRA11", at(2026, 1, 9), "70")])
        line = next(line for line in report_lines(plan) if line.startswith("Total do dia"))
        assert "72" not in line and "70" not in line
        assert fmt.MASK in line

    def test_summary_for_the_dialogs(self) -> None:
        plan = plan_of(read_statement(FIXTURE))
        assert summary(plan, "negociacao.xlsx") == "21 operações de negociacao.xlsx\n18 compras, 3 vendas, 5 tickers"


DAY = at(2026, 1, 9)


def consolidated(kind: TransactionType = BUY, *, fees: str = "0.63", tax: str = "0", id: int = 1) -> Transaction:
    """18 at 106.68: the 5 + 13 below typed as one order with a rounded average price."""
    return txn("VWRA11", DAY, "18", "106.68", kind, id=id, fees=fees, tax=tax)


def executions(kind: TransactionType = BUY) -> Statement:
    return statement(trade(2, "VWRA11", DAY, "5", "106.70", kind), trade(3, "VWRA11", DAY, "13", "106.67", kind))


class TestValueMismatch:
    def test_without_replace_it_is_a_duplicate_with_a_line(self) -> None:
        plan = plan_of(executions(), ledger=[consolidated()])
        assert plan.inserts == ()
        assert len(plan.duplicates) == 2
        assert plan.removals == ()
        [item] = plan.mismatched
        assert (item.in_file, item.in_ledger) == (Decimal("1920.21"), Decimal("1920.24"))
        assert report_lines(plan) == [
            "Novas: 0",
            "Duplicadas: 2 (VWRA11 2)",
            "Valor do dia difere do ledger: VWRA11 (2026-01-09 compra: arquivo 1,920.21 × ledger 1,920.24); "  # noqa: RUF001
            "--replace troca pelos lançamentos da B3",
        ]

    def test_a_cent_of_difference_is_not_a_mismatch(self) -> None:
        ledger = [txn("VWRA11", DAY, "18", "106.6794")]  # 1920.2292: 0,0192 a mais que o arquivo
        assert plan_of(executions(), ledger=ledger).mismatched != ()
        ledger = [txn("VWRA11", DAY, "18", "106.6788")]  # 1920.2184: menos de um centavo
        assert plan_of(executions(), ledger=ledger).mismatched == ()

    def test_replace_swaps_the_row_and_splits_the_fees_by_value(self) -> None:
        typed = consolidated()
        plan = plan_of(executions(), ledger=[typed], replace=True)
        assert plan.removals == (typed,)
        assert [(t.row, t.shares, t.unit_price, t.fees, t.replacing) for t in plan.inserts] == [
            (2, Decimal("5"), Decimal("106.70"), Decimal("0.18"), True),
            (3, Decimal("13"), Decimal("106.67"), Decimal("0.45"), True),
        ]
        assert plan.new == ()
        assert plan.duplicates == ()
        assert report_lines(plan) == ["Novas: 0", "Substituídas: 1 (VWRA11 2026-01-09 compra: 1 → 2 lançamentos)"]
        [outcome] = ticker_outcomes(plan)
        assert (outcome.outcome, outcome.detail) == (Outcome.REPLACED, "2026-01-09 compra: 1 → 2 lançamentos")
        assert rows_accounted(plan) == 2

    def test_fee_rate_does_not_apply_to_a_replaced_group(self) -> None:
        plan = plan_of(executions(), ledger=[consolidated()], replace=True, fee_rate=FEE_RATE)
        assert [t.fees for t in plan.inserts] == [Decimal("0.18"), Decimal("0.45")]

    def test_the_tax_withheld_on_a_sale_is_split_too(self) -> None:
        opening = txn("VWRA11", at(2026, 1, 2), "18", "100", id=1)
        sale = consolidated(SELL, fees="0.63", tax="0.10", id=2)
        plan = plan_of(executions(SELL), ledger=[opening, sale], replace=True)
        assert plan.removals == (sale,)
        assert [(t.fees, t.tax_withheld) for t in plan.inserts] == [
            (Decimal("0.18"), Decimal("0.03")),
            (Decimal("0.45"), Decimal("0.07")),
        ]
        assert sum((t.tax_withheld for t in plan.inserts), Decimal("0")) == Decimal("0.10")
        assert not [line for line in report_lines(plan) if "IR retido" in line]  # o IR veio do ledger

    def test_a_different_quantity_still_skips_the_ticker(self) -> None:
        plan = plan_of(executions(), ledger=[txn("VWRA11", DAY, "17", "106.68")], replace=True)
        assert plan.inserts == () and plan.removals == ()
        assert [item.ticker for item in plan.divergent] == ["VWRA11"]

    def test_the_sale_guard_reads_the_ledger_after_the_swap(self) -> None:
        # Uma compra trocada por duas mais caras, e uma venda nova de tudo no dia
        # seguinte: coberta, porque a guarda soma as execucoes que entram.
        stmt = statement(*executions().trades, trade(4, "VWRA11", at(2026, 1, 12), "18", "107", SELL))
        plan = plan_of(stmt, ledger=[consolidated()], replace=True)
        assert plan.uncovered == ()
        assert [t.row for t in plan.inserts] == [2, 3, 4]
        assert [t.row for t in plan.new] == [4]

    def test_an_uncovered_sale_takes_the_replacement_out_too(self) -> None:
        stmt = statement(*executions().trades, trade(4, "VWRA11", at(2026, 1, 12), "20", "107", SELL))
        plan = plan_of(stmt, ledger=[consolidated()], replace=True)
        assert plan.inserts == () and plan.removals == ()
        assert plan.uncovered[0].missing == Decimal("2")


class TestAllot:
    def test_the_remainder_goes_to_the_last_part(self) -> None:
        assert allot(Decimal("0.63"), [Decimal("533.50"), Decimal("1386.71")]) == [Decimal("0.18"), Decimal("0.45")]
        assert allot(Decimal("0.10"), [Decimal("1"), Decimal("1"), Decimal("1")]) == [
            Decimal("0.03"),
            Decimal("0.03"),
            Decimal("0.04"),
        ]

    def test_no_part_is_negative(self) -> None:
        # As duas primeiras arredondam 0,005 para cima; a segunda so leva o que sobrou.
        parts = allot(Decimal("0.01"), [Decimal("1"), Decimal("1"), Decimal("0")])
        assert parts == [Decimal("0.01"), Decimal("0.00"), Decimal("0.00")]
        assert sum(parts, Decimal("0")) == Decimal("0.01")

    def test_nothing_to_split(self) -> None:
        assert allot(Decimal("0"), [Decimal("5"), Decimal("13")]) == [Decimal("0.00"), Decimal("0")]


# ------------------------------------------------------------------ banco


@pytest.fixture
def registered(conn: psycopg.Connection[DictRow]) -> psycopg.Connection[DictRow]:
    repo = AssetRepository(conn)
    for ticker in PORTFOLIO:
        repo.add(ticker, Decimal("0.2"), asset_type=AssetType.ETF)
    return conn


def ledger_rows(conn: psycopg.Connection[DictRow]) -> list[Transaction]:
    return TransactionRepository(conn).list()


class TestApply:
    def test_the_real_export_is_written_as_planned(self, registered: psycopg.Connection[DictRow]) -> None:
        plan = plan_import(registered, read_statement(FIXTURE), fee_rate=FEE_RATE)
        written = apply_import(registered, plan)
        assert len(written) == 21
        rows = ledger_rows(registered)
        assert len(rows) == 21
        vwra = [(t.shares, t.unit_price) for t in rows if t.ticker == "VWRA11" and t.date.date() == date(2026, 1, 9)]
        assert sorted(vwra) == [(Decimal("20"), Decimal("117.37")), (Decimal("52"), Decimal("117.34"))]
        assert all(t.date.astimezone(SAO_PAULO).hour == 0 for t in rows)
        assert sum((t.fees for t in rows), Decimal("0")) == Decimal("54.67")
        assert {t.tax_withheld for t in rows} == {Decimal("0")}

    def test_reimporting_inserts_nothing(self, registered: psycopg.Connection[DictRow]) -> None:
        apply_import(registered, plan_import(registered, read_statement(FIXTURE)))
        again = plan_import(registered, read_statement(FIXTURE))
        assert again.inserts == ()
        assert len(again.duplicates) == 21
        assert report_lines(again)[:2] == [
            "Novas: 0",
            "Duplicadas: 21 (AUVP11 2, B5P211 7, MUND11 2, NB1011 2, VWRA11 8)",
        ]
        assert apply_import(registered, again) == []
        assert len(ledger_rows(registered)) == 21

    def test_a_ledger_typed_by_hand_with_a_consolidated_order_matches(
        self, registered: psycopg.Connection[DictRow]
    ) -> None:
        repo = TransactionRepository(registered)
        stmt = read_statement(FIXTURE)
        for t in stmt.trades:
            if t.ticker == "VWRA11" and t.when == at(2026, 1, 9):
                continue
            if t.kind is BUY:
                repo.add_buy(t.ticker, t.when, shares=t.shares, unit_price=t.unit_price, fees=Decimal("0.5"))
            else:
                repo.add_sale(t.ticker, t.when, shares=t.shares, unit_price=t.unit_price)
        repo.add_buy("VWRA11", at(2026, 1, 9), shares=Decimal("72"), unit_price=Decimal("117.35"))
        plan = plan_import(registered, stmt)
        assert plan.inserts == ()
        assert len(plan.duplicates) == 21

    def test_a_divergent_ticker_is_left_out_and_the_rest_written(self, registered: psycopg.Connection[DictRow]) -> None:
        TransactionRepository(registered).add_buy(
            "VWRA11", at(2026, 1, 9), shares=Decimal("70"), unit_price=Decimal("117")
        )
        apply_import(registered, plan_import(registered, read_statement(FIXTURE)))
        rows = ledger_rows(registered)
        assert [t.shares for t in rows if t.ticker == "VWRA11"] == [Decimal("70")]
        assert len(rows) == 1 + 13

    def test_an_unregistered_ticker_writes_nothing_of_it(self, conn: psycopg.Connection[DictRow]) -> None:
        repo = AssetRepository(conn)
        for ticker in ("AUVP11", "B5P211", "MUND11", "NB1011"):
            repo.add(ticker, Decimal("0.2"), asset_type=AssetType.ETF)
        plan = plan_import(conn, read_statement(FIXTURE))
        apply_import(conn, plan)
        assert "VWRA11" not in {t.ticker for t in ledger_rows(conn)}
        assert len(ledger_rows(conn)) == 13
        assert AssetRepository(conn).get("VWRA11") is None  # nada e criado

    def test_targets_are_listed_and_never_changed(self, registered: psycopg.Connection[DictRow]) -> None:
        plan = plan_import(registered, read_statement(FIXTURE))
        apply_import(registered, plan)
        weights = {a.ticker: a.target_weight for a in AssetRepository(registered).list()}
        assert weights == dict.fromkeys(PORTFOLIO, Decimal("0.2"))
        assert plan_import(registered, read_statement(FIXTURE)).targets == ()  # reimportar nao lista

    def test_a_failure_in_the_middle_writes_nothing(
        self, registered: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plan = plan_import(registered, read_statement(FIXTURE))

        def explode(*_: Any, **__: Any) -> Transaction:
            raise ValidationError("banco fora do ar")

        monkeypatch.setattr(TransactionRepository, "add_sale", explode)
        with pytest.raises(ValidationError):
            apply_import(registered, plan)
        assert ledger_rows(registered) == []

    def test_a_ledger_changed_since_the_preview_writes_nothing(self, registered: psycopg.Connection[DictRow]) -> None:
        plan = plan_import(registered, read_statement(FIXTURE))
        TransactionRepository(registered).add_buy(
            "MUND11", at(2026, 10, 6), shares=Decimal("81"), unit_price=Decimal("95.27")
        )
        with pytest.raises(ImportStaleError, match="o ledger mudou desde a prévia: nada foi gravado"):
            apply_import(registered, plan)
        assert len(ledger_rows(registered)) == 1


class TestApplyReplace:
    @pytest.fixture
    def typed(self, registered: psycopg.Connection[DictRow]) -> Transaction:
        return TransactionRepository(registered).add_buy(
            "VWRA11", DAY, shares=Decimal("18"), unit_price=Decimal("106.68"), fees=Decimal("0.63")
        )

    def test_without_replace_nothing_changes(self, registered: psycopg.Connection[DictRow], typed: Transaction) -> None:
        plan = plan_import(registered, executions())
        assert apply_import(registered, plan) == []
        assert ledger_rows(registered) == [typed]

    def test_the_row_leaves_and_the_executions_come_in(
        self, registered: psycopg.Connection[DictRow], typed: Transaction
    ) -> None:
        plan = plan_import(registered, executions(), replace=True)
        written = apply_import(registered, plan)
        rows = ledger_rows(registered)
        assert rows == written
        assert typed.id not in {t.id for t in rows}
        assert [(t.shares, t.unit_price, t.fees) for t in rows] == [
            (Decimal("5"), Decimal("106.70"), Decimal("0.18")),
            (Decimal("13"), Decimal("106.67"), Decimal("0.45")),
        ]
        assert sum((t.fees for t in rows), Decimal("0")) == typed.fees

    def test_reimporting_after_the_swap_adds_nothing(
        self, registered: psycopg.Connection[DictRow], typed: Transaction
    ) -> None:
        apply_import(registered, plan_import(registered, executions(), replace=True))
        again = plan_import(registered, executions(), replace=True)
        assert again.inserts == () and again.removals == () and again.replaced == ()
        assert len(again.duplicates) == 2

    def test_a_later_sale_does_not_block_the_swap(
        self, registered: psycopg.Connection[DictRow], typed: Transaction
    ) -> None:
        # Removida sozinha (bogle transaction remove), a compra seria recusada:
        # a venda depois dela ficaria descoberta. Na troca, as execucoes entram
        # na mesma transacao.
        TransactionRepository(registered).add_sale(
            "VWRA11", at(2026, 2, 1), shares=Decimal("18"), unit_price=Decimal("110")
        )
        apply_import(registered, plan_import(registered, executions(), replace=True))
        assert len(ledger_rows(registered)) == 3

    def test_a_sale_keeps_its_tax_withheld(self, registered: psycopg.Connection[DictRow]) -> None:
        repo = TransactionRepository(registered)
        repo.add_buy("VWRA11", at(2026, 1, 2), shares=Decimal("18"), unit_price=Decimal("100"))
        repo.add_sale(
            "VWRA11",
            DAY,
            shares=Decimal("18"),
            unit_price=Decimal("106.68"),
            fees=Decimal("0.63"),
            tax_withheld=Decimal("0.10"),
        )
        apply_import(registered, plan_import(registered, executions(SELL), replace=True))
        sales = [t for t in ledger_rows(registered) if t.transaction_type is SELL]
        assert [(t.fees, t.tax_withheld) for t in sales] == [
            (Decimal("0.18"), Decimal("0.03")),
            (Decimal("0.45"), Decimal("0.07")),
        ]

    def test_a_failure_in_the_middle_removes_nothing(
        self, registered: psycopg.Connection[DictRow], typed: Transaction, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plan = plan_import(registered, executions(), replace=True)
        calls: list[int] = []
        original = TransactionRepository.add_buy

        def second_fails(self: TransactionRepository, *args: Any, **kwargs: Any) -> Transaction:
            calls.append(1)
            if len(calls) == 2:
                raise ValidationError("banco fora do ar")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(TransactionRepository, "add_buy", second_fails)
        with pytest.raises(ValidationError):
            apply_import(registered, plan)
        assert ledger_rows(registered) == [typed]

    def test_a_removed_row_that_changed_since_the_preview_is_refused(
        self, registered: psycopg.Connection[DictRow], typed: Transaction
    ) -> None:
        plan = plan_import(registered, executions(), replace=True)
        with registered.cursor() as cur:
            cur.execute(
                "UPDATE transactions SET fees = 0.70, total_cost = total_cost + 0.07 WHERE id = %s", (typed.id,)
            )
        with pytest.raises(ImportStaleError):
            apply_import(registered, plan)
        assert [t.fees for t in ledger_rows(registered)] == [Decimal("0.70")]

    def test_a_removed_row_retyped_since_the_preview_is_refused(
        self, registered: psycopg.Connection[DictRow], typed: Transaction
    ) -> None:
        # Mesmo conteudo, outro id: as insercoes seriam as mesmas, a remocao nao.
        plan = plan_import(registered, executions(), replace=True)
        repo = TransactionRepository(registered)
        repo.delete(typed.id)
        again = repo.add_buy("VWRA11", DAY, shares=Decimal("18"), unit_price=Decimal("106.68"), fees=Decimal("0.63"))
        with pytest.raises(ImportStaleError):
            apply_import(registered, plan)
        assert ledger_rows(registered) == [again]
