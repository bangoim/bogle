"""Check the ticker of a TESOURO asset against the titles Tesouro Transparente publishes.

The ticker of a TESOURO *is* the title name (``"Tesouro IPCA+ 2035"``): that is the
key :meth:`~bogle.data.tesouro.TesouroClient.get_quote` prices by, so a ticker that
does not match one of them is never priced (issue #93). ``bogle add`` and the TUI's
asset form both come through here, so the same name is accepted or refused in both.

The comparison is :func:`~bogle.data.tesouro.normalize_title` — the one ``get_quote``
uses — so whatever passes here is found there (tickers are stored uppercased, and the
match ignores case). The title list comes from the same cached snapshot the prices
do; when it cannot be obtained at all, the add goes through with a warning instead
of making registering depend on the network.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol

from bogle.data.tesouro import TesouroClient, normalize_title
from bogle.domain.errors import MarketDataError, ValidationError


class TitleSource(Protocol):
    def list_titles(self) -> list[str]: ...


def default_source() -> TitleSource:
    return TesouroClient()


def check_tesouro_title(ticker: str, maturity: date | datetime, *, source: TitleSource | None = None) -> str | None:
    """Refuse a ticker that is not a published title; return a warning when unchecked.

    Raises :class:`ValidationError` when the list was obtained and no title matches,
    suggesting the titles that mature in the same year as ``maturity``. Returns
    ``None`` when the name matches, and a warning when the list could not be
    obtained (:class:`MarketDataError`: network, HTTP, malformed file) — the caller
    saves the asset and shows it.
    """
    client = source if source is not None else default_source()
    try:
        titles = client.list_titles()
    except MarketDataError:
        return (
            f"não foi possível conferir o nome de '{ticker}' no Tesouro Transparente; "
            "se não for o nome exato do título, ele não será precificado."
        )
    finally:
        if source is None and isinstance(client, TesouroClient):
            client.close()
    target = normalize_title(ticker)
    if any(normalize_title(title) == target for title in titles):
        return None
    message = f"'{ticker}' não é um título do Tesouro Transparente."
    same_year = [title for title in titles if title.endswith(f" {maturity.year}")]
    if same_year:
        message += f" Títulos com vencimento em {maturity.year}: {', '.join(same_year)}."
    raise ValidationError(message)
