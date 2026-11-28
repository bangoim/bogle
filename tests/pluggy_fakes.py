"""A fake Pluggy in memory for the spending tests (issue #114).

``FakePluggy`` answers like :class:`~bogle.data.pluggy.PluggyClient` and records
every call. The records are built from JSON bodies shaped like the API's and go
through the real parsers, so the sync sees exactly what the client would hand
it. Every id, name and number here is made up.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from bogle.data.pluggy import (
    PluggyAccount,
    PluggyBill,
    PluggyCategory,
    PluggyItem,
    PluggyTransaction,
    parse_account,
    parse_bill,
    parse_category,
    parse_item,
    parse_transaction,
)
from bogle.domain.errors import BogleError

NOW = datetime(2026, 11, 27, 11, 0, tzinfo=UTC)
"""08:00 in Sao Paulo."""

ITEM = "11111111-1111-4111-8111-111111111111"
ITEM_NEW = "22222222-2222-4222-8222-222222222222"
ITEM_OTHER = "33333333-3333-4333-8333-333333333333"

ENV = {"PLUGGY_CLIENT_ID": "client-id", "PLUGGY_CLIENT_SECRET": "client-secret", "PLUGGY_ITEM_IDS": ITEM}


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def item(
    item_id: str = ITEM,
    *,
    status: str = "UPDATED",
    consent_expires_at: datetime | None = NOW + timedelta(days=300),
    last_updated_at: datetime | None = NOW - timedelta(hours=5),
    connector: str = "MeuPluggy",
    error: dict[str, Any] | None = None,
) -> PluggyItem:
    return parse_item(
        {
            "id": item_id,
            "connector": {"id": 200, "name": connector},
            "status": status,
            "executionStatus": "SUCCESS" if status == "UPDATED" else "ERROR",
            "error": error,
            "createdAt": iso(NOW - timedelta(days=60)),
            "lastUpdatedAt": iso(last_updated_at) if last_updated_at else None,
            "consentExpiresAt": iso(consent_expires_at) if consent_expires_at else None,
        }
    )


def account(
    account_id: str,
    item_id: str = ITEM,
    *,
    kind: str = "CREDIT",
    number: str | None = "9999",
    name: str = "Cartão",
    tax_number: str | None = "000.000.000-00",
    close_date: date | None = None,
    due_date: date | None = None,
) -> PluggyAccount:
    """An account; a card's ``close_date`` and ``due_date`` are ``balanceCloseDate`` and ``balanceDueDate``."""
    body: dict[str, Any] = {
        "id": account_id,
        "itemId": item_id,
        "type": kind,
        "subtype": "CREDIT_CARD" if kind == "CREDIT" else "CHECKING_ACCOUNT",
        "number": number,
        "name": name,
        "balance": 100,
        "currencyCode": "BRL",
        "taxNumber": tax_number,
    }
    if kind == "CREDIT":
        body["creditData"] = {"brand": "MASTERCARD", "level": "GOLD", "creditLimit": 5000}
        if close_date is not None:
            body["creditData"]["balanceCloseDate"] = close_date.isoformat()
        if due_date is not None:
            body["creditData"]["balanceDueDate"] = due_date.isoformat()
    else:
        body["bankData"] = {"transferNumber": f"0001/{number}"}
    return parse_account(body)


def tx(
    tx_id: str,
    account_id: str,
    day: date,
    amount: str = "10",
    *,
    status: str = "POSTED",
    provider_id: str | None = None,
    description: str = "Compra",
    bill_id: str | None = None,
    at: str = "00:00:00.000",
    installment: int | None = None,
    post_date: date | None = None,
    kind: str = "DEBIT",
    category: tuple[str, str] = ("07000000", "Shopping"),
    operation: str | None = None,
    payment_data: dict[str, Any] | None = None,
    currency: str = "BRL",
    in_account_currency: str | None = None,
    merchant_cnpj: str | None = None,
    business_name: str | None = None,
    installments: int = 10,
    purchase_date: date | None = None,
    purchase_total: str | None = None,
) -> PluggyTransaction:
    """A line on ``day``; ``at`` is the UTC time, milliseconds included.

    A purchase abroad comes in ``currency``, with its value in reais in
    ``in_account_currency`` (``amountInAccountCurrency``). ``business_name`` is
    the merchant's ``businessName``. An ``installment`` is the number of one of
    ``installments``; ``purchase_date`` and ``purchase_total`` are the card's
    ``purchaseDate`` and ``totalAmount`` (the whole purchase).

    The real formats: the bank account sends instants (``14:32:07.512``); the
    card sends ``03:00:00.000`` (midnight in Sao Paulo), ``00:00:00.001`` and
    ``12:00:00.001``, with ``billPostDate`` (``post_date``) on every line.
    """
    card: dict[str, Any] = {}
    if bill_id is not None:
        card["billId"] = bill_id
    if installment is not None:
        card |= {"paymentType": "INSTALLMENT", "installmentNumber": installment, "totalInstallments": installments}
    if purchase_date is not None:
        card["purchaseDate"] = f"{purchase_date.isoformat()}T03:00:00.000Z"
    if purchase_total is not None:
        card["totalAmount"] = float(Decimal(purchase_total))
    if post_date is not None:
        card["billPostDate"] = post_date.isoformat()
    merchant: dict[str, Any] | None = None
    if merchant_cnpj or business_name:
        merchant = {"name": "Loja", "cnpj": merchant_cnpj, "businessName": business_name}
    return parse_transaction(
        {
            "id": tx_id,
            "accountId": account_id,
            "date": f"{day.isoformat()}T{at}Z",
            "description": description,
            "amount": float(Decimal(amount)),
            "currencyCode": currency,
            "amountInAccountCurrency": float(Decimal(in_account_currency)) if in_account_currency else None,
            "type": kind,
            "status": status,
            "providerId": provider_id,
            "category": category[1],
            "categoryId": category[0],
            "operationType": operation,
            "paymentData": payment_data,
            "merchant": merchant,
            "creditCardMetadata": card or None,
            "createdAt": iso(NOW),
            "updatedAt": iso(NOW),
        }
    )


