"""Market-data clients and shared types.

Holds one client per external source (brapi, BCB SGS, Tesouro, yfinance), the
provider-agnostic types in :mod:`bogle.data.models`, and the
:class:`~bogle.data.dispatcher.PriceDispatcher` that prices any asset by routing
to the right client / present-value engine.
"""

from __future__ import annotations

from bogle.data.dispatcher import PriceDispatcher

__all__ = ["PriceDispatcher", "default_dispatcher"]


def default_dispatcher() -> PriceDispatcher:
    """A dispatcher wired to the real clients (reads env/caches lazily).

    Imports the clients lazily so importing :mod:`bogle.data` — or running a CLI
    command that never prices anything — does not pull in yfinance/pandas.
    """
    from bogle.data.bcb import BcbClient
    from bogle.data.brapi import BrapiClient
    from bogle.data.tesouro import TesouroClient
    from bogle.data.yfinance_client import YFinanceClient

    return PriceDispatcher(
        brapi=BrapiClient(),
        yfinance=YFinanceClient(),
        tesouro=TesouroClient(),
        bcb=BcbClient(),
    )
