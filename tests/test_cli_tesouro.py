"""``bogle add`` for a TESOURO: the ticker is the title name (issue #93).

In-process runs (CliRunner) with the Tesouro Transparente CSV mocked over HTTP, so
the name that ``add`` accepts is the one the Position then prices — no network.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
import respx
from httpx import Response
from psycopg.rows import DictRow
from typer.testing import CliRunner

from bogle.cli import app
from bogle.data.cache import DiskCache
from bogle.data.dispatcher import PriceDispatcher
from bogle.domain.errors import ValidationError
from bogle.repositories.assets import AssetRepository
from bogle.repositories.transactions import TransactionRepository
from tests.tesouro_fakes import OfflineTitles
from tests.test_dispatcher import FakeBcb, FakeQuoteClient
from tests.test_tesouro import CSV_URL, csv_fixture, make_client

IPCA_2035 = ["--type", "TESOURO", "--weight", "0.2", "--indexer", "IPCA+", "--rate", "0.065"]
DATES_2035 = ["--purchase-date", "2026-01-10", "--maturity-date", "2035-05-15"]


@pytest.fixture
def runner(conn: psycopg.Connection[DictRow], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    # Um cliente novo por uso (quem confere o nome o fecha); o cache em disco e um so.
    monkeypatch.setattr("bogle.tesouro_title.default_source", lambda: make_client(tmp_path))
    return CliRunner()


def test_a_title_added_by_its_name_is_priced_in_the_position(
    runner: CliRunner, conn: psycopg.Connection[DictRow], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "bogle.cli.position.default_dispatcher",
        lambda: PriceDispatcher(
            brapi=FakeQuoteClient(),
            yfinance=FakeQuoteClient(),
            tesouro=make_client(tmp_path),
            bcb=FakeBcb(),
            quote_cache=DiskCache("quotes", base_dir=tmp_path),
        ),
    )
    with respx.mock:
        respx.get(CSV_URL).mock(return_value=Response(200, text=csv_fixture()))
        added = runner.invoke(app, ["add", "Tesouro IPCA+ 2035", *IPCA_2035, *DATES_2035])
        assert added.exit_code == 0, added.output
        assert added.stdout == "asset TESOURO IPCA+ 2035 (TESOURO) adicionado com peso 20.00%.\n"
        assert added.stderr == ""

        TransactionRepository(conn).add_buy(
            "TESOURO IPCA+ 2035",
            datetime(2026, 1, 10, tzinfo=UTC),
            shares=Decimal("2"),
            unit_price=Decimal("2300"),
        )
        result = runner.invoke(app, ["position", "--json"])
    assert result.exit_code == 0, result.output
    row = json.loads(result.stdout)["positions"][0]
    assert row["ticker"] == "TESOURO IPCA+ 2035"
    assert row["price"] == "2404.58"
    assert row["price_source"] == "tesouro"
    assert row["market_value"] == "4809.16"


def test_the_prefixed_title_of_the_readme_is_accepted(runner: CliRunner) -> None:
    with respx.mock:
        respx.get(CSV_URL).mock(return_value=Response(200, text=csv_fixture()))
        result = runner.invoke(
            app,
            [
                "add", "Tesouro Prefixado 2029", "--type", "TESOURO", "--weight", "0.1",
                "--prefixed", "--rate", "0.12", "--purchase-date", "2026-01-10", "--maturity-date", "2029-01-01",
            ],
        )  # fmt: skip
    assert result.exit_code == 0, result.output


def test_a_label_that_is_not_the_title_name_is_refused_with_the_titles_of_the_year(
    runner: CliRunner, conn: psycopg.Connection[DictRow]
) -> None:
    with respx.mock:
        respx.get(CSV_URL).mock(return_value=Response(200, text=csv_fixture()))
        result = runner.invoke(app, ["add", "TESOURO-IPCA-2035", *IPCA_2035, *DATES_2035])
    assert result.exit_code == 1
    assert isinstance(result.exception, ValidationError)
    assert str(result.exception) == (
        "'TESOURO-IPCA-2035' não é um título do Tesouro Transparente. "
        "Títulos com vencimento em 2035: Tesouro Educa+ 2035, Tesouro IPCA+ 2035, "
        "Tesouro IPCA+ com Juros Semestrais 2035, Tesouro Prefixado com Juros Semestrais 2035."
    )
    assert AssetRepository(conn).list() == []


def test_a_year_without_titles_is_refused_without_suggestions(
    runner: CliRunner, conn: psycopg.Connection[DictRow]
) -> None:
    with respx.mock:
        respx.get(CSV_URL).mock(return_value=Response(200, text=csv_fixture()))
        result = runner.invoke(
            app,
            ["add", "TESOURO-IPCA-2099", *IPCA_2035, "--purchase-date", "2026-01-10", "--maturity-date", "2099-05-15"],
        )
    assert result.exit_code == 1
    assert str(result.exception) == "'TESOURO-IPCA-2099' não é um título do Tesouro Transparente."
    assert AssetRepository(conn).list() == []


def test_without_the_title_list_the_asset_is_saved_with_a_warning(
    runner: CliRunner, conn: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("bogle.tesouro_title.default_source", OfflineTitles)
    result = runner.invoke(app, ["add", "TESOURO-IPCA-2035", *IPCA_2035, *DATES_2035])
    assert result.exit_code == 0, result.output
    assert result.stdout == "asset TESOURO-IPCA-2035 (TESOURO) adicionado com peso 20.00%.\n"
    assert result.stderr == (
        "aviso: não foi possível conferir o nome de 'TESOURO-IPCA-2035' no Tesouro Transparente; "
        "se não for o nome exato do título, ele não será precificado.\n"
    )
    assert AssetRepository(conn).get("TESOURO-IPCA-2035") is not None


def test_other_types_never_look_at_the_title_list(
    runner: CliRunner, conn: psycopg.Connection[DictRow], monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden() -> None:
        raise AssertionError("a lista de títulos não deveria ser consultada")

    monkeypatch.setattr("bogle.tesouro_title.default_source", forbidden)
    result = runner.invoke(app, ["add", "VTI", "--weight", "0.4"])
    assert result.exit_code == 0, result.output
