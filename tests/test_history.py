"""Tests for ``bogle history`` (issue #25)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import DictRow
from typer.testing import CliRunner

from bogle.cli import app
from bogle.domain.errors import ValidationError
from bogle.reports.history import HistoryReport, compute_history
from bogle.reports.valuation import PatrimonyPoint
from bogle.repositories.assets import AssetRepository
from bogle.repositories.transactions import TransactionRepository
from tests.test_valuation import FakeYfinance, bar, make_dispatcher

TODAY = date(2026, 7, 20)


@pytest.fixture
def seeded(conn: psycopg.Connection[DictRow]) -> None:
    AssetRepository(conn).add("PETR4", Decimal("0.5"))
    TransactionRepository(conn).add_buy(
        "PETR4", shares=Decimal("10"), unit_price=Decimal("20"), date=datetime(2026, 6, 22, 12, tzinfo=UTC)
    )


class TestComputeHistory:
    def test_series_with_gaps_carries_last_close(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: object
    ) -> None:
        history = {"PETR4.SA": [bar("2026-06-22", "20"), bar("2026-07-17", "25")]}
        dispatcher = make_dispatcher(tmp_path, yfinance=FakeYfinance(history))
        report = compute_history(conn, dispatcher, period="12m", today=TODAY)
        assert report.granularity == "daily"
        assert report.points[0].date == date(2026, 6, 22)  # ancora na 1a transacao
        assert report.points[0].value == Decimal("200")
        # Fim de semana / feriado usa o ultimo fechamento disponivel.
        by_date = {p.date: p.value for p in report.points}
        assert by_date[date(2026, 6, 28)] == Decimal("200")  # domingo
        assert by_date[TODAY] == Decimal("250")

    def test_all_uses_monthly_granularity(
        self, conn: psycopg.Connection[DictRow], seeded: None, tmp_path: object
    ) -> None:
        history = {"PETR4.SA": [bar("2026-06-22", "20"), bar("2026-07-17", "25")]}
        dispatcher = make_dispatcher(tmp_path, yfinance=FakeYfinance(history))
        report = compute_history(conn, dispatcher, period="all", today=TODAY)
        assert report.granularity == "monthly"
        assert report.points[0].date == date(2026, 6, 22)
        assert report.points[-1].date == TODAY

    def test_no_transactions_is_friendly(self, conn: psycopg.Connection[DictRow], tmp_path: object) -> None:
        with pytest.raises(ValidationError, match="Nenhuma transacao"):
            compute_history(conn, make_dispatcher(tmp_path), period="12m", today=TODAY)


class TestCli:
    @pytest.fixture
    def runner(self, conn: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch) -> CliRunner:
        report = HistoryReport(
            points=[
                PatrimonyPoint(date(2026, 5, 22), Decimal("200")),
                PatrimonyPoint(date(2026, 6, 22), Decimal("210")),
                PatrimonyPoint(date(2026, 7, 20), Decimal("250")),
            ],
            granularity="monthly",
            excluded=["TESOURO SELIC 2029"],
        )
        monkeypatch.setattr("bogle.cli.history.default_dispatcher", lambda: None)
        monkeypatch.setattr("bogle.cli.history.compute_history", lambda conn, dispatcher, *, period, today: report)
        return CliRunner()

    def test_table_shows_delta_vs_previous_point(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["history", "--no-chart"])
        assert result.exit_code == 0, result.output
        assert "Evolucao do patrimonio" in result.stdout
        assert "+10.00" in result.stdout  # 200 -> 210
        assert "+5.00%" in result.stdout
        assert "+40.00" in result.stdout  # 210 -> 250
        assert "TESOURO SELIC 2029" in result.stdout

    def test_chart_renders(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["history"])
        assert result.exit_code == 0, result.output

    def test_output_writes_interactive_html(self, runner: CliRunner, tmp_path: Path) -> None:
        out = tmp_path / "history.html"
        result = runner.invoke(app, ["history", "--output", str(out), "--no-open"])
        assert result.exit_code == 0, result.output
        assert f"grafico salvo em {out}" in result.stdout
        assert out.exists()
        assert "Patrimonio" in out.read_text(encoding="utf-8")

    def test_invalid_period(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["history", "--period", "ytd"])
        assert result.exit_code != 0