def bill(
    bill_id: str,
    due: date,
    total: str = "100",
    *,
    payments: Sequence[tuple[date, str]] = (),
    closing: date | None = None,
    charges: Sequence[tuple[str, str]] = (),
) -> PluggyBill:
    """A bill; ``payments`` are ``(day, amount)``, shaped like Nubank's (``DEBIT_ACCOUNT``);
    ``closing`` is ``billClosingDate``; ``charges`` are ``financeCharges``, ``(type, amount)``."""
    return parse_bill(
        {
            "id": bill_id,
            "dueDate": f"{due.isoformat()}T00:00:00.000Z",
            "billClosingDate": f"{closing.isoformat()}T00:00:00.000Z" if closing is not None else None,
            "totalAmount": float(Decimal(total)),
            "totalAmountCurrencyCode": "BRL",
            "financeCharges": [
                {"id": f"{bill_id}-fee-{n}", "type": kind, "amount": float(Decimal(amount)), "currencyCode": "BRL"}
                for n, (kind, amount) in enumerate(charges)
            ],
            "payments": [
                {
                    "id": f"{bill_id}-pay-{n}",
                    "valueType": "FULL_PAYMENT",
                    "paymentDate": f"{day.isoformat()}T00:00:00.000Z",
                    "paymentMode": "DEBIT_ACCOUNT",
                    "amount": amount,
                    "currencyCode": "BRL",
                }
                for n, (day, amount) in enumerate(payments)
            ],
        }
    )


CATEGORIES = [parse_category({"id": "07000000", "description": "Shopping", "descriptionTranslated": "Compras"})]

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def category_tree() -> list[PluggyCategory]:
    """Pluggy's whole tree (GET /categories, 130 categories), as published; public data, no one's."""
    body = json.loads((FIXTURES / "pluggy_categories_tree.json").read_text())
    return [parse_category(entry) for entry in body["results"]]


class FakePluggy:
    """The API in memory. ``fail`` maps a call (``"list_transactions:<account>"``,
    ``"get_item:<item>"``, ``"list_categories"``) to the error it raises."""

    def __init__(self) -> None:
        self.items: dict[str, PluggyItem] = {}
        self.accounts: dict[str, list[PluggyAccount]] = {}
        self.transactions: dict[str, list[PluggyTransaction]] = {}
        self.bills: dict[str, list[PluggyBill]] = {}
        self.categories: list[PluggyCategory] = list(CATEGORIES)
        self.fail: dict[str, BogleError] = {}
        self.calls: list[tuple[Any, ...]] = []
        self.closed = 0

    # --- montagem ----------------------------------------------------------

    def add_item(self, state: PluggyItem, *accounts: PluggyAccount) -> None:
        self.items[state.id] = state
        self.accounts[state.id] = list(accounts)
        for entry in accounts:
            self.transactions.setdefault(entry.id, [])
            self.bills.setdefault(entry.id, [])

    def set_transactions(self, account_id: str, *rows: PluggyTransaction) -> None:
        self.transactions[account_id] = list(rows)

    def set_bills(self, account_id: str, *rows: PluggyBill) -> None:
        self.bills[account_id] = list(rows)

    def _check(self, key: str) -> None:
        error = self.fail.get(key)
        if error is not None:
            raise error

    # --- API ---------------------------------------------------------------

    def get_item(self, item_id: str) -> PluggyItem:
        self.calls.append(("get_item", item_id))
        self._check(f"get_item:{item_id}")
        return self.items[item_id]

    def list_accounts(self, item_id: str) -> list[PluggyAccount]:
        self.calls.append(("list_accounts", item_id))
        self._check(f"list_accounts:{item_id}")
        return list(self.accounts.get(item_id, []))

    def list_transactions(
        self, account_id: str, *, date_from: date | None = None, date_to: date | None = None
    ) -> list[PluggyTransaction]:
        self.calls.append(("list_transactions", account_id, date_from, date_to))
        self._check(f"list_transactions:{account_id}")
        rows = self.transactions.get(account_id, [])
        return [
            row
            for row in rows
            if (date_from is None or row.local_date >= date_from) and (date_to is None or row.local_date <= date_to)
        ]

    def list_bills(self, account_id: str) -> list[PluggyBill]:
        self.calls.append(("list_bills", account_id))
        self._check(f"list_bills:{account_id}")
        return list(self.bills.get(account_id, []))

    def list_categories(self) -> list[PluggyCategory]:
        self.calls.append(("list_categories",))
        self._check("list_categories")
        return list(self.categories)

    def close(self) -> None:
        self.closed += 1

    # --- consultas dos testes ----------------------------------------------

    def requests(self, name: str) -> list[tuple[Any, ...]]:
        return [call for call in self.calls if call[0] == name]

    def factory(self) -> Callable[[str, str], FakePluggy]:
        """A ``client_factory`` that hands out this fake and records the credentials."""

        def make(client_id: str, client_secret: str) -> FakePluggy:
            self.calls.append(("factory", client_id))
            return self

        return make


def no_client(client_id: str, client_secret: str) -> FakePluggy:
    """A ``client_factory`` that must never be called."""
    raise AssertionError("não deveria montar um cliente da Pluggy")
