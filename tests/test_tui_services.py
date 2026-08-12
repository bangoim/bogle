"""Tests for the TUI's data-access layer (issues #73/#74).

Every screen test patches this module, so the layer itself needs its own
coverage: it runs against the real ``bogle_test`` database and pins what each
function actually writes — in particular ``record_income``, the only place that
routes an income type to its repository method.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from psycopg.rows import DictRow

from bogle import format as fmt
from bogle.domain.assets import AssetType
from bogle.domain.errors import AssetNotFoundError, TransactionNotFoundError, ValidationError
from bogle.domain.transactions import TransactionType
from bogle.repositories.assets import AssetRepository
from bogle.settings import DECIMAL_SEPARATOR, LAST_REBALANCE_DATE, REBALANCE_PERIOD_MONTHS, set_value
from bogle.tui import services

WHEN = datetime(2026, 3, 10, 12, tzinfo=UTC)


@pytest.fixture
def seeded(conn: psycopg.Connection[DictRow]) -> None:
    """Two registered assets, committed so the services' own connection sees them."""
    repo = AssetRepository(conn)
    repo.add("PETR4", Decimal("0.4"))
    repo.add("MXRF11", Decimal("0.1"), asset_type=AssetType.FII)


class TestTickers:
    def test_lists_the_registered_tickers(self, seeded: None) -> None:
        assert services.list_tickers() == ["MXRF11", "PETR4"]

    def test_empty_portfolio(self, conn: psycopg.Connection[DictRow]) -> None:
        assert services.list_tickers() == []


class TestRecordTrades:
    def test_buy_is_persisted_with_its_total_cost(self, seeded: None) -> None:
        transaction = services.record_buy(
            ticker="petr4",
            when=WHEN,
            shares=Decimal("100"),
            unit_price=Decimal("30.50"),
            fees=Decimal("5.20"),
        )
        assert transaction.id > 0
        assert transaction.ticker == "PETR4"  # normalizado pelo repositorio
        assert transaction.transaction_type is TransactionType.BUY
        assert transaction.total_investment == Decimal("3050")
        assert transaction.total_cost == Decimal("3055.20")

        [persisted] = services.load_transactions()
        assert persisted.id == transaction.id

    def test_sell_keeps_the_withheld_tax_and_costs_only_the_fees(self, seeded: None) -> None:
        services.record_buy(
            ticker="PETR4", when=WHEN, shares=Decimal("100"), unit_price=Decimal("30"), fees=Decimal("0")
        )
        transaction = services.record_sell(
            ticker="PETR4",
            when=WHEN,
            shares=Decimal("40"),
            unit_price=Decimal("35"),
            fees=Decimal("2.50"),
            tax_withheld=Decimal("0.07"),
        )
        assert transaction.transaction_type is TransactionType.SELL
        assert transaction.total_investment == Decimal("1400")
        assert transaction.total_cost == Decimal("2.50")
        assert transaction.tax_withheld == Decimal("0.07")

    def test_unknown_ticker_is_a_domain_error(self, conn: psycopg.Connection[DictRow]) -> None:
        with pytest.raises(AssetNotFoundError):
            services.record_buy(
                ticker="NOPE", when=WHEN, shares=Decimal("1"), unit_price=Decimal("1"), fees=Decimal("0")
            )

    def test_invalid_values_are_a_domain_error(self, seeded: None) -> None:
        with pytest.raises(ValidationError, match="shares deve ser maior que zero"):
            services.record_buy(
                ticker="PETR4", when=WHEN, shares=Decimal("0"), unit_price=Decimal("1"), fees=Decimal("0")
            )


class TestRecordIncome:
    """The four types must land on the right repository method."""

    @pytest.mark.parametrize(
        ("income_type", "tax", "expected_tax"),
        [
            (TransactionType.DIVIDEND, None, Decimal("0")),
            (TransactionType.DIVIDEND, Decimal("1.5"), Decimal("1.5")),
            (TransactionType.JCP, Decimal("30"), Decimal("30")),
            (TransactionType.INTEREST, Decimal("12.375"), Decimal("12.375")),
            (TransactionType.RENDIMENTO, None, Decimal("0")),
        ],
    )
    def test_type_and_withheld_tax(
        self,
        seeded: None,
        income_type: TransactionType,
        tax: Decimal | None,
        expected_tax: Decimal,
    ) -> None:
        transaction = services.record_income(
            ticker="PETR4", income_type=income_type, when=WHEN, amount=Decimal("200"), tax_withheld=tax
        )
        assert transaction.transaction_type is income_type
        assert transaction.total_investment == Decimal("200")
        assert transaction.tax_withheld == expected_tax
        assert transaction.shares == Decimal("0")  # provento nao move quantidade

    def test_rendimento_ignores_a_withheld_tax_it_cannot_have(self, seeded: None) -> None:
        # A regra "RENDIMENTO nao aceita IR" vive no formulario; se um valor
        # escapar por outro caminho, o repositorio de rendimento nao o grava.
        transaction = services.record_income(
            ticker="MXRF11",
            income_type=TransactionType.RENDIMENTO,
            when=WHEN,
            amount=Decimal("80"),
            tax_withheld=Decimal("5"),
        )
        assert transaction.tax_withheld == Decimal("0")

    def test_a_trade_type_is_rejected(self, seeded: None) -> None:
        with pytest.raises(ValueError, match="tipo de provento invalido"):
            services.record_income(ticker="PETR4", income_type=TransactionType.BUY, when=WHEN, amount=Decimal("10"))


