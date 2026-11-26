"""Fake title sources for the tests of the TESOURO ticker check (issue #93)."""

from __future__ import annotations

from bogle.domain.errors import NetworkError

TITLES = [
    "Tesouro IPCA+ 2029",
    "Tesouro IPCA+ 2035",
    "Tesouro IPCA+ com Juros Semestrais 2035",
    "Tesouro Prefixado 2029",
    "Tesouro Selic 2029",
]


class FakeTitles:
    """The titles of the latest snapshot, as ``TesouroClient.list_titles`` returns them."""

    def __init__(self, titles: list[str] | None = None) -> None:
        self.titles = list(TITLES if titles is None else titles)
        self.calls = 0

    def list_titles(self) -> list[str]:
        self.calls += 1
        return sorted(self.titles)


class OfflineTitles:
    """No connection and no cache: what ``TesouroClient`` raises in that case."""

    def list_titles(self) -> list[str]:
        raise NetworkError("tesouro", "sem conexão")
