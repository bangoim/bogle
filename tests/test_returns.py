"""Tests for ``bogle return`` (issue #27): windowed TWR engine and CLI rendering."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from psycopg.rows import DictRow
from typer.testing import CliRunner

from bogle.cli import app
from bogle.domain.errors import ValidationError
from bogle.reports.returns import PeriodReturn, ReturnsReport, compute_returns
from bogle.reports.valuation import INCONSISTENT_LEDGER, NO_SOURCE
from bogle.repositories.assets import AssetRepository
from bogle.repositories.transactions import TransactionRepository
from bogle.settings import DECIMAL_SEPARATOR, set_setting
from tests.test_valuation import FakeYfinance, bar, make_dispatcher

TODAY = date(2026, 7, 20)


@pytest.fixture
def seeded(conn: psycopg.Connection[DictRow]) -> None:
    AssetRepository(conn).add("PETR4", Decimal("0.5"))
    # Meio-dia UTC: a sessao le TIMESTAMPTZ em America/Sao_Paulo e meia-noite UTC
    # regrediria a data local para o dia anterior a primeira barra do fake.
    TransactionRepository(conn).add_buy(
        "PETR4", shares=Decimal("10"), unit_price=Decimal("20"), date=datetime(2025, 1, 6, 12, tzinfo=UTC)
    )


HISTORY = {
    "PETR4.SA": [
        bar("2025-01-06", "20"),  # compra
        bar("2025-07-18", "22"),  # ~12m atras
        bar("2026-06-19", "24"),  # ~1m atras
        bar("2026-07-17", "25"),  # hoje
    ]
}


class TestComputeReturns:
    def test_three_windows_share_one_valuation(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: object
    ) -> None:
        dispatcher = make_dispatcher(tmp_path, yfinance=FakeYfinance(dict(HISTORY)))
        report = compute_returns(conn, dispatcher, today=TODAY)
        by_period = {row.period: row for row in report.rows}
        assert by_period["total"].start == date(2025, 1, 6)
        assert by_period["total"].twr == Decimal("0.25")  # 20 -> 25
        assert by_period["12m"].twr == Decimal("25") / Decimal("22") - 1  # 22 -> 25
        assert by_period["1m"].twr == Decimal("25") / Decimal("24") - 1  # 24 -> 25
        assert report.excluded == []

    def test_an_inconsistent_ledger_is_excluded_with_that_reason(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: object
    ) -> None:
        # Venda datada antes da compra: "sem historico de precos" seria mentira,
        # o provedor tem o historico; e o ledger que precisa de conserto.
        AssetRepository(conn).add("VALE3", Decimal("0.3"))
        transactions = TransactionRepository(conn)
        transactions.add_sale(
            "VALE3", shares=Decimal("5"), unit_price=Decimal("60"), date=datetime(2025, 1, 6, 12, tzinfo=UTC)
        )
        transactions.add_buy(
            "VALE3", shares=Decimal("10"), unit_price=Decimal("60"), date=datetime(2025, 2, 3, 12, tzinfo=UTC)
        )
        dispatcher = make_dispatcher(tmp_path, yfinance=FakeYfinance(dict(HISTORY)))
        report = compute_returns(conn, dispatcher, periods=("total",), today=TODAY)
        assert report.excluded == ["VALE3"]
        assert report.excluded_reasons == {"VALE3": INCONSISTENT_LEDGER}
        assert report.rows[0].twr == Decimal("0.25")  # so PETR4

    def test_window_older_than_inception_anchors_on_inception(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: object
    ) -> None:
        dispatcher = make_dispatcher(tmp_path, yfinance=FakeYfinance(dict(HISTORY)))
        report = compute_returns(conn, dispatcher, periods=("2y",), today=TODAY)
        assert report.rows[0].start == date(2025, 1, 6)  # inicio < 1a transacao vira inception

    def test_index_comparison_and_errors(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: object
    ) -> None:
        histories = dict(HISTORY)
        histories["^BVSP"] = [bar("2025-01-06", "100000"), bar("2026-07-17", "120000")]
        dispatcher = make_dispatcher(tmp_path, yfinance=FakeYfinance(histories))
        report = compute_returns(conn, dispatcher, periods=("total",), indices=("IBOV", "IFIX"), today=TODAY)
        row = report.rows[0]
        assert row.index_returns["IBOV"] == Decimal("0.2")
        assert row.index_returns["IFIX"] is None
        assert "IFIX" in report.index_errors

    def test_no_transactions_is_friendly(self, conn: psycopg.Connection[DictRow], tmp_path: object) -> None:
        with pytest.raises(ValidationError, match="Nenhuma transação"):
            compute_returns(conn, make_dispatcher(tmp_path), today=TODAY)


class TestCliRendering:
    @pytest.fixture
    def runner(self, conn: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch) -> CliRunner:
        report = ReturnsReport(
            rows=[
                PeriodReturn(
                    period="total",
                    start=date(2023, 1, 15),
                    end=TODAY,
                    twr=Decimal("0.473"),
                    index_returns={"CDI": Decimal("0.298")},
                ),
                PeriodReturn(
                    period="12m",
                    start=date(2025, 7, 20),
                    end=TODAY,
                    twr=Decimal("0.081"),
                    index_returns={"CDI": Decimal("0.112")},
                ),
            ],
            excluded=["TESOURO SELIC 2029"],
            index_errors={},
            excluded_reasons={"TESOURO SELIC 2029": NO_SOURCE},
        )
        monkeypatch.setattr("bogle.cli.returns.default_dispatcher", lambda: None)
        monkeypatch.setattr(
            "bogle.cli.returns.compute_returns",
            lambda conn, dispatcher, *, periods, indices, today: report,
        )
        return CliRunner()

    def test_panel_with_comparison(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["return", "--vs", "cdi"])
        assert result.exit_code == 0, result.output
        assert "Rentabilidade da carteira" in result.stdout
        assert "+47,30%" in result.stdout
        assert "vs CDI:" in result.stdout
        assert "+17,50 p.p." in result.stdout  # outperform
        assert "-3,10 p.p." in result.stdout  # underperform
        note = " ".join(result.stdout.split())  # a nota quebra na largura do terminal
        assert f"TWR não considera TESOURO SELIC 2029 ({NO_SOURCE})." in note

    def test_the_difference_follows_the_configured_separator(
        self, runner: CliRunner, conn: psycopg.Connection[DictRow]
    ) -> None:
        # A diferenca em p.p. era formatada na mao, entao ignorava
        # `decimal_separator`: quem configurou virgula via "+17.50 p.p.".
        set_setting(conn, DECIMAL_SEPARATOR, ",")
        conn.commit()
        result = runner.invoke(app, ["return", "--vs", "cdi"])
        assert result.exit_code == 0, result.output
        assert "+17,50 p.p." in result.stdout
        assert "+47,30%" in result.stdout

    def test_invalid_period_is_friendly(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["return", "--period", "3m"])
        assert result.exit_code != 0
        assert "--period inválido" in str(result.output) + str(result.exception)

    def test_vs_default_reads_setting(
        self, runner: CliRunner, conn: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, tuple[str, ...]] = {}

        def fake_compute(conn_, dispatcher, *, periods, indices, today):
            captured["indices"] = indices
            return ReturnsReport(rows=[], excluded=[], index_errors={})

        monkeypatch.setattr("bogle.cli.returns.compute_returns", fake_compute)
        result = runner.invoke(app, ["return", "--vs", "default"])
        assert result.exit_code == 0, result.output
        assert captured["indices"] == ("IBOV", "CDI")  # default_compare_indices default
