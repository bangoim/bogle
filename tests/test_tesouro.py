"""Tests for the Tesouro Direto client. HTTP mocked with respx over a compact
golden slice of the real Tesouro Transparente CSV (header + the two most recent
Data Base dates); the cache is pinned to tmp_path.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from httpx import Response

from bogle.data.cache import DiskCache
from bogle.data.models import SeriesPoint, TesouroQuote
from bogle.data.tesouro import TesouroClient, _parse_decimal, _parse_rate, mark_price
from bogle.domain.errors import MarketDataError, NetworkError, QuoteNotFoundError

FIXTURES = Path(__file__).parent / "fixtures"
CSV_URL = (
    "https://www.tesourotransparente.gov.br/ckan/dataset/df56aa42-484a-4a59-8184-7676580c81e3"
    "/resource/796d2059-14e9-44e3-80c9-2d9e30b405c1/download/PrecoTaxaTesouroDireto.csv"
)


def csv_fixture() -> str:
    return (FIXTURES / "tesouro_precotaxa.csv").read_text(encoding="utf-8")


def make_client(tmp_path: Path, **kwargs: Any) -> TesouroClient:
    kwargs.setdefault("cache", DiskCache("tesouro", base_dir=tmp_path))
    kwargs.setdefault("sleep", lambda _seconds: None)
    return TesouroClient(**kwargs)


class TestListTitles:
    @respx.mock
    def test_lists_only_latest_snapshot(self, tmp_path: Path) -> None:
        respx.get(CSV_URL).mock(return_value=Response(200, text=csv_fixture()))
        with make_client(tmp_path) as c:
            titles = c.list_titles()
        # 60 titles on the latest Data Base (the fixture also holds the prior day).
        assert len(titles) == 60
        assert titles == sorted(titles)
        assert "Tesouro IPCA+ 2035" in titles
        assert "Tesouro Selic 2029" in titles


class TestGetQuote:
    @respx.mock
    def test_parses_ipca_2035(self, tmp_path: Path) -> None:
        respx.get(CSV_URL).mock(return_value=Response(200, text=csv_fixture()))
        with make_client(tmp_path) as c:
            q = c.get_quote("Tesouro IPCA+ 2035")
        assert isinstance(q, TesouroQuote)
        assert q.title == "Tesouro IPCA+ 2035"
        assert q.bond_type == "Tesouro IPCA+"
        assert q.maturity == date(2035, 5, 15)
        assert q.base_date == date(2026, 7, 17)  # latest, not the prior 07-16
        assert q.pu_compra == Decimal("2429.39")
        assert q.pu_venda == Decimal("2404.58")
        assert q.pu_base == Decimal("2404.58")
        assert q.rate_compra == Decimal("0.0793")
        assert q.rate_venda == Decimal("0.0805")

    @respx.mock
    def test_matching_ignores_case_and_extra_spaces(self, tmp_path: Path) -> None:
        respx.get(CSV_URL).mock(return_value=Response(200, text=csv_fixture()))
        with make_client(tmp_path) as c:
            q = c.get_quote("  tesouro   IPCA+   2035 ")
        assert q.title == "Tesouro IPCA+ 2035"

    @respx.mock
    def test_semestral_and_principal_titles_are_distinct(self, tmp_path: Path) -> None:
        respx.get(CSV_URL).mock(return_value=Response(200, text=csv_fixture()))
        with make_client(tmp_path) as c:
            principal = c.get_quote("Tesouro IPCA+ 2035")
            semestral = c.get_quote("Tesouro IPCA+ com Juros Semestrais 2035")
        assert principal.bond_type == "Tesouro IPCA+"
        assert semestral.bond_type == "Tesouro IPCA+ com Juros Semestrais"
        assert principal.pu_venda != semestral.pu_venda

    @respx.mock
    def test_unknown_title_raises(self, tmp_path: Path) -> None:
        respx.get(CSV_URL).mock(return_value=Response(200, text=csv_fixture()))
        with make_client(tmp_path) as c, pytest.raises(QuoteNotFoundError):
            c.get_quote("Tesouro Inexistente 2099")


class TestCache:
    @respx.mock
    def test_second_call_does_not_redownload(self, tmp_path: Path) -> None:
        route = respx.get(CSV_URL).mock(return_value=Response(200, text=csv_fixture()))
        with make_client(tmp_path) as c:
            c.list_titles()
            c.get_quote("Tesouro IPCA+ 2035")
        assert route.call_count == 1


class TestErrors:
    @respx.mock
    def test_http_error_status_raises(self, tmp_path: Path) -> None:
        respx.get(CSV_URL).mock(return_value=Response(503, text="unavailable"))
        with make_client(tmp_path) as c, pytest.raises(MarketDataError):
            c.list_titles()

    @respx.mock
    def test_network_failure_raises_network_error(self, tmp_path: Path) -> None:
        respx.get(CSV_URL).mock(side_effect=httpx.ConnectError("boom"))
        with make_client(tmp_path) as c, pytest.raises(NetworkError):
            c.list_titles()

    @respx.mock
    def test_retries_on_429_then_succeeds(self, tmp_path: Path) -> None:
        route = respx.get(CSV_URL).mock(side_effect=[Response(429, text="slow"), Response(200, text=csv_fixture())])
        with make_client(tmp_path) as c:
            titles = c.list_titles()
        assert route.call_count == 2
        assert titles

    @respx.mock
    def test_empty_body_raises(self, tmp_path: Path) -> None:
        respx.get(CSV_URL).mock(return_value=Response(200, text=""))
        with make_client(tmp_path) as c, pytest.raises(MarketDataError):
            c.list_titles()

    @respx.mock
    def test_missing_columns_raises(self, tmp_path: Path) -> None:
        respx.get(CSV_URL).mock(return_value=Response(200, text="foo;bar\n1;2\n"))
        with make_client(tmp_path) as c, pytest.raises(MarketDataError):
            c.list_titles()


class TestNumberParsing:
    def test_parse_decimal_comma(self) -> None:
        assert _parse_decimal("2429,39") == Decimal("2429.39")

    def test_parse_decimal_thousands_and_comma(self) -> None:
        assert _parse_decimal("1.234,56") == Decimal("1234.56")

    def test_parse_decimal_negative(self) -> None:
        assert _parse_decimal("-0,02") == Decimal("-0.02")

    def test_parse_decimal_empty_and_none(self) -> None:
        assert _parse_decimal("") is None
        assert _parse_decimal("   ") is None
        assert _parse_decimal(None) is None

    def test_parse_rate_divides_by_100(self) -> None:
        assert _parse_rate("8,05") == Decimal("0.0805")
        assert _parse_rate(None) is None


HEADER = "Tipo Titulo;Data Vencimento;Data Base;Taxa Compra Manha;Taxa Venda Manha;PU Compra Manha;PU Venda Manha;PU Base Manha"
TODAY = date(2026, 11, 26)  # Thursday: the previous business day is Wednesday 2026-11-25
NOON = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


def tesouro_csv(*rows: tuple[str, str, str, str, str]) -> str:
    """A CSV, newest first as the real one: ``(type, maturity, base date, PU venda, PU base)``."""
    lines = [
        f"{kind};{maturity};{base};0,10;0,11;1,00;{venda};{base_pu}" for kind, maturity, base, venda, base_pu in rows
    ]
    return "\n".join([HEADER, *lines]) + "\n"


SELIC_2029 = ("Tesouro Selic", "01/03/2029")
SERIES_CSV = tesouro_csv(
    (*SELIC_2029, "25/11/2026", "17.500,25", "17.500,00"),
    (*SELIC_2029, "24/11/2026", "17.480,10", "17.480,00"),
    ("Tesouro Selic", "01/03/2027", "25/11/2026", "18.000,00", "18.000,00"),
    (*SELIC_2029, "23/11/2026", "17.460,00", "17.460,00"),
)


def day_client(tmp_path: Path, *, today: date = TODAY, now: datetime = NOON, **kwargs: Any) -> TesouroClient:
    return make_client(tmp_path, today=lambda: today, now=lambda: now, **kwargs)


class TestMarkPrice:
    def test_the_redemption_price_wins(self) -> None:
        assert mark_price(Decimal("10.5"), Decimal("9")) == Decimal("10.5")

    def test_zero_redemption_price_falls_back_to_the_base_price(self) -> None:
        assert mark_price(Decimal("0"), Decimal("9")) == Decimal("9")
        assert mark_price(None, Decimal("9")) == Decimal("9")

    def test_no_positive_price_is_none(self) -> None:
        assert mark_price(Decimal("0"), Decimal("0")) is None
        assert mark_price(None, None) is None


class TestGetSeries:
    @respx.mock
    def test_oldest_first_with_the_redemption_price_of_the_title(self, tmp_path: Path) -> None:
        respx.get(CSV_URL).mock(return_value=Response(200, text=SERIES_CSV))
        with day_client(tmp_path) as c:
            series = c.get_series("TESOURO SELIC 2029")  # the ticker is the uppercase name
        assert series == [
            SeriesPoint(date(2026, 11, 23), Decimal("17460.00")),
            SeriesPoint(date(2026, 11, 24), Decimal("17480.10")),
            SeriesPoint(date(2026, 11, 25), Decimal("17500.25")),
        ]

    @respx.mock
    def test_the_last_point_is_the_price_of_the_quote(self, tmp_path: Path) -> None:
        respx.get(CSV_URL).mock(return_value=Response(200, text=SERIES_CSV))
        with day_client(tmp_path) as c:
            last = c.get_series("Tesouro Selic 2029")[-1]
            quote = c.get_quote("Tesouro Selic 2029")
        assert (last.date, last.value) == (quote.base_date, mark_price(quote.pu_venda, quote.pu_base))

    @respx.mock
    def test_a_zero_redemption_price_uses_the_base_price_and_no_price_is_skipped(self, tmp_path: Path) -> None:
        text = tesouro_csv(
            (*SELIC_2029, "25/11/2026", "17.500,00", "17.500,00"),
            (*SELIC_2029, "24/11/2026", "0,00", "17.480,00"),
            (*SELIC_2029, "23/11/2026", "0,00", "0,00"),
        )
        respx.get(CSV_URL).mock(return_value=Response(200, text=text))
        with day_client(tmp_path) as c:
            series = c.get_series("Tesouro Selic 2029")
        assert [(p.date, p.value) for p in series] == [
            (date(2026, 11, 24), Decimal("17480.00")),
            (date(2026, 11, 25), Decimal("17500.00")),
        ]

    @respx.mock
    def test_a_name_with_two_maturities_keeps_the_one_of_the_newest_base(self, tmp_path: Path) -> None:
        text = tesouro_csv(
            ("Tesouro Prefixado", "01/01/2013", "25/11/2012", "800,00", "800,00"),
            ("Tesouro Prefixado", "01/07/2013", "24/11/2012", "700,00", "700,00"),
            ("Tesouro Prefixado", "01/01/2013", "23/11/2012", "790,00", "790,00"),
        )
        respx.get(CSV_URL).mock(return_value=Response(200, text=text))
        with day_client(tmp_path) as c:
            series = c.get_series("Tesouro Prefixado 2013")
        assert [p.value for p in series] == [Decimal("790.00"), Decimal("800.00")]

    @respx.mock
    def test_an_unknown_title_raises(self, tmp_path: Path) -> None:
        respx.get(CSV_URL).mock(return_value=Response(200, text=SERIES_CSV))
        with day_client(tmp_path) as c, pytest.raises(QuoteNotFoundError):
            c.get_series("Tesouro Selic 2099")


class TestOneDownloadADay:
    @respx.mock
    def test_series_of_two_titles_titles_and_a_second_client_share_one_download(self, tmp_path: Path) -> None:
        route = respx.get(CSV_URL).mock(return_value=Response(200, text=SERIES_CSV))
        with day_client(tmp_path) as c:
            c.get_series("Tesouro Selic 2029")
            c.get_series("Tesouro Selic 2027")
            c.list_titles()
        with day_client(tmp_path) as c:  # another screen, same disk
            c.get_series("Tesouro Selic 2027")
            c.get_quote("Tesouro Selic 2029")
        assert route.call_count == 1

    @respx.mock
    def test_a_title_added_later_in_the_day_is_read_from_the_local_file(self, tmp_path: Path) -> None:
        route = respx.get(CSV_URL).mock(return_value=Response(200, text=SERIES_CSV))
        with day_client(tmp_path) as c:
            c.list_titles()
        route.side_effect = httpx.ConnectError("boom")  # any request now would fail the read
        with day_client(tmp_path) as c:
            assert c.get_series("Tesouro Selic 2027")
        assert route.call_count == 1

    @respx.mock
    def test_a_new_day_downloads_again(self, tmp_path: Path) -> None:
        route = respx.get(CSV_URL).mock(return_value=Response(200, text=SERIES_CSV))
        with day_client(tmp_path) as c:
            c.get_series("Tesouro Selic 2029")
        with day_client(tmp_path, today=TODAY + timedelta(days=1)) as c:
            c.get_series("Tesouro Selic 2029")
        assert route.call_count == 2

    @respx.mock
    def test_a_failed_download_raises_is_not_stamped_and_the_next_client_retries(self, tmp_path: Path) -> None:
        route = respx.get(CSV_URL).mock(side_effect=[Response(503, text="unavailable"), Response(200, text=SERIES_CSV)])
        with day_client(tmp_path) as c:
            with pytest.raises(MarketDataError):
                c.get_series("Tesouro Selic 2029")
            with pytest.raises(MarketDataError):  # the same client does not insist
                c.get_series("Tesouro Selic 2027")
            assert route.call_count == 1
        with day_client(tmp_path) as c:
            assert c.get_series("Tesouro Selic 2029")
        assert route.call_count == 2

    @respx.mock
    def test_a_body_that_is_not_the_csv_is_not_kept(self, tmp_path: Path) -> None:
        route = respx.get(CSV_URL).mock(
            side_effect=[Response(200, text="foo;bar\n1;2\n"), Response(200, text=SERIES_CSV)]
        )
        with day_client(tmp_path) as c, pytest.raises(MarketDataError):
            c.get_series("Tesouro Selic 2029")
        with day_client(tmp_path) as c:
            assert c.get_series("Tesouro Selic 2029")
        assert route.call_count == 2


# Wednesday 2026-11-25 is D-1 of TODAY. The first copy only reaches Tuesday.
EARLY_CSV = tesouro_csv((*SELIC_2029, "24/11/2026", "17.480,10", "17.480,00"))
FULL_CSV = tesouro_csv(
    (*SELIC_2029, "25/11/2026", "17.500,25", "17.500,00"),
    (*SELIC_2029, "24/11/2026", "17.480,10", "17.480,00"),
)
VALIDATORS = {"ETag": '"abc"', "Last-Modified": "Thu, 26 Nov 2026 11:00:00 GMT"}


class TestConditionalRecheck:
    @respx.mock
    def test_a_file_without_d1_asks_again_with_the_validators_and_a_304_keeps_it(self, tmp_path: Path) -> None:
        route = respx.get(CSV_URL).mock(side_effect=[Response(200, text=EARLY_CSV, headers=VALIDATORS), Response(304)])
        with day_client(tmp_path, now=NOON) as c:
            assert c.get_series("Tesouro Selic 2029")[-1].date == date(2026, 11, 24)
        with day_client(tmp_path, now=NOON + timedelta(minutes=31)) as c:
            assert c.get_series("Tesouro Selic 2029")[-1].date == date(2026, 11, 24)
        assert route.call_count == 2
        sent = route.calls[1].request.headers
        assert sent["if-none-match"] == '"abc"'
        assert sent["if-modified-since"] == "Thu, 26 Nov 2026 11:00:00 GMT"
        assert "if-none-match" not in route.calls[0].request.headers

    @respx.mock
    def test_a_200_replaces_the_file(self, tmp_path: Path) -> None:
        route = respx.get(CSV_URL).mock(
            side_effect=[Response(200, text=EARLY_CSV, headers=VALIDATORS), Response(200, text=FULL_CSV)]
        )
        with day_client(tmp_path, now=NOON) as c:
            c.get_series("Tesouro Selic 2029")
        with day_client(tmp_path, now=NOON + timedelta(minutes=31)) as c:
            assert c.get_series("Tesouro Selic 2029")[-1].date == date(2026, 11, 25)
        with day_client(tmp_path, now=NOON + timedelta(hours=3)) as c:
            assert c.get_series("Tesouro Selic 2029")[-1].date == date(2026, 11, 25)
        assert route.call_count == 2  # the third client reads the replaced file, D-1 included

    @respx.mock
    def test_the_recheck_is_throttled_to_one_every_30_minutes(self, tmp_path: Path) -> None:
        route = respx.get(CSV_URL).mock(
            side_effect=[Response(200, text=EARLY_CSV, headers=VALIDATORS), Response(304), Response(304)]
        )
        with day_client(tmp_path, now=NOON) as c:
            c.get_series("Tesouro Selic 2029")
        with day_client(tmp_path, now=NOON + timedelta(minutes=29)) as c:  # too soon after the download
            c.get_series("Tesouro Selic 2029")
        assert route.call_count == 1
        with day_client(tmp_path, now=NOON + timedelta(minutes=31)) as c:  # 304: the clock restarts
            c.get_series("Tesouro Selic 2029")
        with day_client(tmp_path, now=NOON + timedelta(minutes=50)) as c:
            c.get_series("Tesouro Selic 2029")
        assert route.call_count == 2
        with day_client(tmp_path, now=NOON + timedelta(minutes=62)) as c:
            c.get_series("Tesouro Selic 2029")
        assert route.call_count == 3

    @respx.mock
    def test_one_recheck_per_client_instance(self, tmp_path: Path) -> None:
        route = respx.get(CSV_URL).mock(side_effect=[Response(200, text=EARLY_CSV, headers=VALIDATORS), Response(304)])
        with day_client(tmp_path, now=NOON) as c:
            c.get_series("Tesouro Selic 2029")
        with day_client(tmp_path, now=NOON + timedelta(minutes=31)) as c:
            c.get_series("Tesouro Selic 2029")
            c.list_titles()
            c.get_quote("Tesouro Selic 2029")
        assert route.call_count == 2

    @respx.mock
    def test_no_recheck_once_the_file_has_d1(self, tmp_path: Path) -> None:
        route = respx.get(CSV_URL).mock(return_value=Response(200, text=FULL_CSV, headers=VALIDATORS))
        with day_client(tmp_path, now=NOON) as c:
            c.get_series("Tesouro Selic 2029")
        with day_client(tmp_path, now=NOON + timedelta(hours=5)) as c:
            c.get_series("Tesouro Selic 2029")
        assert route.call_count == 1

    @respx.mock
    def test_a_failed_recheck_keeps_the_file(self, tmp_path: Path) -> None:
        route = respx.get(CSV_URL).mock(
            side_effect=[Response(200, text=EARLY_CSV, headers=VALIDATORS), httpx.ConnectError("boom")]
        )
        with day_client(tmp_path, now=NOON) as c:
            c.get_series("Tesouro Selic 2029")
        with day_client(tmp_path, now=NOON + timedelta(minutes=31)) as c:
            assert c.get_series("Tesouro Selic 2029")[-1].date == date(2026, 11, 24)
        assert route.call_count == 2

    @respx.mock
    def test_without_validators_there_is_nothing_to_ask(self, tmp_path: Path) -> None:
        route = respx.get(CSV_URL).mock(return_value=Response(200, text=EARLY_CSV))
        with day_client(tmp_path, now=NOON) as c:
            c.get_series("Tesouro Selic 2029")
        with day_client(tmp_path, now=NOON + timedelta(hours=2)) as c:
            c.get_series("Tesouro Selic 2029")
        assert route.call_count == 1


@pytest.mark.live
def test_live_ipca_quote(tmp_path: Path) -> None:
    """Smoke test against the real 14MB CSV. Deselected by default; run with -m live."""
    with TesouroClient(cache=DiskCache("tesouro", base_dir=tmp_path)) as c:
        titles = c.list_titles()
    assert titles
    assert any(t.startswith("Tesouro ") for t in titles)
