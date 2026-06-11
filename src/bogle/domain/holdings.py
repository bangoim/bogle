from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from bogle.domain.assets import AssetType


@dataclass(frozen=True, slots=True)
class Holding:
    """A row of the ``holdings`` view: one asset's consolidated active position.

    ``total_shares`` nets BUY minus SELL quantities; the view only
    returns positions with ``total_shares > 0``. For fixed income
    without daily liquidity the recording convention is shares = 1 per
    application (present value arrives with issue 6.1).

    ``total_invested`` is the net invested capital: BUY costs (fees
    included) minus gross SELL proceeds. It goes negative on an active
    position once sales have returned more cash than was invested.
    """

    ticker: str
    target_weight: Decimal
    asset_type: AssetType
    total_shares: Decimal
    total_invested: Decimal
