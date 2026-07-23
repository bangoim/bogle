from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
import pytest
from psycopg.rows import DictRow

from bogle.domain.assets import AssetType, Indexer
from bogle.repositories.assets import AssetRepository

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BOGLE_BIN = PROJECT_ROOT / ".venv" / "bin" / "bogle"
SAO_PAULO = ZoneInfo("America/Sao_Paulo")


@pytest.fixture(autouse=True)
def _truncate_for_cli(conn: psycopg.Connection) -> Iterator[None]:
    """O conftest já trunca antes do teste; CLI roda em subprocess e
    cria seu próprio conn, então só usamos esta fixture pra reaproveitar
    a limpeza."""
    yield


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()  # já tem BOGLE_DATABASE_URL setado pelo conftest
    return subprocess.run(
        [str(BOGLE_BIN), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(PROJECT_ROOT),
        check=False,
    )


def test_list_empty() -> None:
    result = run_cli("list")
    assert result.returncode == 0
    assert "Nenhum ativo cadastrado" in result.stdout


def test_add_success() -> None:
    result = run_cli("add", "VTI", "-w", "0.5")
    assert result.returncode == 0
    assert "VTI" in result.stdout
    assert "50.00%" in result.stdout


def test_add_duplicate_raises_with_exit_1() -> None:
    assert run_cli("add", "VTI", "-w", "0.5").returncode == 0
    result = run_cli("add", "VTI", "-w", "0.3")
    assert result.returncode == 1
    assert "ja existe" in result.stderr


def test_update_success() -> None:
    assert run_cli("add", "VTI", "-w", "0.5").returncode == 0
    result = run_cli("update", "VTI", "-w", "0.7")
    assert result.returncode == 0
    assert "70.00%" in result.stdout


def test_update_not_found() -> None:
    result = run_cli("update", "XYZ", "-w", "0.1")
    assert result.returncode == 1
    assert "nao encontrado" in result.stderr


def test_update_type_variable_to_variable() -> None:
    assert run_cli("add", "VWRA11", "-w", "0.5", "-t", "stock").returncode == 0
    result = run_cli("update", "VWRA11", "-t", "etf")
    assert result.returncode == 0
    assert "ETF" in result.stdout
    assert "50.00%" in result.stdout  # peso preservado


def test_update_type_and_weight_together() -> None:
    assert run_cli("add", "VWRA11", "-w", "0.5", "-t", "stock").returncode == 0
    result = run_cli("update", "VWRA11", "-t", "fii", "-w", "0.3")
    assert result.returncode == 0
    assert "FII" in result.stdout
    assert "30.00%" in result.stdout


def test_update_type_to_fixed_income_rejected() -> None:
    assert run_cli("add", "VWRA11", "-w", "0.5").returncode == 0
    result = run_cli("update", "VWRA11", "-t", "cdb")
    assert result.returncode == 1
    assert "metadados" in result.stderr
    assert "bogle remove" in result.stderr


def test_update_nothing_to_do() -> None:
    assert run_cli("add", "VWRA11", "-w", "0.5").returncode == 0
    result = run_cli("update", "VWRA11")
    assert result.returncode == 1
    assert "Nada para atualizar" in result.stderr


def test_remove_success() -> None:
    assert run_cli("add", "VTI", "-w", "0.5").returncode == 0
    result = run_cli("remove", "VTI")
    assert result.returncode == 0
    assert "VTI" in result.stdout


def test_validation_weight_out_of_range() -> None:
    result = run_cli("add", "ABC", "-w", "1.5")
    assert result.returncode == 1
    assert "deve estar em (0, 1]" in result.stderr


def test_validation_weight_not_decimal() -> None:
    result = run_cli("add", "ABC", "-w", "foo")
    assert result.returncode == 1
    assert "deve ser um numero decimal" in result.stderr


# ---------------------------------------------------------------------------
# Validacao por asset_type (issue 2.2)
# ---------------------------------------------------------------------------


def test_add_stock_with_explicit_type() -> None:
    result = run_cli("add", "VTI", "--type", "STOCK", "--weight", "0.6")
    assert result.returncode == 0
    assert "VTI" in result.stdout
    assert "60.00%" in result.stdout


def test_add_cdb_full_metadata(conn: psycopg.Connection[DictRow]) -> None:
    result = run_cli(
        "add",
        "CDB-XP-2027",
        "--type",
        "CDB",
        "--weight",
        "0.1",
        "--issuer",
        "XP Investimentos",
        "--indexer",
        "CDI",
        "--rate",
        "1.10",
        "--purchase-date",
        "2026-04-01",
        "--maturity-date",
        "2027-04-01",
        "--no-daily-liquidity",
    )
    assert result.returncode == 0
    assert "CDB-XP-2027" in result.stdout
    assert "10.00%" in result.stdout

    # Persistencia: garante que a fiacao CLI -> repository nao perde/troca campos.
    asset = AssetRepository(conn).get("CDB-XP-2027")
    assert asset is not None
    assert asset.asset_type == AssetType.CDB
    assert asset.issuer == "XP Investimentos"
    assert asset.indexer == Indexer.CDI
    assert asset.rate == Decimal("1.10")
    assert asset.is_prefixed is False
    assert asset.daily_liquidity is False
    assert asset.purchase_date == datetime(2026, 4, 1, tzinfo=SAO_PAULO)
    assert asset.maturity_date == datetime(2027, 4, 1, tzinfo=SAO_PAULO)


def test_add_tesouro_prefixado(conn: psycopg.Connection[DictRow]) -> None:
    result = run_cli(
        "add",
        "TESOURO-PRE-2029",
        "--type",
        "TESOURO",
        "--weight",
        "0.2",
        "--prefixed",
        "--rate",
        "0.12",
        "--purchase-date",
        "2026-01-10",
        "--maturity-date",
        "2029-01-01",
    )
    assert result.returncode == 0
    assert "TESOURO-PRE-2029" in result.stdout

    asset = AssetRepository(conn).get("TESOURO-PRE-2029")
    assert asset is not None
    assert asset.asset_type == AssetType.TESOURO
    assert asset.is_prefixed is True
    assert asset.indexer is None
    assert asset.issuer is None
    assert asset.daily_liquidity is None
    assert asset.purchase_date == datetime(2026, 1, 10, tzinfo=SAO_PAULO)


def test_add_caixinha_daily_liquidity_without_maturity(conn: psycopg.Connection[DictRow]) -> None:
    result = run_cli(
        "add",
        "CAIXINHA-NU",
        "--type",
        "CAIXINHA",
        "--weight",
        "0.05",
        "--issuer",
        "Nubank",
        "--indexer",
        "CDI",
        "--rate",
        "1.0",
        "--purchase-date",
        "2026-02-01",
        "--daily-liquidity",
    )
    assert result.returncode == 0

    asset = AssetRepository(conn).get("CAIXINHA-NU")
    assert asset is not None
    assert asset.daily_liquidity is True
    assert asset.maturity_date is None
    assert asset.is_prefixed is False


def test_add_explicit_no_prefixed_requires_indexer() -> None:
    result = run_cli(
        "add",
        "CDB-NP",
        "--type",
        "CDB",
        "--weight",
        "0.05",
        "--no-prefixed",
        "--issuer",
        "Banco W",
        "--rate",
        "1.0",
        "--purchase-date",
        "2026-02-01",
        "--daily-liquidity",
    )
    assert result.returncode == 1
    assert "--indexer e obrigatorio para CDB pos-fixado" in result.stderr


def test_add_type_and_indexer_are_case_insensitive() -> None:
    result = run_cli(
        "add",
        "TES-IPCA-2035",
        "--type",
        "tesouro",
        "--indexer",
        "ipca+",
        "--weight",
        "0.1",
        "--rate",
        "0.065",
        "--purchase-date",
        "2026-01-10",
        "--maturity-date",
        "2035-05-15",
    )
    assert result.returncode == 0
    assert "TES-IPCA-2035" in result.stdout


def test_add_missing_fields_listed_all_at_once() -> None:
    result = run_cli("add", "CDB-X", "--type", "CDB", "--weight", "0.1")
    assert result.returncode == 1
    for option in ("--issuer", "--indexer", "--rate", "--daily-liquidity", "--purchase-date"):
        assert option in result.stderr


def test_add_irrelevant_field_for_type_raises() -> None:
    result = run_cli("add", "VTI", "--weight", "0.1", "--issuer", "Vanguard")
    assert result.returncode == 1
    assert "--issuer nao se aplica ao tipo STOCK" in result.stderr


def test_add_invalid_date_format() -> None:
    result = run_cli(
        "add",
        "CDB-Y",
        "--type",
        "CDB",
        "--weight",
        "0.1",
        "--issuer",
        "Banco Y",
        "--indexer",
        "CDI",
        "--rate",
        "1.0",
        "--purchase-date",
        "01/04/2026",
        "--daily-liquidity",
    )
    assert result.returncode == 1
    assert "--purchase-date deve ser uma data ISO" in result.stderr


def test_add_parse_error_does_not_hide_missing_fields() -> None:
    # --rate malformado + --issuer faltando: ambos na mesma mensagem.
    result = run_cli(
        "add",
        "CDB-AGG",
        "--type",
        "CDB",
        "--weight",
        "0.05",
        "--rate",
        "abc",
        "--indexer",
        "CDI",
        "--purchase-date",
        "2026-01-01",
        "--daily-liquidity",
    )
    assert result.returncode == 1
    assert "--rate deve ser um numero decimal" in result.stderr
    assert "--issuer e obrigatorio para CDB" in result.stderr


def test_add_rate_overflow_is_friendly() -> None:
    # Sem o limite do parser, NUMERIC(10, 6) estouraria com traceback cru.
    result = run_cli(
        "add",
        "CDB-BIG",
        "--type",
        "CDB",
        "--weight",
        "0.05",
        "--issuer",
        "Banco B",
        "--indexer",
        "CDI",
        "--rate",
        "100000",
        "--purchase-date",
        "2026-01-01",
        "--daily-liquidity",
    )
    assert result.returncode == 1
    assert "--rate deve estar em (0, 10000)" in result.stderr
    assert "Traceback" not in result.stderr
