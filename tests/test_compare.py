"""Tests for ``bogle compare`` (issue #26): base-100 series engine and CLI."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from psycopg.rows import DictRow
from typer.testing import CliRunner

from bogle.cli import app
from bogle.data.models import SeriesPoint
from bogle.domain.errors import ValidationError
from bogle.reports.compare import CompareReport, CompareSeries, compute_compare
from bogle.repositories.assets import AssetRepository
from bogle.repositories.transactions import TransactionRepository
from tests.test_valuation import FakeBcb, FakeYfinance, bar, make_dispatcher

TODAY = date(2026, 7, 20)


@pytest.fixture
def seeded(conn: psycopg.Connection[DictRow]) -> None:
    AssetRepository(conn).add("PETR4", Decimal("0.5"))
    TransactionRepository(conn).add_buy(
        "PETR4", shares=Decimal("10"), unit_price=Decimal("20"), date=datetime(2026, 6, 22, 12, tzinfo=UTC)
    )


HISTORY = {"PETR4.SA": [bar("2026-06-22", "20"), bar("2026-07-17", "25")]}


class TestComputeCompare:
    def test_portfolio_series_is_twr_based_100(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: object
    ) -> None:
        dispatcher = make_dispatcher(tmp_path, yfinance=FakeYfinance(dict(HISTORY)))
        report = compute_compare(conn, dispatcher, period="1m", indices=(), today=TODAY)
        carteira = report.series[0]
        assert carteira.name == "Carteira"
        assert carteira.levels[0] == Decimal("100")
        assert carteira.levels[-1] == Decimal("125")  # 20 -> 25
        assert carteira.accumulated_return == Decimal("0.25")

    def test_contribution_mid_window_is_not_performance(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: object
    ) -> None:
        # Aporte dobra a posicao no meio da janela com preco estavel: TWR fica 0.
        TransactionRepository(conn).add_buy(
            "PETR4", shares=Decimal("10"), unit_price=Decimal("20"), date=datetime(2026, 7, 1, 12, tzinfo=UTC)
        )
        history = {"PETR4.SA": [bar("2026-06-22", "20"), bar("2026-07-01", "20"), bar("2026-07-17", "20")]}
        dispatcher = make_dispatcher(tmp_path, yfinance=FakeYfinance(history))
        report = compute_compare(conn, dispatcher, period="1m", indices=(), today=TODAY)
        assert report.series[0].levels[-1] == Decimal("100")

    def test_index_series_normalized_and_errors_noted(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: object
    ) -> None:
        cdi = [SeriesPoint(date(2026, 6, 23), Decimal("0.001"))]
        histories = dict(HISTORY)
        dispatcher = make_dispatcher(tmp_path, yfinance=FakeYfinance(histories), bcb=FakeBcb(cdi=cdi))
        report = compute_compare(conn, dispatcher, period="1m", indices=("CDI", "IFIX"), today=TODAY)
        names = [s.name for s in report.series]
        assert names == ["Carteira", "CDI"]
        cdi_series = report.series[1]
        assert cdi_series.levels[0] == Decimal("100")
        assert cdi_series.levels[-1] == Decimal("100") * Decimal("1.001")
        assert "IFIX" in report.index_errors

    def test_no_transactions_is_friendly(self, conn: psycopg.Connection[DictRow], tmp_path: object) -> None:
        with pytest.raises(ValidationError, match="Nenhuma transacao"):
            compute_compare(conn, make_dispatcher(tmp_path), period="12m", indices=(), today=TODAY)

    def test_only_unvaluable_positions_is_friendly(self, conn: psycopg.Connection[DictRow], tmp_path: object) -> None:
        from bogle.domain.assets import AssetType

        AssetRepository(conn).add("TESOURO SELIC 2029", Decimal("0.5"), asset_type=AssetType.TESOURO)
        TransactionRepository(conn).add_buy(
            "TESOURO SELIC 2029",
            shares=Decimal("1"),
            unit_price=Decimal("1000"),
            date=datetime(2026, 6, 22, 12, tzinfo=UTC),
        )
        with pytest.raises(ValidationError, match="sem historico: TESOURO SELIC 2029"):
            compute_compare(conn, make_dispatcher(tmp_path), period="1m", indices=(), today=TODAY)


class TestCli:
    @pytest.fixture
    def runner(self, conn: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch) -> CliRunner:
        report = CompareReport(
            grid=[date(2026, 6, 22), date(2026, 7, 20)],
            series=[
                CompareSeries("Carteira", [Decimal("100"), Decimal("125")]),
                CompareSeries("CDI", [Decimal("100"), Decimal("101")]),
            ],
            excluded=[],
            index_errors={"IFIX": "Sem historico gratuito para 'IFIX' (simbolo IFIX.SA)."},
        )
        captured: dict[str, object] = {}
        self.captured = captured

        def fake_compute(conn_, dispatcher, *, period, indices, today):
            captured["indices"] = indices
            captured["period"] = period
            return report

        monkeypatch.setattr("bogle.cli.compare.default_dispatcher", lambda: None)
        monkeypatch.setattr("bogle.cli.compare.compute_compare", fake_compute)
        return CliRunner()

    def test_table_and_chart(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["compare", "--index", "cdi,ifix"])
        assert result.exit_code == 0, result.output
        assert "+25.00%" in result.stdout
        assert "+1.00%" in result.stdout
        assert "Sem historico gratuito" in result.stdout
        assert self.captured["indices"] == ("CDI", "IFIX")

    def test_no_chart_flag(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["compare", "--no-chart"])
        assert result.exit_code == 0, result.output
        assert "Base 100" not in result.stdout  # titulo do grafico ausente

    def test_default_indices_come_from_settings(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["compare", "--no-chart"])
        assert result.exit_code == 0, result.output
        assert self.captured["indices"] == ("CDI",)

    def test_invalid_period(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["compare", "--period", "3m"])
        assert result.exit_code != 0
