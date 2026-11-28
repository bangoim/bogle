"""What the spending sync reads back from the database and reports (issue #114)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class SyncWindow:
    """The logical range of a sync: what it asks for and the only days it may prune.

    Both ends open is the whole history (and every future installment). The
    request to Pluggy is wider by :data:`~bogle.spending.matching.REQUEST_MARGIN`
    on each closed end; the pruning of PENDING lines never is.
    """

    date_from: date | None = None
    date_to: date | None = None

    @property
    def full(self) -> bool:
        return self.date_from is None and self.date_to is None

    def contains(self, day: date) -> bool:
        if self.date_from is not None and day < self.date_from:
            return False
        return self.date_to is None or day <= self.date_to


@dataclass(frozen=True, slots=True)
class StoredAccount:
    id: int
    pluggy_id: str
    replaced_pluggy_ids: tuple[str, ...]
    item_id: str | None
    type: str
    subtype: str
    number: str | None
    name: str
    marketing_name: str | None
    tax_number: str | None
    transfer_number: str | None
    credit_brand: str | None
    last_synced_at: datetime | None
    first_transaction_date: date | None
    last_transaction_date: date | None

    @property
    def label(self) -> str:
        return account_label(self.name, self.marketing_name)


def account_label(name: str, marketing_name: str | None) -> str:
    """How an account is named on screen: the bank's commercial name when it has one."""
    return marketing_name or name


@dataclass(frozen=True, slots=True)
class StoredTransaction:
    id: int
    pluggy_id: str
    provider_id: str | None
    local_date: date
    amount: Decimal
    status: str
    description: str
    moment: datetime
    """Pluggy's ``date``, the exact instant: part of the natural key."""
    installment_number: int | None


@dataclass(frozen=True, slots=True)
class AccountReport:
    """One account after a sync that applied: the counts and where it stands."""

    label: str
    inserted: int = 0
    updated: int = 0
    remapped: int = 0
    """Stored transactions that took a new Pluggy id (by providerId or by natural key)."""
    removed_pending: int = 0
    suggested: int = 0
    """New transactions held back as probable duplicates."""
    inherited: bool = False
    """The account came back under a new id (renewed consent) and kept its history."""
    last_synced_at: datetime | None = None
    last_transaction_date: date | None = None
    first_transaction_date: date | None = None


@dataclass(frozen=True, slots=True)
class SyncFailure:
    """Something the sync could not read: a connection, an account, the categories."""

    subject: str
    message: str


@dataclass(slots=True)
class SyncReport:
    accounts: list[AccountReport] = field(default_factory=list)
    failures: list[SyncFailure] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    """Connections skipped on purpose and ambiguous renewals: terse, one line each."""
    pending_duplicates: int = 0
    unmapped_categories: list[str] = field(default_factory=list)
    """Categories in the reserve group only because no group takes them (#116)."""

    @property
    def applied(self) -> bool:
        return bool(self.accounts)


@dataclass(frozen=True, slots=True)
class AutoSync:
    """What the automatic sync did before a command (or ``bogle check``)."""

    ran: bool
    report: SyncReport | None = None
    notice: str | None = None
    """The line for stderr (``aviso: ...``), when there is something to say."""
    problem: str | None = None
    """What failed (not what was skipped on purpose), for ``bogle check``'s errors."""


@dataclass(frozen=True, slots=True)
class ItemState:
    """A connection as last read from Pluggy, named after its accounts."""

    id: str
    label: str
    status: str
    execution_status: str | None
    error_code: str | None
    last_updated_at: datetime | None
    consent_expires_at: datetime | None
    checked_at: datetime


@dataclass(frozen=True, slots=True)
class AccountStatus:
    label: str
    item_id: str | None
    last_synced_at: datetime | None
    last_transaction_date: date | None
    first_transaction_date: date | None


@dataclass(frozen=True, slots=True)
class TransactionKind:
    """A transaction and what it is (#115): the rules' kind, the user's, and the one that counts."""

    id: int
    account: str
    local_date: date
    description: str
    amount: Decimal
    kind: str | None
    auto_kind: str | None
    auto_reason: str | None
    manual_kind: str | None


@dataclass(frozen=True, slots=True)
class Category:
    """A Pluggy category as stored; ``name`` is the Portuguese one when Pluggy sent it, else ``description``."""

    id: str
    name: str
    description: str
    parent_id: str | None = None


class GroupNature(StrEnum):
    """Whether a group's spending repeats every month; the values are what is stored and typed."""

    FIXED = "fixo"
    VARIABLE = "variavel"


@dataclass(frozen=True, slots=True)
class CategoryGroup:
    """A group of categories (#116): ``fixo`` or ``variavel``; ``fallback`` is the reserve group."""

    id: int
    name: str
    nature: str
    fallback: bool = False


@dataclass(frozen=True, slots=True)
class GroupSummary:
    """A group as ``bogle spending groups`` lists it."""

    group: CategoryGroup
    mapped: tuple[str, ...]
    """The categories mapped to it by name (their children follow them)."""
    categories: int
    """How many known categories end up in it, children and unmapped ones included."""


@dataclass(frozen=True, slots=True)
class TransactionCategory:
    """A transaction and its category (#116): Pluggy's, a rule's, the user's, and the one that counts."""

    id: int
    account: str
    local_date: date
    description: str
    amount: Decimal
    category: str | None
    """The name of the category that counts."""
    group: str
    pluggy_category: str | None
    rule_category: str | None
    rule_id: int | None
    manual_category: str | None


@dataclass(frozen=True, slots=True)
class RuleLine:
    """A stored transaction, as much of it as a category rule reads."""

    id: int
    account_id: int
    value: Decimal
    """The absolute amount, in reais."""
    description: str
    documents: tuple[str, ...]
    """The merchant's CNPJ and the other party's document (``paymentData``), digits only, in that order."""
    manual: bool = False
    """It has a category set by hand, which keeps counting over the rule."""
    rule_id: int | None = None
    rule_category_id: str | None = None


class DescriptionMatch(StrEnum):
    """How a rule's text is compared with a description; the values are what is stored and typed."""

    CONTAINS = "contem"
    STARTS = "comeca"
    EQUALS = "igual"
    """The whole description, not a part of it."""


@dataclass(frozen=True, slots=True)
class RuleConditions:
    """What a line must have for a category rule to take it; every condition given must hold (AND)."""

    document: str | None = None
    """Digits only: the merchant's CNPJ or the other party's CPF/CNPJ."""
    description: str | None = None
    match: DescriptionMatch | None = None
    min_value: Decimal | None = None
    max_value: Decimal | None = None
    """Over the absolute value in reais, both ends included."""
    account_id: int | None = None


@dataclass(frozen=True, slots=True)
class StoredRule:
    """A category rule as stored, with the names it is shown with."""

    id: int
    category_id: str
    category: str
    conditions: RuleConditions
    account: str | None
    created_at: datetime
    transactions: int = 0
    """How many lines it categorizes now."""


@dataclass(frozen=True, slots=True)
class RuleApplication:
    """The last application of a rule to the stored lines, as ``rules undo`` would undo it."""

    id: int
    rule_id: int
    applied_at: datetime
    changes: int
