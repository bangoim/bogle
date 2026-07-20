"""Market-data clients and shared types.

Holds one client per external source (brapi, BCB SGS, Tesouro, yfinance) plus the
provider-agnostic types in :mod:`bogle.data.models`. The cache layer and the
``get_price`` dispatcher that routes by asset type land here in a later milestone.
"""

from __future__ import annotations
