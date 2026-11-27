"""Tests for ``bogle withdraw`` (issue #80): JSON/table rendering (unit) and the CLI
flow with an injected portfolio (no network, but the database for the settings).
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator

import psycopg
import pytest
from psycopg.rows import DictRow
from rich.console import Console
from typer.testing import CliRunner

from bogle.cli import app
from bogle.cli.withdraw import _render, _withdrawal_json
from bogle.domain.assets import AssetType
from bogle.rebalancing import WithdrawalSuggestion, suggest_withdrawal
from bogle.settings import BIRTH_YEAR, LAST_REBALANCE_DATE, get_setting, set_value
from tests.test_withdraw import D, position, retirement_portfolio, summary_of


def sample_withdrawal() -> WithdrawalSuggestion:
    """O saque de 6.000 da carteira de ``test_withdraw``: NB1011 em saida (etapa
    1) e MUND11 acima do alvo (etapa 2), antes da aposentadoria."""
    return suggest_withdrawal(retirement_portfolio(), D("6000"), {"NB1011"}, retired=False)


def render(suggestion: WithdrawalSuggestion) -> str:
    buffer = io.StringIO()
    _render(suggestion, Console(file=buffer, width=200))
    return buffer.getvalue()


class TestJson:
    def test_carries_each_sale_and_the_totals(self) -> None:
        data = _withdrawal_json(sample_withdrawal())
        json.dumps(data)  # must not raise
        assert data["amount"] == "6000"
        assert data["retired"] is False
        nb, mund = data["items"]
        assert nb["ticker"] == "NB1011"
        assert nb["stages"] == ["EXITING"]
        assert nb["quantity"] == "112"
        assert nb["gross"] == "5600"
        assert nb["income_tax"] == "168"
        assert nb["net"] == "5432"
        assert mund["stages"] == ["EXCESS"]
        assert data["totals"] == {
            "gross": "6000",
            "income_tax": "180",
            "net": "5820",
            "residual": "0",
            "net_vs_amount": "-180",
        }
        assert data["warnings"][0] == "Antes da aposentadoria: na acumulação a política é não vender"

    def test_fixed_income_quantity_is_null(self) -> None:
        summary = summary_of(
            position("TESOURO-IPCA-2035", price="3000", quantity="10", target="0.20", asset_type=AssetType.TESOURO),
            position("MUND11", price="100", quantity="300", target="0.80", average="80"),
        )
        data = _withdrawal_json(suggest_withdrawal(summary, D("1000")))
        assert data["items"][0]["quantity"] is None
        assert data["items"][0]["income_tax"] is None  # sem custo medio


class TestTableRender:
    def test_columns_in_order(self) -> None:
        out = render(sample_withdrawal())
        header = next(line for line in out.splitlines() if "Ticker" in line)
        order = ["Preço", "Qtde", "Bruto", "IR (est.)", "Líquido", "Target", "Peso atual", "Peso após"]
        assert [header.index(name) for name in order] == sorted(header.index(name) for name in order)

    def test_totals_compare_the_net_with_the_amount(self) -> None:
        out = render(sample_withdrawal())
        assert "Bruto: 6,000.00 / IR (est.): 180.00 / Líquido: 5,820.00" in out
        assert "Saque: 6,000.00 / Resíduo (cotas inteiras): 0.00 / Líquido vs saque: -180.00" in out

    def test_warnings_are_numbered(self) -> None:
        out = render(sample_withdrawal())
        assert "Atenção:\n1. Antes da aposentadoria: na acumulação a política é não vender" in out
        assert "2. NB1011 em saída na trajetória: vendido primeiro" in out


@pytest.fixture
def clean(conn: psycopg.Connection[DictRow]) -> Iterator[psycopg.Connection[DictRow]]:
    yield conn
    conn.execute("TRUNCATE glide_path_points, user_settings")
    conn.commit()


class TestCliFlow:
    """CLI com carteira injetada: sem rede, mas com banco (as configuracoes)."""

    @pytest.fixture
    def runner(self, clean: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch) -> CliRunner:
        summary = retirement_portfolio()
        monkeypatch.setattr("bogle.cli.withdraw.default_dispatcher", lambda: None)
        monkeypatch.setattr("bogle.withdrawal.get_portfolio_summary", lambda conn, dispatcher, **_: summary)
        return CliRunner()

    def test_json_output(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["withdraw", "1000", "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        # Sem trajetoria nada esta em saida: o NB1011 vende pelo maior excesso.
        [nb] = data["items"]
        assert nb["ticker"] == "NB1011"
        assert nb["stages"] == ["EXCESS"]
        assert nb["quantity"] == "20"

    def test_without_birth_year_it_warns(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["withdraw", "1000"])
        assert result.exit_code == 0, result.output
        assert "Sem ano de nascimento: tratado como acumulação" in result.stdout

    def test_in_retirement_there_is_no_policy_warning(
        self, runner: CliRunner, clean: psycopg.Connection[DictRow]
    ) -> None:
        set_value(clean, BIRTH_YEAR, 1940)
        result = runner.invoke(app, ["withdraw", "1000,50"])
        assert result.exit_code == 0, result.output
        assert "Atenção" not in result.stdout
        assert "Saque: 1.000,50" in result.stdout  # o separador default da CLI

    def test_records_nothing(self, runner: CliRunner, clean: psycopg.Connection[DictRow]) -> None:
        result = runner.invoke(app, ["withdraw", "1000"])
        assert result.exit_code == 0, result.output
        assert get_setting(clean, LAST_REBALANCE_DATE) is None

    @pytest.mark.parametrize("amount", ["0", "abc", "200000"])
    def test_an_invalid_amount_fails(self, runner: CliRunner, amount: str) -> None:
        result = runner.invoke(app, ["withdraw", amount])
        assert result.exit_code != 0