class TestLedger:
    def test_loads_everything_in_chronological_order(self, seeded: None) -> None:
        services.record_buy(
            ticker="PETR4",
            when=datetime(2026, 5, 1, 12, tzinfo=UTC),
            shares=Decimal("1"),
            unit_price=Decimal("30"),
            fees=Decimal("0"),
        )
        services.record_income(
            ticker="MXRF11",
            income_type=TransactionType.RENDIMENTO,
            when=datetime(2026, 4, 1, 12, tzinfo=UTC),
            amount=Decimal("8"),
        )
        assert [t.ticker for t in services.load_transactions()] == ["MXRF11", "PETR4"]

    def test_delete_removes_the_row(self, seeded: None) -> None:
        transaction = services.record_buy(
            ticker="PETR4", when=WHEN, shares=Decimal("1"), unit_price=Decimal("30"), fees=Decimal("0")
        )
        services.delete_transaction(transaction.id)
        assert services.load_transactions() == []

    def test_deleting_a_missing_row_is_a_domain_error(self, conn: psycopg.Connection[DictRow]) -> None:
        with pytest.raises(TransactionNotFoundError):
            services.delete_transaction(999999)


class TestOverviewDate:
    def test_reference_is_the_previous_business_day(self) -> None:
        assert services.overview_date(date(2026, 8, 12)) == date(2026, 8, 11)  # quarta -> terca
        assert services.overview_date(date(2026, 8, 10)) == date(2026, 8, 7)  # segunda -> sexta

    def test_defaults_to_today(self) -> None:
        assert services.overview_date() < date.today()


class TestDisplayFormat:
    def test_loads_the_configured_separator(self, conn: psycopg.Connection[DictRow]) -> None:
        set_value(conn, DECIMAL_SEPARATOR, ",")
        services.apply_display_format()
        assert fmt.separators().decimal == ","

    def test_default_keeps_the_canonical_format(self, conn: psycopg.Connection[DictRow]) -> None:
        services.apply_display_format()
        assert fmt.separators().is_canonical

    def test_a_broken_database_leaves_the_canonical_format(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*_args: object, **_kwargs: object) -> object:
            raise psycopg.OperationalError("connection refused")

        monkeypatch.setattr(services, "get_connection", boom)
        services.apply_display_format()
        assert fmt.separators().is_canonical


class TestRebalanceNotice:
    def test_quiet_when_no_evaluation_was_ever_recorded(self, conn: psycopg.Connection[DictRow]) -> None:
        assert services.rebalance_notice(today=date(2026, 8, 12)) is None

    def test_reports_an_overdue_cycle(self, conn: psycopg.Connection[DictRow]) -> None:
        set_value(conn, REBALANCE_PERIOD_MONTHS, 6)
        set_value(conn, LAST_REBALANCE_DATE, date(2026, 1, 10))
        notice = services.rebalance_notice(today=date(2026, 8, 12))
        assert notice is not None
        assert "6 meses vencido desde 2026-07-10" in notice

    def test_quiet_inside_the_cycle(self, conn: psycopg.Connection[DictRow]) -> None:
        set_value(conn, LAST_REBALANCE_DATE, date(2026, 8, 1))
        assert services.rebalance_notice(today=date(2026, 8, 12)) is None

    def test_a_broken_database_never_blocks_the_interface(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*_args: object, **_kwargs: object) -> object:
            raise psycopg.OperationalError("connection refused")

        monkeypatch.setattr(services, "get_connection", boom)
        assert services.rebalance_notice(today=date(2026, 8, 12)) is None


class TestLoadSnapshot:
    def test_without_prices_it_never_touches_a_provider(self, seeded: None) -> None:
        services.record_buy(
            ticker="PETR4", when=WHEN, shares=Decimal("10"), unit_price=Decimal("30"), fees=Decimal("0")
        )
        snapshot = services.load_snapshot(with_prices=False, today=date(2026, 3, 20))
        assert [p.ticker for p in snapshot.summary.positions] == ["PETR4"]
        assert snapshot.summary.positions[0].price is None
        assert snapshot.month_profit is None
        assert snapshot.summary.total_invested == Decimal("300")
