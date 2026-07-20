"""Small on-disk JSON cache with per-entry expiry.

Backs the clients whose data changes slowly (BCB series, Tesouro prices): a
one-shot CLI process cannot benefit from an in-memory cache, so results are
persisted under ``$XDG_CACHE_HOME/bogle`` (``~/.cache/bogle`` by default). Each
namespace is a subdirectory; each key is one file holding
``{"expires_at": <unix>, "value": <json>}``.

Values must be JSON-serializable. The market-data clients cache the *raw* API
payloads (whose numbers already arrive as strings), so no ``Decimal`` ever needs
to round-trip through JSON here. The richer in-memory + two-TTL layer used by the
dispatcher builds on top of this primitive.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

_APP_DIR = "bogle"
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def default_cache_dir() -> Path:
    """Base cache directory, honoring ``XDG_CACHE_HOME`` then ``~/.cache``."""
    xdg = os.environ.get("XDG_CACHE_HOME")
    root = Path(xdg) if xdg else Path.home() / ".cache"
    return root / _APP_DIR


class DiskCache:
    """File-backed cache for one namespace.

    ``get`` returns ``None`` on a miss, on expiry, or on a corrupt/partial file
    (treated as a miss so a bad write self-heals on the next ``set``). The clock
    is injectable so tests can advance time without sleeping.
    """

    def __init__(
        self,
        namespace: str,
        *,
        base_dir: Path | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        base = base_dir if base_dir is not None else default_cache_dir()
        self._dir = base / namespace
        self._now = now

    def _path(self, key: str) -> Path:
        return self._dir / f"{_UNSAFE.sub('_', key)}.json"

    def get(self, key: str) -> Any | None:
        try:
            raw = self._path(key).read_text()
        except FileNotFoundError:
            return None
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(entry, dict) or self._now() >= entry.get("expires_at", 0):
            return None
        return entry.get("value")

    def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        entry = {"expires_at": self._now() + ttl_seconds, "value": value}
        # Write to a temp file then rename so a crash mid-write can't leave a
        # half-written entry that a concurrent reader might see.
        path = self._path(key)
        tmp = path.parent / f"{path.name}.tmp"
        tmp.write_text(json.dumps(entry))
        tmp.replace(path)
