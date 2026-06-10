from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import psycopg
import pytest
from psycopg import errors as pg_errors
from psycopg.rows import DictRow

from bogle.db import get_connection
from bogle.domain.errors import (
    AssetNotFoundError,
    TransactionNotFoundError,
    ValidationError,
)
from bogle.domain.transactions import Transaction, TransactionType
from bogle.repositories.assets import AssetRepository
from bogle.repositories.transactions import TransactionRepository

D1 = datetime(2025, 1, 15, tzinfo=UTC)
D2 = datetime(2025, 2, 15, tzinfo=UTC)


@pytest.fixture
def petr4(repo: AssetRepository) -> None:
    repo.add("PETR4", Decimal("0.1"))


class TestAddBuy:
    def test_computed_fields_with_fees(self, trepo: TransactionRepository, petr4: None) -> None:
        tx = trepo.add_buy("PETR4", D1, Decimal("100"), Decimal("30"), fees=Decimal("5.5"))
        assert isinstance(tx, Transaction)
        assert tx.id > 0
        assert tx.transaction_type is TransactionType.BUY
        assert tx.shares == Decimal("100")
        assert tx.unit_price == Decimal("30")
        assert tx.total_investment == Decimal("3000")
        assert tx.fees == Decimal("5.5")
        assert tx.total_cost == Decimal("3005.5")
        assert tx.tax_withheld == Decimal("0")

    def test_computed_fields_without_fees(self, trepo: TransactionRepository, petr4: None) -> None:
        tx = trepo.add_buy("PETR4", D1, Decimal("200"), Decimal("25"))
        assert tx.total_investment == Decimal("5000")
        assert tx.fees == Decimal("0")
        assert tx.total_cost == Decimal("5000")

    def test_ticker_uppercased(self, trepo: TransactionRepository, petr4: None) -> None:
        tx = trepo.add_buy("petr4", D1, Decimal("50"), Decimal("60"))
        assert tx.ticker == "PETR4"

    def test_missing_asset_raises_friendly_error(self, trepo: TransactionRepository) -> None:
        with pytest.raises(AssetNotFoundError):
            trepo.add_buy("NOPE", D1, Decimal("10"), Decimal("10"))

    def test_invalid_values_listed_together(self, trepo: TransactionRepository, petr4: None) -> None:
        with pytest.raises(ValidationError) as exc:
            trepo.add_buy("PETR4", D1, Decimal("0"), Decimal("-1"), fees=Decimal("-2"))
        message = str(exc.value)
        assert "shares deve ser maior que zero" in message
        assert "unit_price deve ser maior que zero" in message
        assert "fees nao pode ser negativo" in message


class TestAddSale:
    def test_cost_is_only_fees(self, trepo: TransactionRepository, petr4: None) -> None:
        trepo.add_buy("PETR4", D1, Decimal("100"), Decimal("30"))
        tx = trepo.add_sale("PETR4", D2, Decimal("40"), Decimal("35"), fees=Decimal("2.5"))
        assert tx.transaction_type is TransactionType.SELL
        assert tx.shares == Decimal("40")  # quantidade vendida
        assert tx.total_investment == Decimal("1400")  # proventos brutos
        assert tx.total_cost == Decimal("2.5")  # custo da operacao = fees

    def test_dedo_duro_tax_withheld(self, trepo: TransactionRepository, petr4: None) -> None:
        tx = trepo.add_sale("PETR4", D2, Decimal("40"), Decimal("35"), tax_withheld=Decimal("0.07"))
        assert tx.tax_withheld == Decimal("0.07")

    def test_invalid_shares_raises(self, trepo: TransactionRepository, petr4: None) -> None:
        with pytest.raises(ValidationError, match="shares deve ser maior que zero"):
            trepo.add_sale("PETR4", D2, Decimal("-40"), Decimal("35"))

    def test_negative_tax_withheld_raises(self, trepo: TransactionRepository, petr4: None) -> None:
        with pytest.raises(ValidationError, match="tax_withheld nao pode ser negativo"):
            trepo.add_sale("PETR4", D2, Decimal("10"), Decimal("30"), tax_withheld=Decimal("-1"))


