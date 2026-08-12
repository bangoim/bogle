"""Fakes and helpers for the TUI tests (issue #73).

The interface is tested through Textual's ``Pilot``: the app runs headless, keys
are pressed and widgets are inspected. Nothing here touches the database or the
network — :mod:`bogle.tui.services` is patched with the builders below, which is
exactly the seam that module exists for.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from bogle.domain.assets import AssetType
from bogle.domain.transactions import Transaction, TransactionType
from bogle.position import PortfolioSummary, Position
from bogle.reports.overview import PortfolioOverview
from bogle.reports.snapshot import PortfolioSnapshot
from bogle.tui import services
from bogle.tui.app import BogleApp

TODAY = date(2026, 8, 12)
AS_OF = date(2026, 8, 11)
QUOTED_AT = datetime(2026, 8, 11, 18, 28, tzinfo=UTC)
TICKERS = ["AUVP11", "MXRF11", "PETR4"]


def stub_services(monkeypatch: Any) -> None:
    """Patch every service with a working default, so any screen can mount.

    Tests override the one they are about; without this, the Home screen (always
    mounted first) would call the real database on its way to the tested screen.
    """
    monkeypatch.setattr(services, "load_overview", lambda **_: make_overview())
    monkeypatch.setattr(services, "load_snapshot", lambda **_: make_snapshot())
    monkeypatch.setattr(services, "rebalance_notice", lambda **_: None)
    # Preferencias: os testes nao escrevem em user_settings sem pedir.
    monkeypatch.setattr(services, "load_preferences", services.Preferences)
    monkeypatch.setattr(services, "save_hide_amounts", lambda hidden: None)
    monkeypatch.setattr(services, "save_theme", lambda theme: None)
    monkeypatch.setattr(services, "list_tickers", lambda: list(TICKERS))
    monkeypatch.setattr(services, "load_transactions", list)
    monkeypatch.setattr(services, "delete_transaction", lambda transaction_id: None)
    monkeypatch.setattr(services, "record_buy", lambda **kwargs: make_transaction(TransactionType.BUY, **kwargs))
    monkeypatch.setattr(services, "record_sell", lambda **kwargs: make_transaction(TransactionType.SELL, **kwargs))
    monkeypatch.setattr(
        services,
        "record_income",
        lambda **kwargs: make_transaction(kwargs.pop("income_type"), **kwargs),
    )


async def settle(pilot: Any) -> None:
    """Let the worker threads finish and the UI process their updates.

    Loops because a worker's callback can start another one (removing a
    transaction reloads the ledger), and ``wait_for_complete`` only awaits the
    workers that existed when it was called.
    """
    for _ in range(5):
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()
        if not list(pilot.app.workers):
            return


def make_overview(**overrides: Any) -> PortfolioOverview:
    fields: dict[str, Any] = {
        "as_of": AS_OF,
        "inception": date(2024, 3, 1),
        "invested": Decimal("7350"),
        "patrimony": Decimal("7866.20"),
        "twr_12m": Decimal("0.1275"),
        "twr_total": Decimal("0.1840"),
        "twr_12m_start": date(2025, 8, 11),
        "excluded": [],
    }
    fields.update(overrides)
    return PortfolioOverview(**fields)


def empty_overview() -> PortfolioOverview:
    return make_overview(
        inception=None, invested=Decimal("0"), patrimony=None, twr_12m=None, twr_total=None, twr_12m_start=None
    )


def make_position(ticker: str, asset_type: AssetType, **overrides: Any) -> Position:
    fields: dict[str, Any] = {
        "ticker": ticker,
        "asset_type": asset_type,
        "quantity": Decimal("100"),
        "total_invested": Decimal("3750"),
        "target_weight": Decimal("0.5"),
        "dividends": Decimal("145"),
        "price": Decimal("41.15"),
        "market_value": Decimal("4115"),
        "current_weight": Decimal("0.5230"),
        "drift": Decimal("0.0230"),
        "pnl": Decimal("365"),
        "pnl_percent": Decimal("0.0974"),
        "twr": Decimal("0.1275"),
        "price_source": "brapi",
        "as_of": QUOTED_AT,
    }
    fields.update(overrides)
    return Position(**fields)


def make_unpriced_position(ticker: str, asset_type: AssetType, **overrides: Any) -> Position:
    """A position whose market fields are all unavailable (an unpriced ticker)."""
    unpriced: dict[str, Any] = {
        "price": None,
        "market_value": None,
        "current_weight": None,
        "drift": None,
        "pnl": None,
        "pnl_percent": None,
        "twr": None,
        "price_source": None,
        "as_of": None,
    }
    unpriced.update(overrides)
    return make_position(ticker, asset_type, **unpriced)


def snapshot_of(*positions: Position, **overrides: Any) -> PortfolioSnapshot:
    """A snapshot over explicit positions, with the totals derived from them."""
    zero = Decimal("0")
    summary = PortfolioSummary(
        positions=list(positions),
        total_value=sum((p.market_value for p in positions if p.market_value is not None), zero),
        total_invested=sum((p.total_invested for p in positions), zero),
        total_pnl=sum((p.pnl for p in positions if p.pnl is not None), zero),
        total_dividends=sum((p.dividends for p in positions), zero),
    )
    fields: dict[str, Any] = {
        "summary": summary,
        "month_profit": Decimal("82.40"),
        "income_12m": Decimal("145"),
        "excluded": [],
    }
    fields.update(overrides)
    return PortfolioSnapshot(**fields)


def make_snapshot(**overrides: Any) -> PortfolioSnapshot:
    petr4 = make_position("PETR4", AssetType.STOCK)
    cdb = make_position(
        "CDB-XP-2027",
        AssetType.CDB,
        quantity=Decimal("1"),
        total_invested=Decimal("800"),
        target_weight=Decimal("0.1"),
        dividends=Decimal("0"),
        price=Decimal("811.20"),
        market_value=Decimal("811.20"),
        current_weight=Decimal("0.1031"),
        drift=Decimal("0.0031"),
        pnl=Decimal("11.20"),
        pnl_percent=Decimal("0.014"),
        twr=Decimal("0.014"),
        price_source="calculado",
        as_of=None,
    )
    return snapshot_of(petr4, cdb, **overrides)


def empty_snapshot() -> PortfolioSnapshot:
    return snapshot_of(month_profit=None, income_12m=Decimal("0"))


def make_transaction(kind: TransactionType, **entry: Any) -> Transaction:
    """A persisted transaction, as a repository would return it.

    Takes the same keyword arguments as the ``record_*`` services, so a fake can
    echo back what the form asked to write.
    """
    zero = Decimal("0")
    shares = entry.get("shares", zero)
    unit_price = entry.get("unit_price", zero)
    fees = entry.get("fees", zero)
    amount = entry.get("amount")
    gross = amount if amount is not None else shares * unit_price
    return Transaction(
        id=entry.get("id", 7),
        ticker=entry.get("ticker", "PETR4"),
        transaction_type=kind,
        date=entry.get("when", datetime(2026, 8, 12, tzinfo=UTC)),
        shares=shares,
        unit_price=unit_price,
        total_investment=gross,
        fees=fees,
        total_cost=gross + fees if kind is TransactionType.BUY else fees,
        tax_withheld=entry.get("tax_withheld") or zero,
    )


def make_ledger() -> list[Transaction]:
    """A small ledger: two tickers, a trade of each kind and an income event."""
    return [
        make_transaction(
            TransactionType.BUY,
            id=1,
            ticker="AUVP11",
            when=datetime(2026, 3, 10, tzinfo=UTC),
            shares=Decimal("3"),
            unit_price=Decimal("126.25"),
            fees=Decimal("0.13"),
        ),
        make_transaction(
            TransactionType.DIVIDEND,
            id=2,
            ticker="PETR4",
            when=datetime(2026, 5, 15, tzinfo=UTC),
            amount=Decimal("45.50"),
        ),
        make_transaction(
            TransactionType.SELL,
            id=3,
            ticker="AUVP11",
            when=datetime(2026, 6, 20, tzinfo=UTC),
            shares=Decimal("1"),
            unit_price=Decimal("130"),
            fees=Decimal("0.13"),
            tax_withheld=Decimal("0.01"),
        ),
    ]


class ToastSpy:
    """Records the toasts a screen raises, via the public ``notify`` API."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def install(self, monkeypatch: Any, *screen_classes: type) -> None:
        def notify(_screen: Any, message: str, **kwargs: Any) -> None:
            self.calls.append((message, kwargs.get("severity", "information")))

        for screen_class in screen_classes:
            monkeypatch.setattr(screen_class, "notify", notify)

    @property
    def messages(self) -> list[str]:
        return [message for message, _ in self.calls]

    def severity_of(self, fragment: str) -> str | None:
        for message, severity in self.calls:
            if fragment in message:
                return severity
        return None


def make_app() -> BogleApp:
    return BogleApp()
