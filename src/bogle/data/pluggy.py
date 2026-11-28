"""Client for the Pluggy API, the source of the Meu Pluggy connections (issue #114).

Shared by everything that reads a Pluggy item (spending now, investments in
#111), so nothing here knows what the data is for: it authenticates, pages and
parses, and the callers decide what to keep.

Wire format (OpenAPI of the Pluggy API, confirmed live 2026-11-27):

- ``POST /auth {clientId, clientSecret}`` answers ``{apiKey}``, valid for 2 h,
  sent back as ``X-API-KEY``. Bad credentials are 401 with a ``codeDescription``
  (``CLIENT_KEYS_UNAUTHORIZED``, ``CLIENT_SECRET_EXPIRED``, ``CLIENT_DISABLED``);
  a missing or invalid API key on any other endpoint is **403**
  ``API_KEY_MISSING_OR_INVALID``.
- ``/accounts``, ``/bills`` and ``/categories`` page with ``page`` and answer
  ``{page, total, totalPages, results}``. ``pageSize`` is never sent: the
  default (500, the maximum) covers these volumes, and ``/v2/transactions``
  answers an empty page with 200 when it gets ``pageSize=500`` (seen live
  2026-11-27), so the parameter is kept out of every route.
- ``/v2/transactions`` pages with a cursor: ``{results, next}``, where ``next`` is
  a ready query string (``?accountId=...&after=...``) or ``null``. Only the
  decoded ``after`` is sent back, next to the same filters.
- Errors come as ``{code, message, codeDescription}``; an unknown item is 404
  ``ITEM_NOT_FOUND``, while ``/accounts`` of an unknown item is 200 and empty.

Numbers are parsed straight to ``Decimal``; every parsed record keeps the JSON it
came from in ``raw``. The credentials never appear in a message.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from datetime import time as clock_time
from decimal import Decimal
from typing import Any, NoReturn
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

import httpx

from bogle.domain.errors import NetworkError, PluggyAuthError, PluggyError, PluggyNotConfiguredError, RateLimitError

PROVIDER = "pluggy"
BASE_URL = "https://api.pluggy.ai"

CLIENT_ID_ENV = "PLUGGY_CLIENT_ID"
CLIENT_SECRET_ENV = "PLUGGY_CLIENT_SECRET"
ITEM_IDS_ENV = "PLUGGY_ITEM_IDS"
CONFIG_VARS = (CLIENT_ID_ENV, CLIENT_SECRET_ENV, ITEM_IDS_ENV)

MAX_ITEMS = 5
"""Connections the Meu Pluggy demo application takes."""

API_KEY_TTL = 115 * 60.0
"""Seconds an API key is reused: Pluggy gives 2 h, renewed five minutes early."""

MAX_PAGES = 1000
"""Guard against a server that keeps answering with another page."""

_DEFAULT_TIMEOUT = 30.0
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_BACKOFF_BASE = 0.5

_LOCAL_TZ = ZoneInfo("America/Sao_Paulo")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_UNRESOLVED_REFERENCE = "op://"


# ------------------------------------------------------------- configuracao


@dataclass(frozen=True, slots=True)
class PluggyConfig:
    client_id: str
    client_secret: str = field(repr=False)
    item_ids: tuple[str, ...]


def _value(env: Mapping[str, str], name: str) -> str:
    return (env.get(name) or "").strip()


def missing_config(env: Mapping[str, str]) -> list[str]:
    """The variables absent from ``env``; an empty value counts as absent."""
    return [name for name in CONFIG_VARS if not _value(env, name)]


def any_config(env: Mapping[str, str]) -> bool:
    """Whether the user started configuring Pluggy at all (one variable is enough)."""
    return len(missing_config(env)) < len(CONFIG_VARS)


def load_config(env: Mapping[str, str] | None = None) -> PluggyConfig:
    """Credentials and item ids from the environment, checked.

    A value still holding ``op://`` is a 1Password reference nobody resolved:
    the command ran without ``op run``. Item ids are UUIDs, at most
    :data:`MAX_ITEMS`, deduplicated in the order given.
    """
    env = os.environ if env is None else env
    missing = missing_config(env)
    if missing:
        verb = "falta" if len(missing) == 1 else "faltam"
        raise PluggyNotConfiguredError(
            f"{verb} {', '.join(missing)} no ambiente (defina ou rode com op run).", missing=tuple(missing)
        )
    for name in CONFIG_VARS:
        if _value(env, name).startswith(_UNRESOLVED_REFERENCE):
            raise PluggyNotConfiguredError(f"{name} é uma referência do 1Password não resolvida: rode com op run.")
    item_ids: list[str] = []
    for part in _value(env, ITEM_IDS_ENV).split(","):
        item_id = part.strip().lower()
        if not item_id:
            continue
        if not _UUID.fullmatch(item_id):
            raise PluggyNotConfiguredError(f"{ITEM_IDS_ENV} tem um id inválido: '{part.strip()}'.")
        if item_id not in item_ids:
            item_ids.append(item_id)
    if not item_ids:
        raise PluggyNotConfiguredError(f"falta {ITEM_IDS_ENV} no ambiente (defina ou rode com op run).")
    if len(item_ids) > MAX_ITEMS:
        raise PluggyNotConfiguredError(f"{ITEM_IDS_ENV} aceita até {MAX_ITEMS} conexões, recebeu {len(item_ids)}.")
    return PluggyConfig(
        client_id=_value(env, CLIENT_ID_ENV),
        client_secret=_value(env, CLIENT_SECRET_ENV),
        item_ids=tuple(item_ids),
    )


# ------------------------------------------------------------------- modelos


@dataclass(frozen=True, slots=True)
class PluggyItem:
    id: str
    status: str
    execution_status: str | None
    connector_id: int | None
    connector_name: str | None
    error_code: str | None
    error_message: str | None
    created_at: datetime | None
    last_updated_at: datetime | None
    consent_expires_at: datetime | None
    raw: dict[str, Any] = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class PluggyAccount:
    id: str
    item_id: str
    type: str
    subtype: str
    number: str | None
    name: str
    marketing_name: str | None
    owner: str | None
    tax_number: str | None
    currency_code: str
    balance: Decimal | None
    transfer_number: str | None
    credit_brand: str | None
    credit_level: str | None
    credit_limit: Decimal | None
    available_credit_limit: Decimal | None
    minimum_payment: Decimal | None
    balance_close_date: date | None
    balance_due_date: date | None
    raw: dict[str, Any] = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class PluggyTransaction:
    id: str
    account_id: str
    date: datetime
    description: str
    description_raw: str | None
    amount: Decimal
    currency_code: str
    amount_in_account_currency: Decimal | None
    balance: Decimal | None
    type: str
    status: str
    provider_id: str | None
    provider_code: str | None
    category_id: str | None
    category: str | None
    operation_type: str | None
    merchant_name: str | None
    merchant_business_name: str | None
    merchant_cnpj: str | None
    merchant_cnae: str | None
    merchant_category: str | None
    payment_data: dict[str, Any] | None
    bill_id: str | None
    bill_post_date: date | None
    bill_forecast_month: str | None
    payment_type: str | None
    installment_number: int | None
    total_installments: int | None
    installments_total_amount: Decimal | None
    purchase_date: date | None
    payee_mcc: str | None
    card_number: str | None
    created_at: datetime | None
    updated_at: datetime | None
    raw: dict[str, Any] = field(repr=False, compare=False)

    @property
    def local_date(self) -> date:
        """The day the statement shows: the card's ``billPostDate`` when there is one.

        On the card it came in every line (235 of 235, seen live 2026-11-27) and
        is always the right day, where ``date`` mixes midnight in Sao Paulo,
        midnight UTC and noon UTC.
        """
        return self.bill_post_date if self.bill_post_date is not None else local_day(self.date)


@dataclass(frozen=True, slots=True)
class PluggyBill:
    id: str
    due_date: date
    closing_date: date | None
    total_amount: Decimal
    currency_code: str
    minimum_payment_amount: Decimal | None
    allows_installments: bool | None
    finance_charges: list[Any]
    payments: list[Any]
    raw: dict[str, Any] = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class PluggyCategory:
    id: str
    description: str
    description_translated: str | None
    parent_id: str | None
    parent_description: str | None


# ------------------------------------------------------------------- parsing


def local_day(moment: datetime) -> date:
    """The calendar day of a Pluggy timestamp, as the statement shows it.

    Pluggy sends ``date`` in UTC. A value at midnight UTC, milliseconds aside
    (``00:00:00.000Z``, and the ``00:00:00.001Z`` of the card's IOF refunds), is
    a date without a time: converting it to Sao Paulo would move it to the day
    before. Anything else is a real instant (the bank account's lines carry the
    time to the millisecond; most card lines come at ``03:00:00.000Z``, which is
    midnight in Sao Paulo), and its day is the one in Sao Paulo. A card line
    prefers ``billPostDate`` over this (:attr:`PluggyTransaction.local_date`).
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    utc = moment.astimezone(UTC)
    if utc.replace(microsecond=0).timetz().replace(tzinfo=None) == clock_time(0):
        return utc.date()
    return moment.astimezone(_LOCAL_TZ).date()


def _str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _dec(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _int(value: Any) -> int | None:
    return None if value is None else int(value)


def _datetime(value: Any) -> datetime | None:
    text = _str(value)
    if text is None:
        return None
    moment = datetime.fromisoformat(text)
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _day(value: Any) -> date | None:
    moment = _datetime(value)
    return None if moment is None else local_day(moment)


def _mcc(value: Any) -> str | None:
    # A doc diz inteiro; um MCC tem quatro digitos e o zero a esquerda conta.
    if value is None:
        return None
    if isinstance(value, int | Decimal):
        return f"{int(value):04d}"
    return _str(value)


def parse_item(body: Mapping[str, Any]) -> PluggyItem:
    connector = body.get("connector") or {}
    error = body.get("error") or {}
    return PluggyItem(
        id=str(body["id"]),
        status=str(body.get("status") or ""),
        execution_status=_str(body.get("executionStatus")),
        connector_id=_int(connector.get("id")),
        connector_name=_str(connector.get("name")),
        error_code=_str(error.get("code")),
        error_message=_str(error.get("message")),
        created_at=_datetime(body.get("createdAt")),
        last_updated_at=_datetime(body.get("lastUpdatedAt")),
        consent_expires_at=_datetime(body.get("consentExpiresAt")),
        raw=dict(body),
    )


def parse_account(body: Mapping[str, Any]) -> PluggyAccount:
    bank = body.get("bankData") or {}
    credit = body.get("creditData") or {}
    return PluggyAccount(
        id=str(body["id"]),
        item_id=str(body.get("itemId") or ""),
        type=str(body["type"]),
        subtype=str(body.get("subtype") or ""),
        number=_str(body.get("number")),
        name=str(body.get("name") or ""),
        marketing_name=_str(body.get("marketingName")),
        owner=_str(body.get("owner")),
        tax_number=_str(body.get("taxNumber")),
        currency_code=str(body.get("currencyCode") or "BRL"),
        balance=_dec(body.get("balance")),
        transfer_number=_str(bank.get("transferNumber")),
        credit_brand=_str(credit.get("brand")),
        credit_level=_str(credit.get("level")),
        credit_limit=_dec(credit.get("creditLimit")),
        available_credit_limit=_dec(credit.get("availableCreditLimit")),
        minimum_payment=_dec(credit.get("minimumPayment")),
        balance_close_date=_day(credit.get("balanceCloseDate")),
        balance_due_date=_day(credit.get("balanceDueDate")),
        raw=dict(body),
    )


def parse_transaction(body: Mapping[str, Any]) -> PluggyTransaction:
    merchant = body.get("merchant") or {}
    card = body.get("creditCardMetadata") or {}
    moment = _datetime(body.get("date"))
    kind = _str(body.get("type"))
    if moment is None or kind is None:
        raise PluggyError(f"transação {body.get('id')} da Pluggy sem data ou sem tipo.")
    return PluggyTransaction(
        id=str(body["id"]),
        account_id=str(body.get("accountId") or ""),
        date=moment,
        description=str(body.get("description") or ""),
        description_raw=_str(body.get("descriptionRaw")),
        amount=Decimal(str(body["amount"])),
        currency_code=str(body.get("currencyCode") or "BRL"),
        amount_in_account_currency=_dec(body.get("amountInAccountCurrency")),
        balance=_dec(body.get("balance")),
        type=kind,
        status=str(body.get("status") or "POSTED"),
        provider_id=_str(body.get("providerId")),
        provider_code=_str(body.get("providerCode")),
        category_id=_str(body.get("categoryId")),
        category=_str(body.get("category")),
        operation_type=_str(body.get("operationType")),
        merchant_name=_str(merchant.get("name")),
        merchant_business_name=_str(merchant.get("businessName")),
        merchant_cnpj=_str(merchant.get("cnpj")),
        merchant_cnae=_str(merchant.get("cnae")),
        merchant_category=_str(merchant.get("category")),
        payment_data=body.get("paymentData") or None,
        bill_id=_str(card.get("billId")),
        bill_post_date=_day(card.get("billPostDate")),
        bill_forecast_month=_str(card.get("billForecastDate")),
        payment_type=_str(card.get("paymentType")),
        installment_number=_int(card.get("installmentNumber")),
        total_installments=_int(card.get("totalInstallments")),
        installments_total_amount=_dec(card.get("totalAmount")),
        purchase_date=_day(card.get("purchaseDate")),
        payee_mcc=_mcc(card.get("payeeMCC")),
        card_number=_str(card.get("cardNumber")),
        created_at=_datetime(body.get("createdAt")),
        updated_at=_datetime(body.get("updatedAt")),
        raw=dict(body),
    )


def parse_bill(body: Mapping[str, Any]) -> PluggyBill:
    due = _day(body.get("dueDate"))
    if due is None:
        raise PluggyError(f"fatura {body.get('id')} da Pluggy sem vencimento.")
    return PluggyBill(
        id=str(body["id"]),
        due_date=due,
        closing_date=_day(body.get("billClosingDate")),
        total_amount=Decimal(str(body["totalAmount"])),
        currency_code=str(body.get("totalAmountCurrencyCode") or "BRL"),
        minimum_payment_amount=_dec(body.get("minimumPaymentAmount")),
        allows_installments=body.get("allowsInstallments"),
        finance_charges=list(body.get("financeCharges") or []),
        payments=list(body.get("payments") or []),
        raw=dict(body),
    )


def parse_category(body: Mapping[str, Any]) -> PluggyCategory:
    return PluggyCategory(
        id=str(body["id"]),
        description=str(body.get("description") or ""),
        description_translated=_str(body.get("descriptionTranslated")),
        parent_id=_str(body.get("parentId")),
        parent_description=_str(body.get("parentDescription")),
    )


def dumps(value: Any) -> str:
    """JSON for a ``raw`` payload: the ``Decimal`` the parser made go back as numbers."""
    return json.dumps(value, default=float, ensure_ascii=False)


# -------------------------------------------------------------------- cliente


def _after_of(next_query: str) -> str:
    """The decoded ``after`` of a ``next`` query string (never the whole string)."""
    query = next_query.split("?", 1)[1] if "?" in next_query else next_query
    values = parse_qs(query).get("after")
    if not values or not values[0]:
        raise PluggyError("a Pluggy devolveu uma próxima página sem cursor.")
    return values[0]


class PluggyClient:
    """Thin, synchronous client over ``httpx``, like :class:`~bogle.data.brapi.BrapiClient`.

    The API key is fetched on the first request and reused for
    :data:`API_KEY_TTL`; a 401 or 403 on a read renews it and repeats the read
    once. ``clock`` measures the key's age and ``sleep`` the 429 backoff, both
    injectable so tests neither wait nor depend on the wall clock.
    """

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        *,
        base_url: str = BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        backoff_base: float = _DEFAULT_BACKOFF_BASE,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client_id = client_id if client_id is not None else _value(os.environ, CLIENT_ID_ENV)
        self._client_secret = client_secret if client_secret is not None else _value(os.environ, CLIENT_SECRET_ENV)
        self._max_retries = max(1, max_retries)
        self._backoff_base = backoff_base
        self._sleep = sleep
        self._clock = clock
        self._api_key: str | None = None
        self._key_issued_at = 0.0
        self._owns_client = client is None
        self._client = client if client is not None else httpx.Client(base_url=base_url, timeout=timeout)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> PluggyClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # --- API publica ------------------------------------------------------

    def get_item(self, item_id: str) -> PluggyItem:
        try:
            body = self._get(f"/items/{item_id}")
        except PluggyError as exc:
            if exc.code == "ITEM_NOT_FOUND":
                raise PluggyError(
                    f"conexão {item_id} não encontrada na Pluggy: confira {ITEM_IDS_ENV}.", code=exc.code
                ) from None
            raise
        return parse_item(body)

    def list_accounts(self, item_id: str) -> list[PluggyAccount]:
        return [parse_account(row) for row in self._paged("/accounts", {"itemId": item_id})]

    def list_transactions(
        self, account_id: str, *, date_from: date | None = None, date_to: date | None = None
    ) -> list[PluggyTransaction]:
        """Every transaction of the account in the range, following the cursor to the end.

        No range is the whole history Pluggy keeps, future installments included.
        """
        params = {"accountId": account_id}
        if date_from is not None:
            params["dateFrom"] = date_from.isoformat()
        if date_to is not None:
            params["dateTo"] = date_to.isoformat()
        return [parse_transaction(row) for row in self._cursor("/v2/transactions", params)]

    def list_bills(self, account_id: str) -> list[PluggyBill]:
        return [parse_bill(row) for row in self._paged("/bills", {"accountId": account_id})]

    def list_categories(self) -> list[PluggyCategory]:
        return [parse_category(row) for row in self._paged("/categories", {})]

    # --- paginacao --------------------------------------------------------

    def _paged(self, path: str, params: dict[str, str]) -> Iterator[dict[str, Any]]:
        page = 1
        while True:
            # Sem pageSize, de proposito: ver o docstring do modulo.
            body = self._get(path, {**params, "page": str(page)})
            yield from self._results(body, path)
            total_pages = int(body.get("totalPages") or 1)
            if page >= total_pages:
                return
            page += 1
            if page > MAX_PAGES:
                raise PluggyError(f"a Pluggy devolveu páginas demais em {path}.")

    def _cursor(self, path: str, params: dict[str, str]) -> Iterator[dict[str, Any]]:
        seen: set[str] = set()
        request = dict(params)
        for _page in range(MAX_PAGES):
            body = self._get(path, request)
            yield from self._results(body, path)
            next_query = body.get("next")
            if not next_query:
                return
            after = _after_of(str(next_query))
            if after in seen:
                raise PluggyError(f"a Pluggy repetiu o cursor em {path}.")
            seen.add(after)
            request = {**params, "after": after}
        raise PluggyError(f"a Pluggy devolveu páginas demais em {path}.")

    @staticmethod
    def _results(body: Any, path: str) -> list[dict[str, Any]]:
        results = body.get("results") if isinstance(body, dict) else None
        if not isinstance(results, list):
            raise PluggyError(f"resposta inesperada da Pluggy em {path}.")
        return results

    # --- HTTP -------------------------------------------------------------

    def _key(self) -> str:
        if self._api_key is None or self._clock() - self._key_issued_at >= API_KEY_TTL:
            self._authenticate()
        assert self._api_key is not None  # _authenticate grava ou levanta
        return self._api_key

    def _authenticate(self) -> None:
        missing = [
            name
            for name, value in ((CLIENT_ID_ENV, self._client_id), (CLIENT_SECRET_ENV, self._client_secret))
            if not value
        ]
        if missing:
            verb = "falta" if len(missing) == 1 else "faltam"
            raise PluggyNotConfiguredError(
                f"{verb} {', '.join(missing)} no ambiente (defina ou rode com op run).", missing=tuple(missing)
            )
        payload = {"clientId": self._client_id, "clientSecret": self._client_secret}
        resp = self._send("POST", "/auth", json_body=payload)
        body = self._decode(resp)
        if resp.status_code in (401, 403):
            raise PluggyAuthError(_auth_message(body, resp.status_code), code=_code(body))
        if resp.status_code != 200:
            self._raise(resp, body)
        key = body.get("apiKey") if isinstance(body, dict) else None
        if not key:
            raise PluggyAuthError("a Pluggy não devolveu a chave de API.")
        self._api_key = str(key)
        self._key_issued_at = self._clock()

    def _get(self, path: str, params: dict[str, str] | None = None) -> Any:
        for attempt in range(2):
            resp = self._send("GET", path, params=params, headers={"X-API-KEY": self._key()})
            if resp.status_code in (401, 403):
                self._api_key = None
                if attempt == 0:
                    continue
                body = self._decode(resp)
                raise PluggyAuthError(f"a Pluggy recusou a chave de API (HTTP {resp.status_code}).", code=_code(body))
            body = self._decode(resp)
            if resp.status_code != 200:
                self._raise(resp, body)
            return body
        raise AssertionError("inalcancavel: o laco devolve ou levanta")  # pragma: no cover

    def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        json_body: dict[str, str] | None = None,
    ) -> httpx.Response:
        resp: httpx.Response | None = None
        for attempt in range(self._max_retries):
            try:
                resp = self._client.request(method, path, params=params, headers=headers, json=json_body)
            except httpx.HTTPError as exc:
                raise NetworkError(PROVIDER, str(exc) or type(exc).__name__) from None
            if resp.status_code == 429 and attempt < self._max_retries - 1:
                self._sleep(self._backoff_base * (2**attempt))
                continue
            break
        assert resp is not None  # o laco roda ao menos uma vez (max_retries >= 1)
        if resp.status_code == 429:
            raise RateLimitError(PROVIDER)
        return resp

    @staticmethod
    def _decode(resp: httpx.Response) -> Any:
        try:
            return json.loads(resp.text, parse_float=Decimal)
        except json.JSONDecodeError:
            raise PluggyError(f"resposta inválida da Pluggy (HTTP {resp.status_code}).") from None

    @staticmethod
    def _raise(resp: httpx.Response, body: Any) -> NoReturn:
        message = str(body.get("message") or "") if isinstance(body, dict) else ""
        code = _code(body)
        detail = message or code or "sem detalhe"
        raise PluggyError(f"erro da Pluggy: {detail} (HTTP {resp.status_code}).", code=code)


def _code(body: Any) -> str:
    return str(body.get("codeDescription") or "") if isinstance(body, dict) else ""


_AUTH_MESSAGES = {
    "CLIENT_SECRET_EXPIRED": (
        f"o client secret da Pluggy venceu: gere outro no Dashboard e atualize {CLIENT_SECRET_ENV}."
    ),
    "CLIENT_KEYS_UNAUTHORIZED": f"a Pluggy recusou as credenciais: confira {CLIENT_ID_ENV} e {CLIENT_SECRET_ENV}.",
    "CLIENT_DISABLED": "a aplicação da Pluggy está desativada.",
}


def _auth_message(body: Any, status: int) -> str:
    known = _AUTH_MESSAGES.get(_code(body))
    if known is not None:
        return known
    message = str(body.get("message") or "") if isinstance(body, dict) else ""
    return f"a Pluggy recusou a autenticação: {message or f'HTTP {status}'}."
