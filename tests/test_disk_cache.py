"""Tests for the on-disk JSON cache. All I/O is confined to tmp_path."""

from __future__ import annotations

from pathlib import Path

import pytest

from bogle.data.cache import DiskCache, default_cache_dir


class TestGetSet:
    def test_set_then_get_roundtrips(self, tmp_path: Path) -> None:
        cache = DiskCache("ns", base_dir=tmp_path)
        cache.set("k", {"a": 1, "b": [2, 3]}, ttl_seconds=100)
        assert cache.get("k") == {"a": 1, "b": [2, 3]}

    def test_missing_key_is_none(self, tmp_path: Path) -> None:
        assert DiskCache("ns", base_dir=tmp_path).get("nope") is None

    def test_overwrite_updates_value(self, tmp_path: Path) -> None:
        cache = DiskCache("ns", base_dir=tmp_path)
        cache.set("k", 1, 100)
        cache.set("k", 2, 100)
        assert cache.get("k") == 2

    def test_namespaces_are_isolated(self, tmp_path: Path) -> None:
        a = DiskCache("a", base_dir=tmp_path)
        b = DiskCache("b", base_dir=tmp_path)
        a.set("k", "va", 100)
        assert a.get("k") == "va"
        assert b.get("k") is None

    def test_key_with_unsafe_chars(self, tmp_path: Path) -> None:
        cache = DiskCache("ns", base_dir=tmp_path)
        cache.set("12:2020-01-01:2026-12-31", [1, 2], 100)
        assert cache.get("12:2020-01-01:2026-12-31") == [1, 2]


class TestExpiry:
    def test_returns_value_before_expiry_and_none_after(self, tmp_path: Path) -> None:
        clock = [1000.0]
        cache = DiskCache("ns", base_dir=tmp_path, now=lambda: clock[0])
        cache.set("k", "v", ttl_seconds=10)
        assert cache.get("k") == "v"
        clock[0] = 1009.9
        assert cache.get("k") == "v"
        clock[0] = 1010.0  # expiry is inclusive (>=)
        assert cache.get("k") is None
        clock[0] = 5000.0
        assert cache.get("k") is None


class TestCorruption:
    def test_corrupt_file_is_treated_as_miss(self, tmp_path: Path) -> None:
        cache = DiskCache("ns", base_dir=tmp_path)
        cache.set("k", "v", 100)
        stored = next((tmp_path / "ns").glob("*.json"))
        stored.write_text("{ not json")
        assert cache.get("k") is None


class TestDefaultDir:
    def test_honors_xdg_cache_home(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        assert default_cache_dir() == tmp_path / "bogle"

    def test_falls_back_to_home_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        assert default_cache_dir() == Path.home() / ".cache" / "bogle"
