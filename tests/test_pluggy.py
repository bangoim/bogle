"""Tests for the Pluggy client (issue #114). Every HTTP call is mocked with respx.

The fixtures (``tests/fixtures/pluggy_*.json``) are synthetic: shaped like the
API's answers (OpenAPI, and the fields confirmed live on 2026-11-27), with
made-up ids, names and numbers. The error bodies are the ones the live API
sent: a missing or invalid key is 403 ``API_KEY_MISSING_OR_INVALID``, an unknown
item is 404 ``ITEM_NOT_FOUND``.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx
from httpx import Response

from bogle.data.pluggy import (
    API_KEY_TTL,
    MAX_ITEMS,
    PluggyClient,
    PluggyConfig,
    any_config,
    load_config,
    local_day,
    missing_config,
    parse_transaction,
)
from bogle.domain.errors import NetworkError, PluggyAuthError, PluggyError, PluggyNotConfiguredError, RateLimitError

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://api.pluggy.ai"
ITEM = "0b5e1c3a-1111-4a2b-8c3d-000000000001"
BANK = "a0000000-0000-4000-8000-00000000000b"
CARD = "a0000000-0000-4000-8000-00000000000c"
SECRET = "s3cr3t-value-never-shown"

ID_A = "11111111-1111-4111-8111-111111111111"
ID_B = "22222222-2222-4222-8222-222222222222"


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def client(clock: Clock | None = None, **kwargs: Any) -> PluggyClient:
    kwargs.setdefault("sleep", lambda _seconds: None)
    return PluggyClient("client-id", SECRET, clock=clock or Clock(), **kwargs)


def auth_route(*keys: str) -> respx.Route:
    route = respx.post(f"{BASE}/auth")
    route.side_effect = [Response(200, json={"apiKey": key}) for key in keys] or [
        Response(200, json={"apiKey": "key-1"})
    ]
    return route


def forbidden() -> Response:
    return Response(
        403,
        json={
            "code": 403,
            "codeDescription": "API_KEY_MISSING_OR_INVALID",
            "message": "Missing or invalid authorization token",
        },
    )


def query(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(urlsplit(str(request.url)).query)


# ------------------------------------------------------------- configuracao


class TestConfig:
    def env(self, **overrides: str) -> dict[str, str]:
        return {"PLUGGY_CLIENT_ID": "id", "PLUGGY_CLIENT_SECRET": SECRET, "PLUGGY_ITEM_IDS": ID_A} | overrides

    def test_reads_the_three_variables(self) -> None:
        config = load_config(self.env(PLUGGY_ITEM_IDS=f" {ID_A} , {ID_B.upper()} "))
        assert config == PluggyConfig("id", SECRET, (ID_A, ID_B))
        assert SECRET not in repr(config)

    def test_names_what_is_missing(self) -> None:
        with pytest.raises(PluggyNotConfiguredError, match="faltam PLUGGY_CLIENT_SECRET, PLUGGY_ITEM_IDS") as caught:
            load_config({"PLUGGY_CLIENT_ID": "id"})
        assert caught.value.missing == ("PLUGGY_CLIENT_SECRET", "PLUGGY_ITEM_IDS")

    def test_empty_counts_as_missing(self) -> None:
        env = self.env(PLUGGY_CLIENT_ID="  ")
        assert missing_config(env) == ["PLUGGY_CLIENT_ID"]
        with pytest.raises(PluggyNotConfiguredError, match=r"^falta PLUGGY_CLIENT_ID"):
            load_config(env)

    def test_nothing_at_all(self) -> None:
        empty: dict[str, str] = dict.fromkeys(("PLUGGY_CLIENT_ID", "PLUGGY_CLIENT_SECRET", "PLUGGY_ITEM_IDS"), "")
        assert not any_config(empty)
        assert not any_config({})
        assert any_config({"PLUGGY_ITEM_IDS": ID_A})

    def test_an_unresolved_1password_reference(self) -> None:
        env = self.env(PLUGGY_CLIENT_SECRET="op://Passwords/pluggy/credential")
        with pytest.raises(
            PluggyNotConfiguredError, match="PLUGGY_CLIENT_SECRET é uma referência do 1Password"
        ) as caught:
            load_config(env)
        assert "op run" in str(caught.value)

    def test_an_invalid_item_id_is_named(self) -> None:
        with pytest.raises(PluggyNotConfiguredError, match="PLUGGY_ITEM_IDS tem um id inválido: 'abc'"):
            load_config(self.env(PLUGGY_ITEM_IDS=f"{ID_A},abc"))

    def test_more_than_five_connections_are_refused(self) -> None:
        ids = ",".join(f"{index:08d}-1111-4111-8111-111111111111" for index in range(MAX_ITEMS + 1))
        with pytest.raises(PluggyNotConfiguredError, match="aceita até 5 conexões, recebeu 6"):
            load_config(self.env(PLUGGY_ITEM_IDS=ids))

    def test_repeated_ids_count_once(self) -> None:
        ids = ",".join([ID_A] * 6)
        assert load_config(self.env(PLUGGY_ITEM_IDS=ids)).item_ids == (ID_A,)

    def test_commas_only_is_missing(self) -> None:
        with pytest.raises(PluggyNotConfiguredError, match="falta PLUGGY_ITEM_IDS"):
            load_config(self.env(PLUGGY_ITEM_IDS=" , ,"))

    def test_a_client_without_credentials_says_so_before_any_request(self) -> None:
        with respx.mock(assert_all_called=False) as mock:
            auth = mock.post(f"{BASE}/auth")
            with (
                PluggyClient("", "", sleep=lambda _s: None) as c,
                pytest.raises(PluggyNotConfiguredError, match="faltam PLUGGY_CLIENT_ID, PLUGGY_CLIENT_SECRET"),
            ):
                c.list_categories()
            assert not auth.called


# ------------------------------------------------------------------ parsing


class TestParsing:
    @respx.mock
    def test_item(self) -> None:
        auth_route()
        respx.get(f"{BASE}/items/{ITEM}").mock(return_value=Response(200, json=fixture("pluggy_item.json")))
        with client() as c:
            item = c.get_item(ITEM)
        assert item.id == ITEM
        assert item.status == "UPDATED"
        assert item.connector_name == "MeuPluggy"
        assert item.connector_id == 200
        assert item.consent_expires_at == datetime(2027, 11, 20, 12, tzinfo=UTC)
        assert item.last_updated_at == datetime(2026, 11, 27, 6, 10, tzinfo=UTC)
        assert item.error_code is None

    @respx.mock
    def test_accounts(self) -> None:
        auth_route()
        route = respx.get(f"{BASE}/accounts").mock(return_value=Response(200, json=fixture("pluggy_accounts.json")))
        with client() as c:
            bank, card = c.list_accounts(ITEM)
        assert query(route.calls.last.request) == {"itemId": [ITEM], "page": ["1"]}
        assert (bank.type, bank.subtype, bank.transfer_number) == ("BANK", "CHECKING_ACCOUNT", "0001/12345-6")
        assert bank.balance == Decimal("1520.35")
        assert bank.marketing_name is None
        assert (card.type, card.number, card.credit_brand) == ("CREDIT", "9999", "MASTERCARD")
        assert card.credit_limit == Decimal("5000")
        assert card.balance_due_date == date(2026, 12, 10)
        assert card.marketing_name == "Cartão Exemplo Platinum"
        assert card.raw["creditData"]["level"] == "PLATINUM"

    @respx.mock
    def test_card_transactions_carry_the_card_metadata(self) -> None:
        auth_route()
        respx.get(f"{BASE}/v2/transactions").mock(
            return_value=Response(200, json=fixture("pluggy_transactions_card.json"))
        )
        with client() as c:
            posted, refund, noon, pending = c.list_transactions(CARD)
        assert posted.amount == Decimal("89.9")
        assert isinstance(posted.amount, Decimal)
        assert posted.status == "POSTED"
        assert posted.provider_id is None  # o Meu Pluggy nao manda providerId
        assert posted.bill_id == "b0000000-0000-4000-8000-000000000011"
        assert posted.bill_post_date == date(2026, 11, 20)
        assert posted.bill_forecast_month == "2026-12"
        assert posted.payment_type == "SINGLE"
        assert posted.payee_mcc == "5411"
        assert posted.merchant_cnpj == "00000000000000"
        assert posted.date == datetime(2026, 11, 20, 3, tzinfo=UTC)  # meia-noite em Sao Paulo
        assert posted.local_date == date(2026, 11, 20)
        # Estorno de IOF a 00:00:00.001Z: o dia UTC, que e o billPostDate.
        assert refund.date == datetime(2026, 11, 18, 0, 0, 0, 1000, tzinfo=UTC)
        assert refund.local_date == date(2026, 11, 18)
        assert noon.local_date == date(2026, 11, 10)
        assert (pending.status, pending.installment_number, pending.total_installments) == ("PENDING", 3, 10)
        assert pending.bill_id is None
        assert pending.payee_mcc == "0742"
        assert pending.local_date == date(2027, 1, 20)

    def test_the_card_day_is_the_bill_post_date(self) -> None:
        # Mesmo quando o `date` levaria a outro dia, vale o dia lancado na fatura.
        body = fixture("pluggy_transactions_card.json")["results"][0]
        body["date"] = "2026-11-21T02:00:00.000Z"  # 23:00 de 20/11 em Sao Paulo
        body["creditCardMetadata"]["billPostDate"] = "2026-11-21"
        assert parse_transaction(body).local_date == date(2026, 11, 21)
        body["creditCardMetadata"]["billPostDate"] = None
        assert parse_transaction(body).local_date == date(2026, 11, 20)

    @respx.mock
    def test_bank_transactions(self) -> None:
        auth_route()
        respx.get(f"{BASE}/v2/transactions").mock(
            return_value=Response(200, json=fixture("pluggy_transactions_bank.json"))
        )
        with client() as c:
            sent, received = c.list_transactions(BANK)
        assert sent.amount == Decimal("-45.5")
        assert sent.operation_type == "PIX"
        assert sent.payment_data == {"paymentMethod": "PIX", "reason": None}
        assert sent.date == datetime(2026, 11, 19, 14, 32, 7, 512000, tzinfo=UTC)
        assert sent.local_date == date(2026, 11, 19)
        assert sent.bill_id is None
        # 01:15Z de 20/11 e 22:15 de 19/11 em Sao Paulo.
        assert received.local_date == date(2026, 11, 19)

    @respx.mock
    def test_bills_and_categories(self) -> None:
        auth_route()
        respx.get(f"{BASE}/bills").mock(return_value=Response(200, json=fixture("pluggy_bills.json")))
        respx.get(f"{BASE}/categories").mock(return_value=Response(200, json=fixture("pluggy_categories.json")))
        with client() as c:
            [statement] = c.list_bills(CARD)
            categories = c.list_categories()
        assert statement.due_date == date(2026, 12, 10)
        assert statement.closing_date == date(2026, 12, 3)
        assert statement.total_amount == Decimal("812.4")
        assert statement.payments[0]["valueType"] == "FULL_PAYMENT"
        assert [category.id for category in categories] == ["07000000", "07010000", "05000000"]
        assert categories[1].parent_id == "07000000"
        assert categories[2].description_translated == "Transferências"


class TestLocalDay:
    def test_midnight_utc_is_a_bare_date(self) -> None:
        assert local_day(datetime(2026, 11, 1, tzinfo=UTC)) == date(2026, 11, 1)

    def test_milliseconds_after_midnight_utc_still_a_bare_date(self) -> None:
        assert local_day(datetime(2026, 11, 1, 0, 0, 0, 1000, tzinfo=UTC)) == date(2026, 11, 1)
        assert local_day(datetime(2026, 11, 1, 0, 0, 0, 999000, tzinfo=UTC)) == date(2026, 11, 1)

    def test_one_second_past_midnight_utc_is_an_instant(self) -> None:
        assert local_day(datetime(2026, 11, 1, 0, 0, 1, tzinfo=UTC)) == date(2026, 10, 31)

    def test_midnight_in_sao_paulo_and_noon_utc(self) -> None:
        assert local_day(datetime(2026, 11, 20, 3, tzinfo=UTC)) == date(2026, 11, 20)
        assert local_day(datetime(2026, 11, 10, 12, 0, 0, 1000, tzinfo=UTC)) == date(2026, 11, 10)

    def test_an_instant_is_read_in_sao_paulo(self) -> None:
        assert local_day(datetime(2026, 11, 2, 1, 30, tzinfo=UTC)) == date(2026, 11, 1)
        assert local_day(datetime(2026, 11, 2, 3, 0, tzinfo=UTC)) == date(2026, 11, 2)


# -------------------------------------------------------------------- auth


class TestAuth:
    @respx.mock
    def test_posts_the_credentials_and_sends_the_key(self) -> None:
        auth = auth_route("key-1")
        categories = respx.get(f"{BASE}/categories").mock(
            return_value=Response(200, json=fixture("pluggy_categories.json"))
        )
        with client() as c:
            c.list_categories()
        assert json.loads(auth.calls.last.request.content) == {"clientId": "client-id", "clientSecret": SECRET}
        assert categories.calls.last.request.headers["X-API-KEY"] == "key-1"

    @respx.mock
    def test_one_auth_for_many_reads(self) -> None:
        auth = auth_route("key-1")
        respx.get(f"{BASE}/categories").mock(return_value=Response(200, json=fixture("pluggy_categories.json")))
        respx.get(f"{BASE}/accounts").mock(return_value=Response(200, json=fixture("pluggy_accounts.json")))
        with client() as c:
            c.list_categories()
            c.list_accounts(ITEM)
            c.list_categories()
        assert auth.call_count == 1

    @respx.mock
    def test_the_key_is_renewed_by_age(self) -> None:
        auth = auth_route("key-1", "key-2")
        route = respx.get(f"{BASE}/categories").mock(return_value=Response(200, json=fixture("pluggy_categories.json")))
        clock = Clock()
        with client(clock) as c:
            c.list_categories()
            clock.now += API_KEY_TTL - 1
            c.list_categories()
            assert auth.call_count == 1
            clock.now += 2
            c.list_categories()
        assert auth.call_count == 2
        assert route.calls.last.request.headers["X-API-KEY"] == "key-2"

    @pytest.mark.parametrize("status", [401, 403])
    @respx.mock
    def test_a_refused_key_is_renewed_and_the_read_repeated_once(self, status: int) -> None:
        auth = auth_route("key-1", "key-2")
        route = respx.get(f"{BASE}/categories")
        route.side_effect = [
            Response(status, json={"code": status, "message": "expired"}),
            Response(200, json=fixture("pluggy_categories.json")),
        ]
        with client() as c:
            assert len(c.list_categories()) == 3
        assert auth.call_count == 2
        assert route.calls.last.request.headers["X-API-KEY"] == "key-2"

    @respx.mock
    def test_a_second_refusal_is_an_auth_error(self) -> None:
        auth_route("key-1", "key-2")
        respx.get(f"{BASE}/categories").mock(return_value=forbidden())
        with client() as c, pytest.raises(PluggyAuthError, match=r"recusou a chave de API \(HTTP 403\)") as caught:
            c.list_categories()
        assert caught.value.code == "API_KEY_MISSING_OR_INVALID"

    @respx.mock
    def test_an_expired_secret_says_what_to_do(self) -> None:
        respx.post(f"{BASE}/auth").mock(
            return_value=Response(
                401,
                json={
                    "code": 401,
                    "codeDescription": "CLIENT_SECRET_EXPIRED",
                    "message": "client secret has expired, generate a new one",
                },
            )
        )
        with client() as c, pytest.raises(PluggyAuthError, match="client secret da Pluggy venceu") as caught:
            c.list_categories()
        assert caught.value.code == "CLIENT_SECRET_EXPIRED"
        assert SECRET not in str(caught.value)

    @respx.mock
    def test_bad_credentials(self) -> None:
        respx.post(f"{BASE}/auth").mock(
            return_value=Response(
                401,
                json={"code": 401, "codeDescription": "CLIENT_KEYS_UNAUTHORIZED", "message": "Client keys are invalid"},
            )
        )
        with client() as c, pytest.raises(PluggyAuthError, match="confira PLUGGY_CLIENT_ID e PLUGGY_CLIENT_SECRET"):
            c.list_categories()

    @respx.mock
    def test_secrets_never_reach_a_message(self) -> None:
        auth_route("key-secreta")
        respx.get(f"{BASE}/categories").mock(return_value=Response(500, json={"code": 500, "message": "boom"}))
        with client() as c, pytest.raises(PluggyError) as caught:
            c.list_categories()
        assert SECRET not in str(caught.value)
        assert "key-secreta" not in str(caught.value)


# ---------------------------------------------------------------- paginacao


def tx_body(tx_id: str) -> dict[str, Any]:
    return {
        "id": tx_id,
        "accountId": CARD,
        "date": "2026-11-01T00:00:00.000Z",
        "description": "x",
        "amount": 1,
        "type": "DEBIT",
        "status": "POSTED",
    }


class TestCursor:
    @respx.mock
    def test_follows_next_with_the_decoded_after_and_the_same_filters(self) -> None:
        auth_route()
        route = respx.get(f"{BASE}/v2/transactions")
        route.side_effect = [
            Response(200, json={"results": [tx_body("t1")], "next": f"?accountId={CARD}&after=YWJj%3D%3D"}),
            Response(200, json={"results": [tx_body("t2")], "next": f"?accountId={CARD}&after=ZGVm"}),
            Response(200, json={"results": [tx_body("t3")], "next": None}),
        ]
        with client() as c:
            rows = c.list_transactions(CARD, date_from=date(2026, 10, 1), date_to=date(2027, 7, 31))
        assert [row.id for row in rows] == ["t1", "t2", "t3"]
        first, second, third = (query(call.request) for call in route.calls)
        assert first == {"accountId": [CARD], "dateFrom": ["2026-10-01"], "dateTo": ["2027-07-31"]}
        assert second == {**first, "after": ["YWJj=="]}  # %3D decodificado, nunca reenviado cru
        assert third == {**first, "after": ["ZGVm"]}

    @respx.mock
    def test_page_size_is_never_sent(self) -> None:
        # /v2/transactions com pageSize=500 devolve 200 e lista vazia (visto ao
        # vivo em 2026-11-27): o parametro nao vai em rota nenhuma.
        auth_route()
        routes = [
            respx.get(f"{BASE}/v2/transactions").mock(return_value=Response(200, json={"results": [], "next": None})),
            respx.get(f"{BASE}/accounts").mock(return_value=Response(200, json=fixture("pluggy_accounts.json"))),
            respx.get(f"{BASE}/bills").mock(return_value=Response(200, json=fixture("pluggy_bills.json"))),
            respx.get(f"{BASE}/categories").mock(return_value=Response(200, json=fixture("pluggy_categories.json"))),
        ]
        with client() as c:
            c.list_transactions(CARD, date_from=date(2026, 1, 1))
            c.list_accounts(ITEM)
            c.list_bills(CARD)
            c.list_categories()
        for route in routes:
            assert route.called
            assert "pageSize" not in query(route.calls.last.request)

    @respx.mock
    def test_no_range_sends_no_dates(self) -> None:
        auth_route()
        route = respx.get(f"{BASE}/v2/transactions").mock(
            return_value=Response(200, json={"results": [], "next": None})
        )
        with client() as c:
            assert c.list_transactions(CARD) == []
        assert query(route.calls.last.request) == {"accountId": [CARD]}

    @respx.mock
    def test_a_repeated_cursor_stops_with_an_error(self) -> None:
        auth_route()
        respx.get(f"{BASE}/v2/transactions").mock(
            return_value=Response(200, json={"results": [tx_body("t1")], "next": "?after=same"})
        )
        with client() as c, pytest.raises(PluggyError, match="repetiu o cursor"):
            c.list_transactions(CARD)

    @respx.mock
    def test_pages_until_total_pages(self) -> None:
        auth_route()
        route = respx.get(f"{BASE}/categories")
        route.side_effect = [
            Response(200, json={"page": 1, "total": 3, "totalPages": 2, "results": [{"id": "1", "description": "a"}]}),
            Response(200, json={"page": 2, "total": 3, "totalPages": 2, "results": [{"id": "2", "description": "b"}]}),
        ]
        with client() as c:
            assert [category.id for category in c.list_categories()] == ["1", "2"]
        assert [query(call.request)["page"] for call in route.calls] == [["1"], ["2"]]


# ------------------------------------------------------------------- erros


class TestErrors:
    @respx.mock
    def test_a_server_error_carries_the_message_and_code(self) -> None:
        auth_route()
        respx.get(f"{BASE}/accounts").mock(
            return_value=Response(500, json={"code": 500, "codeDescription": "INTERNAL", "message": "falhou"})
        )
        with client() as c, pytest.raises(PluggyError, match=r"erro da Pluggy: falhou \(HTTP 500\)") as caught:
            c.list_accounts(ITEM)
        assert caught.value.code == "INTERNAL"

    @respx.mock
    def test_an_unknown_item_names_the_variable(self) -> None:
        auth_route()
        respx.get(f"{BASE}/items/{ITEM}").mock(
            return_value=Response(
                404, json={"code": 404, "codeDescription": "ITEM_NOT_FOUND", "message": "item not found"}
            )
        )
        with client() as c, pytest.raises(PluggyError, match="não encontrada na Pluggy: confira PLUGGY_ITEM_IDS"):
            c.get_item(ITEM)

    @respx.mock
    def test_429_backs_off_then_gives_up(self) -> None:
        auth_route()
        route = respx.get(f"{BASE}/categories").mock(return_value=Response(429, json={"code": 429}))
        waits: list[float] = []
        with client(sleep=waits.append) as c, pytest.raises(RateLimitError):
            c.list_categories()
        assert route.call_count == 3
        assert waits == [0.5, 1.0]

    @respx.mock
    def test_429_then_success(self) -> None:
        auth_route()
        route = respx.get(f"{BASE}/categories")
        route.side_effect = [Response(429), Response(200, json=fixture("pluggy_categories.json"))]
        with client() as c:
            assert len(c.list_categories()) == 3

    @respx.mock
    def test_a_connection_failure_is_a_network_error(self) -> None:
        auth_route()
        respx.get(f"{BASE}/categories").mock(side_effect=httpx.ConnectError("sem rede"))
        with client() as c, pytest.raises(NetworkError, match="pluggy"):
            c.list_categories()

    @respx.mock
    def test_a_body_that_is_not_json(self) -> None:
        auth_route()
        respx.get(f"{BASE}/categories").mock(return_value=Response(502, text="<html>bad gateway</html>"))
        with client() as c, pytest.raises(PluggyError, match=r"resposta inválida da Pluggy \(HTTP 502\)"):
            c.list_categories()

    @respx.mock
    def test_a_body_without_results(self) -> None:
        auth_route()
        respx.get(f"{BASE}/categories").mock(return_value=Response(200, json={"oops": True}))
        with client() as c, pytest.raises(PluggyError, match="resposta inesperada"):
            c.list_categories()
