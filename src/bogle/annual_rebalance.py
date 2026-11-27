"""What the annual rebalance reads from and writes to the database (issue #81).

:func:`~bogle.rebalancing.suggest_annual_rebalance` is pure; this gathers what it
needs from one connection, so ``bogle rebalance`` and the Rebalancear screen ask
the same question the same way — the same reads as the withdrawal
(:mod:`bogle.withdrawal`), plus the drift threshold and the targets with no
position yet, which can receive a purchase:

- the positions and the targets, priced (:func:`~bogle.position.get_allocation_summary`);
- the tickers the glide path is taking out (:func:`~bogle.glide_path.exiting_tickers`);
- ``weight_drift_threshold``, the tolerance of the sale;
- the stock sales already registered this month, and the oldest lot of each
  fixed-income ticker, for the IR.

Seeing the plan writes nothing. Only :func:`record_annual_rebalance` (``bogle
rebalance --done``, or the key on the screen) records the year, which is what
silences the reminder: a plan looked at is not a plan executed.
"""

from __future__ import annotations

from datetime import date

import psycopg
from psycopg.rows import DictRow

from bogle.data.dispatcher import PriceDispatcher
from bogle.domain.errors import ValidationError
from bogle.glide_path import active_glide_path, exiting_tickers
from bogle.position import get_allocation_summary
from bogle.rebalancing import (
    AnnualRebalance,
    AnnualRebalanceState,
    annual_rebalance_refusal,
    annual_rebalance_state,
    suggest_annual_rebalance,
)
from bogle.repositories.assets import AssetRepository
from bogle.repositories.transactions import TransactionRepository
from bogle.settings import (
    BIRTH_YEAR,
    LAST_ANNUAL_REBALANCE_YEAR,
    RETIREMENT_AGE,
    WEIGHT_DRIFT_THRESHOLD,
    get_setting,
    set_value,
)
from bogle.withdrawal import fixed_income_held_since, month_stock_sales


def load_annual_state(conn: psycopg.Connection[DictRow], today: date) -> AnnualRebalanceState | None:
    """Where the annual rebalance of ``today.year`` stands; ``None`` without ``birth_year``."""
    return annual_rebalance_state(
        get_setting(conn, BIRTH_YEAR),
        get_setting(conn, RETIREMENT_AGE),
        get_setting(conn, LAST_ANNUAL_REBALANCE_YEAR),
        today.year,
    )


def _check_phase(conn: psycopg.Connection[DictRow], today: date) -> None:
    refusal = annual_rebalance_refusal(get_setting(conn, BIRTH_YEAR), get_setting(conn, RETIREMENT_AGE), today.year)
    if refusal is not None:
        raise ValidationError(refusal)


def plan_annual_rebalance(
    conn: psycopg.Connection[DictRow], dispatcher: PriceDispatcher, *, today: date
) -> AnnualRebalance:
    """The plan of the year: what to sell and what to buy with the net. Nothing is written.

    Refused (:class:`~bogle.domain.errors.ValidationError`) outside the phase, or
    without ``birth_year``.
    """
    _check_phase(conn, today)
    # get_allocation_summary, como no aporte: um alvo sem posicao (um ticker que a
    # trajetoria acabou de trazer) tambem recebe a compra.
    summary = get_allocation_summary(conn, dispatcher, on_date=today)
    exiting = exiting_tickers(active_glide_path(conn), (p.ticker for p in summary.positions if p.quantity > 0))
    transactions = TransactionRepository(conn).list()
    types = {asset.ticker: asset.asset_type for asset in AssetRepository(conn).list()}
    return suggest_annual_rebalance(
        summary,
        exiting,
        year=today.year,
        threshold=get_setting(conn, WEIGHT_DRIFT_THRESHOLD),
        month_stock_sales=month_stock_sales(transactions, types, today),
        held_since=fixed_income_held_since(summary.positions, transactions),
        today=today,
    )


def record_annual_rebalance(conn: psycopg.Connection[DictRow], *, today: date) -> int:
    """Record the rebalance of ``today.year`` as done, and return the year.

    Refused outside the phase, like the plan: there is nothing to have done.
    """
    _check_phase(conn, today)
    return set_value(conn, LAST_ANNUAL_REBALANCE_YEAR, today.year)