class TestIncome:
    def test_dividend(self, trepo: TransactionRepository, petr4: None) -> None:
        tx = trepo.add_dividend("PETR4", D1, Decimal("123.45"))
        assert tx.transaction_type is TransactionType.DIVIDEND
        assert tx.total_investment == Decimal("123.45")  # valor bruto recebido
        assert tx.shares == Decimal("0")
        assert tx.unit_price == Decimal("0")
        assert tx.fees == Decimal("0")
        assert tx.total_cost == Decimal("0")
        assert tx.tax_withheld == Decimal("0")

    def test_dividend_with_tax(self, trepo: TransactionRepository, petr4: None) -> None:
        tx = trepo.add_dividend("PETR4", D1, Decimal("100"), tax_withheld=Decimal("1"))
        assert tx.tax_withheld == Decimal("1")

    def test_jcp_requires_tax_withheld_argument(self, trepo: TransactionRepository, petr4: None) -> None:
        tx = trepo.add_jcp("PETR4", D1, Decimal("200"), Decimal("30"))
        assert tx.transaction_type is TransactionType.JCP
        assert tx.total_investment == Decimal("200")
        assert tx.tax_withheld == Decimal("30")  # 15% retido na fonte

    def test_rendimento_is_always_exempt(self, trepo: TransactionRepository, repo: AssetRepository) -> None:
        repo.add("MXRF11", Decimal("0.05"))
        tx = trepo.add_rendimento("MXRF11", D1, Decimal("80"))
        assert tx.transaction_type is TransactionType.RENDIMENTO
        assert tx.tax_withheld == Decimal("0")

    def test_interest(self, trepo: TransactionRepository, petr4: None) -> None:
        tx = trepo.add_interest("PETR4", D1, Decimal("55"), tax_withheld=Decimal("12.375"))
        assert tx.transaction_type is TransactionType.INTEREST
        assert tx.total_investment == Decimal("55")
        assert tx.tax_withheld == Decimal("12.375")

    @pytest.mark.parametrize("amount", [Decimal("0"), Decimal("-10")])
    def test_non_positive_amount_raises(self, trepo: TransactionRepository, petr4: None, amount: Decimal) -> None:
        with pytest.raises(ValidationError, match="amount deve ser maior que zero"):
            trepo.add_dividend("PETR4", D1, amount)

    def test_negative_tax_raises(self, trepo: TransactionRepository, petr4: None) -> None:
        with pytest.raises(ValidationError, match="tax_withheld nao pode ser negativo"):
            trepo.add_jcp("PETR4", D1, Decimal("100"), Decimal("-1"))

    def test_missing_asset_raises_friendly_error(self, trepo: TransactionRepository) -> None:
        with pytest.raises(AssetNotFoundError):
            trepo.add_dividend("NOPE", D1, Decimal("10"))


class TestList:
    def test_empty(self, trepo: TransactionRepository) -> None:
        assert trepo.list() == []

    def test_all_types_round_trip(self, trepo: TransactionRepository, repo: AssetRepository) -> None:
        repo.add("PETR4", Decimal("0.1"))
        repo.add("MXRF11", Decimal("0.05"))
        trepo.add_buy("PETR4", D1, Decimal("100"), Decimal("30"))
        trepo.add_sale("PETR4", D2, Decimal("50"), Decimal("35"))
        trepo.add_dividend("PETR4", D2, Decimal("10"))
        trepo.add_jcp("PETR4", D2, Decimal("20"), Decimal("3"))
        trepo.add_rendimento("MXRF11", D2, Decimal("8"))
        trepo.add_interest("PETR4", D2, Decimal("5"))

        types = {t.transaction_type for t in trepo.list()}
        assert types == set(TransactionType)

    def test_filter_by_ticker(self, trepo: TransactionRepository, repo: AssetRepository) -> None:
        repo.add("PETR4", Decimal("0.1"))
        repo.add("VALE3", Decimal("0.1"))
        trepo.add_buy("PETR4", D1, Decimal("100"), Decimal("30"))
        trepo.add_buy("VALE3", D1, Decimal("50"), Decimal("60"))
        rows = trepo.list("petr4")  # filtro tambem normaliza para uppercase
        assert len(rows) == 1
        assert rows[0].ticker == "PETR4"

    def test_chronological_order(self, trepo: TransactionRepository, petr4: None) -> None:
        trepo.add_buy("PETR4", D2, Decimal("10"), Decimal("30"))
        trepo.add_buy("PETR4", D1, Decimal("20"), Decimal("28"))
        rows = trepo.list("PETR4")
        assert [r.date for r in rows] == [D1, D2]

    def test_same_date_ordered_by_insertion(self, trepo: TransactionRepository, petr4: None) -> None:
        # Empate de data e o caso comum (varios eventos no mesmo dia);
        # o desempate por id garante ordem de insercao deterministica.
        first = trepo.add_buy("PETR4", D1, Decimal("10"), Decimal("30"))
        second = trepo.add_dividend("PETR4", D1, Decimal("5"))
        third = trepo.add_sale("PETR4", D1, Decimal("4"), Decimal("31"))
        assert [t.id for t in trepo.list("PETR4")] == [first.id, second.id, third.id]


