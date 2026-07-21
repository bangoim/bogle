"""Portfolio analytics: pure functions over domain + market-data types.

No I/O and no persistence here — callers pass in the transactions and the price
history (or an injected valuator), and get numbers back.
"""

from __future__ import annotations
