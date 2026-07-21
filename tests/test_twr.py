"""Tests for the TWR engine. Pure math — no HTTP, no DB.

Each scenario builds transactions + a close-price history and states the expected
sub-period linking in comments so the arithmetic can be checked by hand.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from bogle.analytics.twr import compute_twr, compute_twr_per_ticker, price_history_valuator
from bogle.data.models import HistPoint
from bogle.domain.transactions import Transaction, TransactionType

_ZERO = Decimal("0")


def _dt(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def _txn(ticker: str, day: date, ttype: TransactionType, shares: str = "0", amount: str = "0") -> Transaction:
    return Transaction(
        id=0,
        ticker=ticker,
        transaction_type=ttype,
        date=_dt(day),
        shares=Decimal(shares),
        unit_price=_ZERO,
        total_investment=Decimal(amount),
        fees=_ZERO,
        total_cost=_ZERO,
        tax_withheld=_ZERO,
    )


def buy(ticker: str, day: date, shares: str) -> Transaction:
    return _txn(ticker, day, TransactionType.BUY, shares=shares)


def sell(ticker: str, day: date, shares: str) -> Transaction:
    return _txn(ticker, day, TransactionType.SELL, shares=shares)


def dividend(ticker: str, day: date, amount: str) -> Transaction:
    return _txn(ticker, day, TransactionType.DIVIDEND, amount=amount)


def hp(day: date, close: str) -> HistPoint:
    c = Decimal(close)
    return HistPoint(date=_dt(day), open=c, high=c, low=c, close=c, volume=0)


def approx(value: Decimal, expected: str, tol: str = "1e-12") -> bool:
    return abs(value - Decimal(expected)) < Decimal(tol)


D = date  # brevity in scenarios


class TestSinglePeriod:
    def test_no_flows_is_plain_price_return(self) -> None:
        # Hold 10 from before the window; 100 -> 110 => +10%.
        txns = [buy("AAA", D(2025, 12, 1), "10")]
        history = {"AAA": [hp(D(2026, 1, 1), "100"), hp(D(2026, 1, 31), "110")]}
        assert compute_twr(txns, history, D(2026, 1, 1), D(2026, 1, 31)) == Decimal("0.1")

    def test_empty_transactions_is_zero(self) -> None:
        assert compute_twr([], {}, D(2026, 1, 1), D(2026, 1, 31)) == _ZERO


class TestCashFlowsExcludedFromReturn:
    def test_asset_bought_mid_period(self) -> None:
        # Nothing at start; buy 10 @ 50 on 1/10; 55 at end => only 50->55 counts = +10%.
        txns = [buy("AAA", D(2026, 1, 10), "10")]
        history = {"AAA": [hp(D(2026, 1, 10), "50"), hp(D(2026, 1, 31), "55")]}
        assert compute_twr(txns, history, D(2026, 1, 1), D(2026, 1, 31)) == Decimal("0.1")

    def test_partial_sell_does_not_affect_return(self) -> None:
        # Hold 10 @ 50 (base 500). 1/15: price 60 (r=600/500-1=20%), sell 4. End 60 (flat).
        txns = [buy("AAA", D(2025, 12, 1), "10"), sell("AAA", D(2026, 1, 15), "4")]
        history = {
            "AAA": [hp(D(2026, 1, 1), "50"), hp(D(2026, 1, 15), "60"), hp(D(2026, 1, 31), "60")],
        }
        assert compute_twr(txns, history, D(2026, 1, 1), D(2026, 1, 31)) == Decimal("0.2")

    def test_irregular_contributions_chain_geometrically(self) -> None:
        # r1 = 120/100-1 = .20 ; buy 5 @12 -> base 180
        # r2 = 225/180-1 = .25 ; buy 5 @15 -> base 300
        # r3 = 320/300-1 = 1/15 ; TWR = 1.2*1.25*(16/15)-1 = 0.60
        txns = [
            buy("AAA", D(2025, 12, 1), "10"),
            buy("AAA", D(2026, 1, 10), "5"),
            buy("AAA", D(2026, 1, 20), "5"),
        ]
        history = {
            "AAA": [
                hp(D(2026, 1, 1), "10"),
                hp(D(2026, 1, 10), "12"),
                hp(D(2026, 1, 20), "15"),
                hp(D(2026, 1, 31), "16"),
            ]
        }
        twr = compute_twr(txns, history, D(2026, 1, 1), D(2026, 1, 31))
        assert approx(twr, "0.6")


class TestDividends:
    def test_dividend_is_credited_as_return(self) -> None:
        # 1 share. 1/15 ex-div: price 100->95, pays 5 => r1 = (95+5)/100-1 = 0.
        # End 1/31 back to 100 => r2 = 100/95-1. TWR = 100/95 - 1.
        txns = [buy("AAA", D(2025, 12, 1), "1"), dividend("AAA", D(2026, 1, 15), "5")]
        history = {
            "AAA": [hp(D(2026, 1, 1), "100"), hp(D(2026, 1, 15), "95"), hp(D(2026, 1, 31), "100")],
        }
        twr = compute_twr(txns, history, D(2026, 1, 1), D(2026, 1, 31))
        assert approx(twr, str(Decimal("100") / Decimal("95") - 1))

    def test_flat_price_with_dividend_is_positive(self) -> None:
        # Price flat at 100, a 5 dividend mid-period => total return is the dividend.
        txns = [buy("AAA", D(2025, 12, 1), "1"), dividend("AAA", D(2026, 1, 15), "5")]
        history = {
            "AAA": [hp(D(2026, 1, 1), "100"), hp(D(2026, 1, 15), "100"), hp(D(2026, 1, 31), "100")],
        }
        # r1 = (100+5)/100-1 = .05 ; r2 = 100/100-1 = 0 ; TWR = 5%.
        assert compute_twr(txns, history, D(2026, 1, 1), D(2026, 1, 31)) == Decimal("0.05")


class TestWeekendRule:
    def test_uses_nearest_earlier_price(self) -> None:
        # Window starts Sat 2026-01-03; only Fri 2026-01-02 has a price.
        txns = [buy("AAA", D(2025, 12, 1), "10")]
        history = {"AAA": [hp(D(2026, 1, 2), "100"), hp(D(2026, 1, 31), "110")]}
        assert compute_twr(txns, history, D(2026, 1, 3), D(2026, 1, 31)) == Decimal("0.1")


class TestMultipleTickers:
    def test_aggregate_portfolio_value(self) -> None:
        # AAA 10@10->11 (+10 mkt), BBB 5@20->22 (+10 mkt): 200 -> 220 = +10%.
        txns = [buy("AAA", D(2025, 12, 1), "10"), buy("BBB", D(2025, 12, 1), "5")]
        history = {
            "AAA": [hp(D(2026, 1, 1), "10"), hp(D(2026, 1, 31), "11")],
            "BBB": [hp(D(2026, 1, 1), "20"), hp(D(2026, 1, 31), "22")],
        }
        assert compute_twr(txns, history, D(2026, 1, 1), D(2026, 1, 31)) == Decimal("0.1")

    def test_per_ticker_returns_each_independently(self) -> None:
        txns = [buy("AAA", D(2025, 12, 1), "10"), buy("BBB", D(2025, 12, 1), "5")]
        history = {
            "AAA": [hp(D(2026, 1, 1), "10"), hp(D(2026, 1, 31), "11")],  # +10%
            "BBB": [hp(D(2026, 1, 1), "20"), hp(D(2026, 1, 31), "25")],  # +25%
        }
        per = compute_twr_per_ticker(txns, history, D(2026, 1, 1), D(2026, 1, 31))
        assert per == {"AAA": Decimal("0.1"), "BBB": Decimal("0.25")}


class TestInjectedValuator:
    def test_custom_valuator_drives_valuation(self) -> None:
        # Injection point (decision D): no price_history, valuator maps date->unit price.
        prices = {D(2026, 1, 1): Decimal("10"), D(2026, 1, 31): Decimal("11")}
        txns = [buy("AAA", D(2025, 12, 1), "10")]

        def valuator(holdings: Mapping[str, Decimal], on: date) -> Decimal:
            units = sum(holdings.values(), _ZERO)
            return units * prices[on]

        twr = compute_twr(txns, None, D(2026, 1, 1), D(2026, 1, 31), valuator=valuator)
        assert twr == Decimal("0.1")


class TestGuards:
    def test_end_before_start_raises(self) -> None:
        with pytest.raises(ValueError, match="end"):
            compute_twr([], {}, D(2026, 2, 1), D(2026, 1, 1))

    def test_missing_valuator_and_history_raises(self) -> None:
        with pytest.raises(ValueError, match="valuator"):
            compute_twr([buy("AAA", D(2025, 1, 1), "1")], None, D(2026, 1, 1), D(2026, 1, 31))

    def test_missing_price_for_held_ticker_raises(self) -> None:
        txns = [buy("AAA", D(2025, 12, 1), "10")]
        with pytest.raises(ValueError, match="Sem preco"):
            compute_twr(txns, {"AAA": []}, D(2026, 1, 1), D(2026, 1, 31))


class TestPriceValuatorHelper:
    def test_skips_zero_share_holdings(self) -> None:
        # A ticker held at 0 shares needs no price lookup.
        valuate = price_history_valuator({})
        assert valuate({"AAA": _ZERO}, D(2026, 1, 1)) == _ZERO
