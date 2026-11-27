"""Fake Tesouro sources: the title list of the ticker check (issue #93) and the daily series (#87)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from bogle.data.models import SeriesPoint, TesouroQuote
from bogle.domain.errors import MarketDataError, NetworkError, QuoteNotFoundError

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


def point(day: str, value: str) -> SeriesPoint:
    return SeriesPoint(date.fromisoformat(day), Decimal(value))


class FakeSeries:
    """What ``TesouroClient`` answers about prices: the series per title, with a call counter.

    ``fail`` makes every call raise, like a file that could not be downloaded. A
    title that is not in ``series`` is one the CSV does not publish.
    """

    def __init__(self, series: dict[str, list[SeriesPoint]] | None = None, *, fail: MarketDataError | None = None):
        self.series = series or {}
        self.fail = fail
        self.series_calls: list[str] = []
        self.quote_calls: list[str] = []

    def get_series(self, title: str) -> list[SeriesPoint]:
        self.series_calls.append(title)
        if self.fail is not None:
            raise self.fail
        if title not in self.series:
            raise QuoteNotFoundError(title, provider="tesouro")
        return list(self.series[title])

    def get_quote(self, title: str) -> TesouroQuote:
        """The latest point as the snapshot would give it, the same PU."""
        self.quote_calls.append(title)
        if self.fail is not None:
            raise self.fail
        if title not in self.series:
            raise QuoteNotFoundError(title, provider="tesouro")
        last = self.series[title][-1]
        return TesouroQuote(
            title=title, bond_type="Tesouro", maturity=date(2099, 1, 1), base_date=last.date, pu_venda=last.value
        )