class TestDelete:
    def test_existing(self, trepo: TransactionRepository, petr4: None) -> None:
        tx = trepo.add_buy("PETR4", D1, Decimal("100"), Decimal("30"))
        trepo.delete(tx.id)
        assert trepo.list() == []

    def test_removes_only_the_target_row(
        self, trepo: TransactionRepository, repo: AssetRepository, petr4: None
    ) -> None:
        repo.add("VALE3", Decimal("0.1"))
        target = trepo.add_buy("PETR4", D1, Decimal("100"), Decimal("30"))
        survivor_same_ticker = trepo.add_dividend("PETR4", D2, Decimal("10"))
        survivor_other_ticker = trepo.add_buy("VALE3", D1, Decimal("50"), Decimal("60"))

        trepo.delete(target.id)

        remaining = {t.id for t in trepo.list()}
        assert remaining == {survivor_same_ticker.id, survivor_other_ticker.id}

    def test_missing_raises(self, trepo: TransactionRepository) -> None:
        with pytest.raises(TransactionNotFoundError):
            trepo.delete(999_999)


class TestTransactionalBehaviour:
    def test_insert_is_committed_for_other_connections(self, trepo: TransactionRepository, petr4: None) -> None:
        tx = trepo.add_buy("PETR4", D1, Decimal("100"), Decimal("30"))
        # Outra conexao so enxerga a linha se o commit aconteceu de fato.
        other = get_connection()
        try:
            rows = TransactionRepository(other).list("PETR4")
        finally:
            other.close()
        assert tx.id in {t.id for t in rows}

    def test_connection_usable_after_failed_insert(self, trepo: TransactionRepository, petr4: None) -> None:
        # Sem o rollback do bloco transaction(), a conexao ficaria em
        # estado abortado (InFailedSqlTransaction) apos o erro de FK.
        with pytest.raises(AssetNotFoundError):
            trepo.add_buy("NOPE", D1, Decimal("10"), Decimal("10"))
        tx = trepo.add_buy("PETR4", D1, Decimal("10"), Decimal("10"))
        assert tx.id > 0


class TestNumericLimits:
    def test_field_overflow_is_friendly(self, trepo: TransactionRepository, petr4: None) -> None:
        # shares NUMERIC(20, 8) suporta |valor| < 10^12.
        with pytest.raises(ValidationError, match="excedem a precisao"):
            trepo.add_buy("PETR4", D1, Decimal("1e13"), Decimal("10"))

    def test_product_overflow_is_friendly(self, trepo: TransactionRepository, petr4: None) -> None:
        # Campos individualmente validos, mas o produto estoura NUMERIC(20, 4).
        with pytest.raises(ValidationError, match="excedem a precisao"):
            trepo.add_buy("PETR4", D1, Decimal("1e11"), Decimal("1e9"))

    @pytest.mark.parametrize("bad", [Decimal("NaN"), Decimal("Infinity")])
    def test_non_finite_values_are_friendly(self, trepo: TransactionRepository, petr4: None, bad: Decimal) -> None:
        with pytest.raises(ValidationError):
            trepo.add_buy("PETR4", D1, bad, Decimal("10"))
        with pytest.raises(ValidationError):
            trepo.add_dividend("PETR4", D1, bad)


class TestMigration003:
    def test_plain_insert_defaults_to_buy(
        self, conn: psycopg.Connection[DictRow], trepo: TransactionRepository, petr4: None
    ) -> None:
        # Linhas pre-003 (sem transaction_type) viram BUY via DEFAULT.
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO transactions
                    (ticker, purchase_date, shares, unit_price,
                     total_investment, fees, total_cost)
                VALUES ('PETR4', %s, 10, 30, 300, 0, 300)
                """,
                (D1,),
            )
        conn.commit()
        tx = trepo.list("PETR4")[0]
        assert tx.transaction_type is TransactionType.BUY
        assert tx.tax_withheld == Decimal("0")

    def test_check_constraint_rejects_income_with_shares(self, conn: psycopg.Connection[DictRow], petr4: None) -> None:
        with pytest.raises(pg_errors.CheckViolation), conn.transaction(), conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO transactions
                    (ticker, transaction_type, purchase_date, shares,
                     unit_price, total_investment, fees, total_cost)
                VALUES ('PETR4', 'DIVIDEND', %s, 10, 30, 300, 0, 0)
                """,
                (D1,),
            )
