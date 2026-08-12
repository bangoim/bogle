"""Tests for the TUI's data-access layer (issues #73/#74).

Every screen test patches this module, so the layer itself needs its own
coverage: it runs against the real ``bogle_test`` database and pins what each
function actually writes — in particular ``record_income``, the only place that
routes an income type to its repository method.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from psycopg.rows import DictRow

from bogle import format as fmt
from bogle.domain.assets import AssetType, Indexer
from bogle.domain.errors import (
    AssetHasTransactionsError,
    AssetNotFoundError,
    TransactionNotFoundError,
    UnknownSettingError,
    ValidationError,
    WeightSumExceededError,
)
from bogle.domain.transactions import TransactionType
from bogle.position import PortfolioSummary, Position
from bogle.repositories.assets import AssetRepository
from bogle.settings import (
    DECIMAL_SEPARATOR,
    DEFAULT_COMPARE_INDICES,
    DEFAULT_THEME,
    HIDE_VALUES,
    LAST_REBALANCE_DATE,
    REBALANCE_PERIOD_MONTHS,
    THEME,
    get_setting,
    set_value,
)
from bogle.tui import services
from tests.test_valuation import FakeYfinance, bar, make_dispatcher

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


class TestPreferences:
    def test_reads_what_was_configured(self, conn: psycopg.Connection[DictRow]) -> None:
        set_value(conn, DECIMAL_SEPARATOR, ",")
        set_value(conn, HIDE_VALUES, True)
        set_value(conn, THEME, "ansi-dark")
        preferences = services.load_preferences()
        assert preferences.decimal_separator == ","
        assert preferences.hide_amounts is True
        assert preferences.theme == "ansi-dark"

    def test_defaults_when_nothing_was_configured(self, conn: psycopg.Connection[DictRow]) -> None:
        preferences = services.load_preferences()
        assert preferences.decimal_separator == fmt.CANONICAL_DECIMAL
        assert preferences.hide_amounts is False
        assert preferences.theme == DEFAULT_THEME

    def test_a_broken_database_falls_back_to_the_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*_args: object, **_kwargs: object) -> object:
            raise psycopg.OperationalError("connection refused")

        monkeypatch.setattr(services, "get_connection", boom)
        assert services.load_preferences() == services.Preferences()

    def test_saving_the_privacy_mode_round_trips(self, conn: psycopg.Connection[DictRow]) -> None:
        # A queixa que isso resolve: ocultar, fechar, reabrir e estar visivel.
        services.save_hide_amounts(True)
        assert get_setting(conn, HIDE_VALUES) is True
        assert services.load_preferences().hide_amounts is True
        services.save_hide_amounts(False)
        assert services.load_preferences().hide_amounts is False

    def test_saving_the_theme_round_trips(self, conn: psycopg.Connection[DictRow]) -> None:
        services.save_theme("gruvbox")
        assert get_setting(conn, THEME) == "gruvbox"
        assert services.load_preferences().theme == "gruvbox"


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


class TestAssets:
    def test_lists_what_was_registered(self, seeded: None) -> None:
        assert [asset.ticker for asset in services.list_assets()] == ["MXRF11", "PETR4"]

    def test_registers_variable_income(self, conn: psycopg.Connection[DictRow]) -> None:
        asset = services.add_asset(ticker="vale3", target_weight=Decimal("0.2"), asset_type=AssetType.STOCK)
        assert asset.ticker == "VALE3"
        assert [a.ticker for a in services.list_assets()] == ["VALE3"]

    def test_registers_private_fixed_income_with_its_metadata(self, conn: psycopg.Connection[DictRow]) -> None:
        asset = services.add_asset(
            ticker="CDB-NU-2028",
            target_weight=Decimal("0.1"),
            asset_type=AssetType.CDB,
            issuer="Nubank",
            indexer=Indexer.CDI,
            rate=Decimal("1.05"),
            is_prefixed=False,
            daily_liquidity=False,
            purchase_date=datetime(2026, 5, 2, tzinfo=UTC),
            maturity_date=datetime(2028, 5, 2, tzinfo=UTC),
        )
        assert asset.issuer == "Nubank"
        stored = services.list_assets()[0]
        assert stored.indexer is Indexer.CDI
        assert stored.rate == Decimal("1.05")
        assert stored.daily_liquidity is False

    def test_a_field_the_type_does_not_accept_is_refused_before_the_insert(
        self, conn: psycopg.Connection[DictRow]
    ) -> None:
        # Mesma validacao de dominio do `bogle add`: nada e escrito.
        with pytest.raises(ValidationError, match="nao se aplica"):
            services.add_asset(ticker="PETR4", target_weight=Decimal("0.2"), asset_type=AssetType.STOCK, issuer="XP")
        assert services.list_assets() == []

    def test_missing_fixed_income_fields_are_refused(self, conn: psycopg.Connection[DictRow]) -> None:
        with pytest.raises(ValidationError, match="--rate e obrigatorio"):
            services.add_asset(ticker="TESOURO-IPCA-2035", target_weight=Decimal("0.2"), asset_type=AssetType.TESOURO)

    def test_the_weight_sum_guard_still_applies(self, seeded: None) -> None:
        with pytest.raises(WeightSumExceededError):
            services.add_asset(ticker="VALE3", target_weight=Decimal("0.6"), asset_type=AssetType.STOCK)

    def test_updates_the_weight(self, seeded: None) -> None:
        asset = services.update_asset(ticker="petr4", target_weight=Decimal("0.5"))
        assert asset.target_weight == Decimal("0.5")

    def test_updates_the_type_between_variable_income_types(self, seeded: None) -> None:
        asset = services.update_asset(ticker="PETR4", asset_type=AssetType.ETF)
        assert asset.asset_type is AssetType.ETF

    def test_changing_a_fixed_income_type_is_refused(self, conn: psycopg.Connection[DictRow]) -> None:
        services.add_asset(
            ticker="TESOURO-IPCA-2035",
            target_weight=Decimal("0.2"),
            asset_type=AssetType.TESOURO,
            indexer=Indexer.IPCA_PLUS,
            rate=Decimal("0.065"),
            purchase_date=datetime(2026, 1, 10, tzinfo=UTC),
            maturity_date=datetime(2035, 5, 15, tzinfo=UTC),
        )
        with pytest.raises(ValidationError, match="renda fixa"):
            services.update_asset(ticker="TESOURO-IPCA-2035", asset_type=AssetType.STOCK)

    def test_updating_an_unknown_ticker_is_a_domain_error(self, conn: psycopg.Connection[DictRow]) -> None:
        with pytest.raises(AssetNotFoundError):
            services.update_asset(ticker="NOPE3", target_weight=Decimal("0.1"))

    def test_removes_an_asset_without_transactions(self, seeded: None) -> None:
        services.remove_asset("MXRF11")
        assert [asset.ticker for asset in services.list_assets()] == ["PETR4"]

    def test_an_asset_with_transactions_cannot_be_removed(self, seeded: None) -> None:
        services.record_buy(ticker="PETR4", when=WHEN, shares=Decimal("1"), unit_price=Decimal("30"), fees=Decimal("0"))
        with pytest.raises(AssetHasTransactionsError):
            services.remove_asset("PETR4")


class TestIncomeReport:
    def test_groups_the_same_window_both_ways(self, seeded: None) -> None:
        services.record_income(ticker="PETR4", income_type=TransactionType.DIVIDEND, when=WHEN, amount=Decimal("45.50"))
        services.record_income(ticker="MXRF11", income_type=TransactionType.RENDIMENTO, when=WHEN, amount=Decimal("80"))
        report = services.load_income(period="all", today=date(2026, 3, 20))
        assert report.start is None
        assert report.end == date(2026, 3, 20)
        assert [row.month for row in report.by_month] == [date(2026, 3, 1)]
        assert report.by_month[0].total == Decimal("125.50")
        assert [(row.ticker, row.total) for row in report.by_ticker] == [
            ("MXRF11", Decimal("80")),
            ("PETR4", Decimal("45.50")),
        ]

    def test_the_twelve_month_window_is_in_calendar_months(self, seeded: None) -> None:
        report = services.load_income(period="12m", today=date(2026, 8, 12))
        assert report.start == date(2025, 9, 1)

    def test_jcp_is_reported_net_of_the_withheld_tax(self, seeded: None) -> None:
        services.record_income(
            ticker="PETR4",
            income_type=TransactionType.JCP,
            when=WHEN,
            amount=Decimal("200"),
            tax_withheld=Decimal("30"),
        )
        report = services.load_income(period="all", today=date(2026, 3, 20))
        assert report.by_month[0].jcp == Decimal("170")


class TestCycle:
    def test_never_evaluated(self, conn: psycopg.Connection[DictRow]) -> None:
        cycle = services.load_cycle(today=date(2026, 8, 12))
        assert cycle.period_months == 12  # default
        assert cycle.last_evaluation is None
        assert cycle.next_evaluation is None
        assert cycle.days is None

    def test_counts_the_days_to_the_next_evaluation(self, conn: psycopg.Connection[DictRow]) -> None:
        set_value(conn, LAST_REBALANCE_DATE, date(2026, 2, 10))
        conn.commit()
        cycle = services.load_cycle(today=date(2026, 8, 12))
        assert cycle.next_evaluation == date(2027, 2, 10)
        assert cycle.days == (date(2027, 2, 10) - date(2026, 8, 12)).days

    def test_an_overdue_cycle_counts_negative(self, conn: psycopg.Connection[DictRow]) -> None:
        set_value(conn, REBALANCE_PERIOD_MONTHS, 6)
        set_value(conn, LAST_REBALANCE_DATE, date(2025, 8, 10))
        conn.commit()
        cycle = services.load_cycle(today=date(2026, 8, 12))
        assert cycle.period_months == 6
        assert cycle.next_evaluation == date(2026, 2, 10)
        assert cycle.days is not None and cycle.days < 0


class TestSettingsAccess:
    def test_lists_every_key_with_its_provenance(self, conn: psycopg.Connection[DictRow]) -> None:
        entries = {entry.key: entry for entry in services.load_settings()}
        assert entries[REBALANCE_PERIOD_MONTHS].is_default is True
        assert entries[REBALANCE_PERIOD_MONTHS].value == 12

    def test_saving_parses_the_raw_value_and_reports_the_typed_one(self, conn: psycopg.Connection[DictRow]) -> None:
        assert services.save_setting(REBALANCE_PERIOD_MONTHS, "6") == 6
        entries = {entry.key: entry for entry in services.load_settings()}
        assert entries[REBALANCE_PERIOD_MONTHS].value == 6
        assert entries[REBALANCE_PERIOD_MONTHS].is_default is False

    def test_an_invalid_value_is_refused_with_the_command_s_message(self, conn: psycopg.Connection[DictRow]) -> None:
        with pytest.raises(ValidationError, match="deve ser 6 ou 12 meses"):
            services.save_setting(REBALANCE_PERIOD_MONTHS, "7")

    def test_an_unknown_key_is_refused(self, conn: psycopg.Connection[DictRow]) -> None:
        with pytest.raises(UnknownSettingError):
            services.save_setting("nao_existe", "1")

    def test_resetting_returns_the_default_that_came_back(self, conn: psycopg.Connection[DictRow]) -> None:
        services.save_setting(DECIMAL_SEPARATOR, ",")
        assert services.reset_setting(DECIMAL_SEPARATOR) == "."
        entries = {entry.key: entry for entry in services.load_settings()}
        assert entries[DECIMAL_SEPARATOR].is_default is True

    def test_default_indices_come_from_the_setting(self, conn: psycopg.Connection[DictRow]) -> None:
        assert services.default_indices() == ("IBOV", "CDI")
        services.save_setting(DEFAULT_COMPARE_INDICES, "cdi,ipca")
        assert services.default_indices() == ("CDI", "IPCA")


class TestSuggestion:
    @pytest.fixture
    def priced(self, seeded: None, monkeypatch: pytest.MonkeyPatch) -> None:
        """A priced portfolio injected the way the CLI's own suggest test injects it.

        A suggestion needs every position priced (``MissingPriceError`` otherwise),
        which is a live quote; what this test is about is what the service adds
        around the engine — the split it returns and the evaluation it records.
        """
        summary = PortfolioSummary(
            positions=[
                Position(
                    ticker="PETR4",
                    asset_type=AssetType.STOCK,
                    quantity=Decimal("10"),
                    total_invested=Decimal("300"),
                    target_weight=Decimal("0.4"),
                    dividends=Decimal("0"),
                    price=Decimal("32"),
                    market_value=Decimal("320"),
                    current_weight=Decimal("1"),
                    drift=Decimal("0.6"),
                )
            ],
            total_value=Decimal("320"),
            total_invested=Decimal("300"),
            total_pnl=Decimal("20"),
            total_dividends=Decimal("0"),
        )
        monkeypatch.setattr(services, "default_dispatcher", lambda: None)
        monkeypatch.setattr(services, "get_portfolio_summary", lambda conn, dispatcher: summary)

    def test_splits_the_amount_and_records_the_evaluation(
        self, priced: None, conn: psycopg.Connection[DictRow]
    ) -> None:
        # A gravacao de `last_rebalance_date` e o efeito colateral que faz o aviso
        # de ciclo vencido parar de cobrar — a tela de Aporte herda ele do comando.
        suggestion = services.load_suggestion(Decimal("320"), today=date(2026, 3, 20))
        assert [item.ticker for item in suggestion.items] == ["PETR4"]
        assert suggestion.total_allocated + suggestion.leftover == Decimal("320")
        assert get_setting(conn, LAST_REBALANCE_DATE) == date(2026, 3, 20)

    def test_an_invalid_amount_records_nothing(self, priced: None, conn: psycopg.Connection[DictRow]) -> None:
        with pytest.raises(ValidationError):
            services.load_suggestion(Decimal("0"), today=date(2026, 3, 20))
        assert get_setting(conn, LAST_REBALANCE_DATE) is None


class TestReportLoaders:
    """The report wrappers: thin, but a wrong keyword here would only show up at
    runtime — every screen test patches this module out."""

    @pytest.fixture
    def priced(self, seeded: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        services.record_buy(
            ticker="PETR4", when=WHEN, shares=Decimal("10"), unit_price=Decimal("30"), fees=Decimal("0")
        )
        yf = FakeYfinance(
            {
                "PETR4.SA": [bar("2026-03-10", "30"), bar("2026-03-20", "32")],
                "^BVSP": [bar("2026-03-10", "100000"), bar("2026-03-20", "104000")],
            }
        )
        dispatcher = make_dispatcher(tmp_path, yfinance=yf)
        monkeypatch.setattr(services, "default_dispatcher", lambda: dispatcher)

    def test_returns_covers_the_three_windows(self, priced: None) -> None:
        report = services.load_returns(today=date(2026, 3, 20))
        assert [row.period for row in report.rows] == ["total", "12m", "1m"]

    def test_returns_accepts_indices(self, priced: None) -> None:
        report = services.load_returns(indices=("IBOV",), today=date(2026, 3, 20))
        assert "IBOV" in report.rows[0].index_returns

    def test_compare_is_base_one_hundred_at_the_start(self, priced: None) -> None:
        report = services.load_compare(period="12m", indices=("IBOV",), today=date(2026, 3, 20))
        assert [series.name for series in report.series] == ["Carteira", "IBOV"]
        assert all(series.levels[0] == Decimal("100") for series in report.series)

    def test_history_samples_the_window(self, priced: None) -> None:
        report = services.load_history(period="12m", today=date(2026, 3, 20))
        assert report.points
        assert report.points[-1].date == date(2026, 3, 20)

    def test_profit_windows_only_the_income(self, priced: None) -> None:
        services.record_income(ticker="PETR4", income_type=TransactionType.DIVIDEND, when=WHEN, amount=Decimal("10"))
        whole = services.load_profit(period="all", today=date(2026, 3, 20))
        windowed = services.load_profit(period="12m", today=date(2026, 3, 20))
        assert whole.income_start is None
        assert windowed.income_start == date(2025, 4, 1)
        assert whole.realized == windowed.realized  # ganho de capital nao muda


class TestChartExport:
    def test_writes_the_html_where_it_said_and_opens_it(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        opened: list[str] = []
        monkeypatch.setattr(services.charts, "open_in_browser", lambda path: opened.append(path))
        target = tmp_path / "chart.html"
        written = services.export_chart(
            title="Carteira",
            x_values=[date(2026, 1, 1), date(2026, 2, 1)],
            series=[("Carteira", [0.0, 2.5])],
            path=target,
            y_suffix="%",
        )
        assert written == target
        assert target.exists()
        assert "Carteira" in target.read_text(encoding="utf-8")
        assert opened == [str(target)]

    def test_the_path_is_stable_per_report_and_window(self) -> None:
        # Nome estavel: pedir duas vezes sobrescreve em vez de sujar o diretorio.
        assert services.chart_path("compare-12m") == services.chart_path("compare-12m")
        assert services.chart_path("compare-12m") != services.chart_path("history-12m")
