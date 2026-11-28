"""Spending by group and category, and the monthly trend (issue #117).

Everything is read from ``spending.expenses`` (only what counts as spending,
refunds netted, the category that counts and its group as they stand now: a
category moved to another group moves its past months too, with no sync) and,
for the income shown apart, from the lines of kind ``receita``.

The rules:

- A line counts in the month of its ``local_date`` (the day the statement
  shows): an installment counts in its own month, a purchase in ten
  installments weighs a tenth in each. Nothing after ``today`` counts: the
  future installments are #118's projection.
- An account covers the months from the one of its ``first_transaction_date``
  on. A month counts only the accounts that cover it, and a month no account
  covers is left out of the series instead of showing as zero. A covered month
  without spending is zero.
- A month is compared with the ``compare`` months before it, account by
  account: each account's average over the months it is compared in, summed.
  An account is compared only from the month *after* its first transaction's:
  the history rarely starts on the 1st, and a partial first month would drag
  every average down. The entry month still shows in the series and may be
  the month summarized. An amount whose month has a line of an account with no
  month to compare it with has no average (``None``): half a base would make
  the change up.
- The current month, still running, is compared up to the same day of those
  months (the day held to the end of a shorter month): its pace. The pace
  leaves the fixed groups out: rent paid on the 5th or on the 10th says
  nothing of how the month is going.
- The same month a year before takes only the accounts compared in it, and is
  set against the current month of those same accounts.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

import psycopg
from psycopg.rows import DictRow

from bogle.domain.errors import ValidationError
from bogle.spending import repository
from bogle.spending.categories import find_group
from bogle.spending.classify import normalized
from bogle.spending.models import CategoryGroup, ExpenseLine, GroupNature, IncomeLine, StoredAccount

Connection = psycopg.Connection[DictRow]

COMPARE_MONTHS = 3
"""How many months before the one shown its average takes."""

TREND_MONTHS = 12
MAX_MONTHS = 120
"""The most ``--compare`` and ``--months`` take: ten years."""

MIN_YEAR, MAX_YEAR = 1900, 2999
"""The years ``--month`` takes. With up to ``MAX_MONTHS`` around them, every date stays valid."""

TOP_MERCHANTS = 5
NO_CATEGORY = "Sem categoria"

ACCOUNT_TYPES = {"corrente": "BANK", "cartao": "CREDIT"}
"""What ``--account`` takes besides an account's name: every account of the type."""

ZERO = Decimal(0)

_MONTH = re.compile(r"^(\d{4})-(\d{2})$")


# ------------------------------------------------------------------ meses


def month_of(day: date) -> date:
    """The month of ``day``, as its first day: how months are keyed here."""
    return day.replace(day=1)


def add_months(month: date, count: int) -> date:
    index = month.year * 12 + month.month - 1 + count
    return date(index // 12, index % 12 + 1, 1)


def month_end(month: date) -> date:
    return add_months(month, 1) - timedelta(days=1)


def cut_end(month: date, day: int | None) -> date:
    """The last day of ``month`` that counts: ``day`` (held to the month's end), or the whole month."""
    last = month_end(month)
    return last if day is None else month.replace(day=min(day, last.day))


def parse_month(text: str) -> date:
    """``2026-11`` -> ``date(2026, 11, 1)``."""
    found = _MONTH.match(text.strip())
    if found is None or not 1 <= int(found[2]) <= 12:
        raise ValidationError(f"Mês '{text}' inválido. Use AAAA-MM, como 2026-11.")
    if not MIN_YEAR <= int(found[1]) <= MAX_YEAR:
        raise ValidationError(f"Mês '{text}' fora da faixa: o ano vai de {MIN_YEAR} a {MAX_YEAR}.")
    return date(int(found[1]), int(found[2]), 1)


def month_label(month: date) -> str:
    return f"{month:%Y-%m}"


def printable(text: str) -> str:
    # O texto vem da Pluggy: sem caractere de controle (ESC, quebra de linha) no terminal.
    return "".join(char for char in text if char.isprintable())


def change(value: Decimal, base: Decimal | None) -> Decimal | None:
    """How much ``value`` is above (or below) ``base``, as a fraction; ``None`` without a positive base."""
    if base is None or base <= 0:
        return None
    return (value - base) / base


# ---------------------------------------------------------------- contas


def select_accounts(accounts: Sequence[StoredAccount], text: str | None) -> list[StoredAccount]:
    """The accounts ``--account`` names: ``corrente`` or ``cartao`` (every one of the type), else by name.

    The name is the bank's commercial one or Pluggy's, compared without regard
    to case, accents or repeated spaces; every account with that name is taken.
    """
    if text is None:
        return list(accounts)
    key = normalized(text)
    if key in ACCOUNT_TYPES:
        chosen = [account for account in accounts if account.type == ACCOUNT_TYPES[key]]
    else:
        chosen = [account for account in accounts if key in (normalized(account.name), normalized(account.label))]
    if not chosen:
        names = ", ".join(dict.fromkeys(printable(account.label) for account in accounts))
        raise ValidationError(
            f"Conta '{text}' não encontrada. Use {' ou '.join(ACCOUNT_TYPES)}"
            + (f", ou o nome de uma conta: {names}." if names else ".")
        )
    return chosen


@dataclass(frozen=True, slots=True)
class Coverage:
    """The first month each account covers (``None``: no transaction yet, it covers none)."""

    starts: Mapping[int, date | None]
    labels: Mapping[int, str] = field(default_factory=dict)
    """How each account is named on screen."""

    @classmethod
    def of(cls, accounts: Iterable[StoredAccount]) -> Coverage:
        chosen = list(accounts)
        return cls(
            {
                account.id: month_of(account.first_transaction_date) if account.first_transaction_date else None
                for account in chosen
            },
            {account.id: account.label for account in chosen},
        )

    @property
    def first(self) -> date | None:
        """The first month any account covers."""
        return min((start for start in self.starts.values() if start is not None), default=None)

    def covers(self, month: date) -> bool:
        first = self.first
        return first is not None and first <= month

    def counts(self, account_id: int, day: date) -> bool:
        """Whether a line of the account on ``day`` counts: the account covers its month."""
        start = self.starts.get(account_id)
        return start is not None and start <= month_of(day)

    def compares(self, account_id: int, month: date) -> bool:
        """Whether the account is compared in ``month``: from the month after its first transaction's."""
        start = self.starts.get(account_id)
        return start is not None and start < month

    def missing(self, month: date) -> tuple[str, ...]:
        """The accounts whose history starts after ``month`` (an account with none is not listed)."""
        return tuple(
            self.labels.get(account_id, str(account_id))
            for account_id, start in self.starts.items()
            if start is not None and start > month
        )


# ---------------------------------------------------------------- resumo


@dataclass(frozen=True, slots=True)
class CategorySpending:
    category_id: str | None
    name: str
    value: Decimal
    average: Decimal | None
    """``None``: out of the comparison (no month to compare with, or a fixed group in the pace)."""


@dataclass(frozen=True, slots=True)
class GroupSpending:
    group: CategoryGroup
    value: Decimal
    average: Decimal | None
    categories: tuple[CategorySpending, ...]


@dataclass(frozen=True, slots=True)
class MerchantVisits:
    """A merchant of the month: how many purchases, against the average of the compared months."""

    name: str
    count: int
    average: Decimal | None
    value: Decimal
    """The month's net spending there (refunds included)."""


@dataclass(frozen=True, slots=True)
class Pace:
    """The variable spending of the running month up to today, and the average up to the same day."""

    value: Decimal
    average: Decimal


@dataclass(frozen=True, slots=True)
class YearBefore:
    """The same month a year before, of the accounts compared in it, and the current month of those accounts."""

    value: Decimal
    now: Decimal
    missing: tuple[str, ...] = ()
    """The accounts of the month left out: not compared a year before."""


@dataclass(frozen=True, slots=True)
class MonthSummary:
    month: date
    until: date
    """The last day counted: the month's end, or today in the running month."""
    partial: bool
    compared: tuple[date, ...]
    """The months the averages take: up to ``compare`` before this one, those some account is compared in."""
    accounts: tuple[str, ...]
    """The accounts read, when ``--account`` chose some; empty when all of them."""
    groups: tuple[GroupSpending, ...]
    total: Decimal
    total_average: Decimal | None
    """Only for a whole month: the running one is compared by its pace."""
    income: Decimal
    fixed: Decimal
    variable: Decimal
    pace: Pace | None
    fallback: str
    """The reserve group's name."""
    fallback_value: Decimal
    last_year: YearBefore | None
    """The same month a year before, up to the same day; ``None`` when no account is compared in it."""
    merchants: tuple[MerchantVisits, ...]
    shorter: tuple[tuple[str, int], ...] = ()
    """The accounts averaged over fewer months than ``compared``, with how many."""

    @property
    def leftover(self) -> Decimal:
        return self.income - self.total

    @property
    def fallback_share(self) -> Decimal | None:
        """How much of the month's spending is in the reserve group: how much is still unclassified."""
        return self.fallback_value / self.total if self.total > 0 else None


def _total(lines: Iterable[ExpenseLine | IncomeLine]) -> Decimal:
    return sum((line.value for line in lines), ZERO)


def _value(line: ExpenseLine) -> Decimal:
    return line.value


def _purchase(line: ExpenseLine) -> Decimal:
    """One for a purchase, nothing for a refund: how visits to a merchant are counted."""
    return Decimal(1) if line.value > 0 else ZERO


@dataclass(frozen=True, slots=True)
class _Base:
    """What the month is compared with, account by account."""

    windows: Mapping[int, tuple[list[ExpenseLine], ...]]
    """Per account compared in some month: its lines in each of those months."""
    current: list[ExpenseLine]

    def average[K](
        self, key: Callable[[ExpenseLine], K | None], measure: Callable[[ExpenseLine], Decimal] = _value
    ) -> Callable[[K], Decimal | None]:
        """The average of each ``key`` (``None`` keeps a line out): each account's, summed."""
        if not self.windows:
            return lambda _: None
        sums: dict[K, Decimal] = defaultdict(Decimal)
        for windows in self.windows.values():
            totals: dict[K, Decimal] = defaultdict(Decimal)
            for lines in windows:
                for line in lines:
                    found = key(line)
                    if found is not None:
                        totals[found] += measure(line)
            for found, total in totals.items():
                sums[found] += total / len(windows)
        # Uma conta sem mes para comparar nao tem media: o que ela toca fica sem.
        unbased = {key(line) for line in self.current if line.account_id not in self.windows}

        def lookup(found: K) -> Decimal | None:
            return None if found in unbased else sums.get(found, ZERO)

        return lookup


def _whole(line: ExpenseLine) -> bool:
    return True


def summarize(
    lines: Iterable[ExpenseLine],
    incomes: Iterable[IncomeLine],
    coverage: Coverage,
    month: date,
    *,
    today: date,
    fallback: str,
    compare: int = COMPARE_MONTHS,
    accounts: tuple[str, ...] = (),
) -> MonthSummary:
    """The month by group and category, against the months before it (see the module's rules)."""
    month = month_of(month)
    if month > month_of(today):
        raise ValidationError(f"Mês {month_label(month)} ainda não começou.")
    first = coverage.first
    if first is None:
        raise ValidationError("Nenhuma transação nas contas escolhidas.")
    if not coverage.covers(month):
        raise ValidationError(f"Mês {month_label(month)} fora da cobertura: os dados começam em {month_label(first)}.")
    partial = month == month_of(today)
    day = today.day if partial else None
    until = cut_end(month, day)
    counted = [line for line in lines if line.local_date <= today and coverage.counts(line.account_id, line.local_date)]

    def within(target: date, account_ids: Iterable[int] | None = None) -> list[ExpenseLine]:
        end = cut_end(target, day)
        chosen = set(coverage.starts) if account_ids is None else set(account_ids)
        return [line for line in counted if target <= line.local_date <= end and line.account_id in chosen]

    candidates = [add_months(month, -back) for back in range(compare, 0, -1)]
    compared = tuple(
        previous
        for previous in candidates
        if any(coverage.compares(account_id, previous) for account_id in coverage.starts)
    )
    current = within(month)
    windows: dict[int, tuple[list[ExpenseLine], ...]] = {}
    for account_id in coverage.starts:
        # Cada conta, so nos meses em que ela e comparada.
        own = tuple(within(previous, (account_id,)) for previous in compared if coverage.compares(account_id, previous))
        if own:
            windows[account_id] = own
    base = _Base(windows, current)
    fixed = _total(line for line in current if line.group.nature == GroupNature.FIXED)
    variable = _total(current) - fixed
    pace = None
    if partial:
        # O ritmo compara so o variavel: o fixo cai em dia marcado, nao no ritmo do mes.
        variable_average = base.average(lambda line: True if line.group.nature != GroupNature.FIXED else None)(True)
        if variable_average is not None:
            pace = Pace(variable, variable_average)
    last_year = add_months(month, -12)
    then = [account_id for account_id in coverage.starts if coverage.compares(account_id, last_year)]
    left_out = tuple(
        coverage.labels.get(account_id, str(account_id))
        for account_id, start in coverage.starts.items()
        if start is not None and start <= month and account_id not in then
    )
    year_before = (
        YearBefore(
            _total(within(last_year, then)), _total(line for line in current if line.account_id in then), left_out
        )
        if then
        else None
    )
    return MonthSummary(
        month=month,
        until=until,
        partial=partial,
        compared=compared,
        accounts=accounts,
        groups=_groups(current, base, partial=partial),
        total=_total(current),
        total_average=None if partial else base.average(_whole)(True),
        income=_total(
            line
            for line in incomes
            if month <= line.local_date <= until and coverage.counts(line.account_id, line.local_date)
        ),
        fixed=fixed,
        variable=variable,
        pace=pace,
        fallback=fallback,
        fallback_value=_total(line for line in current if line.group.fallback),
        last_year=year_before,
        merchants=_merchants(current, base),
        shorter=tuple(
            (coverage.labels.get(account_id, str(account_id)), len(own))
            for account_id, own in windows.items()
            if len(own) < len(compared)
        ),
    )


def _group_key(line: ExpenseLine) -> int:
    return line.group.id


def _category_key(line: ExpenseLine) -> tuple[int, str | None]:
    return line.group.id, line.category_id


def _groups(current: list[ExpenseLine], base: _Base, *, partial: bool) -> tuple[GroupSpending, ...]:
    """Every group with spending in the month or in the compared ones, the largest first."""
    groups: dict[int, CategoryGroup] = {}
    names: dict[tuple[int, str | None], str] = {}
    for line in [*(line for windows in base.windows.values() for lines in windows for line in lines), *current]:
        groups[line.group.id] = line.group
        names[_category_key(line)] = line.category or NO_CATEGORY
    by_group: dict[int, Decimal] = defaultdict(Decimal)
    by_category: dict[tuple[int, str | None], Decimal] = defaultdict(Decimal)
    for line in current:
        by_group[_group_key(line)] += line.value
        by_category[_category_key(line)] += line.value
    group_average = base.average(_group_key)
    category_average = base.average(_category_key)

    def fixed_out(group: CategoryGroup) -> bool:
        return partial and group.nature == GroupNature.FIXED  # na parcial, o fixo fica fora do ritmo

    result: list[GroupSpending] = []
    for group_id, group in groups.items():
        categories = sorted(
            (
                CategorySpending(
                    category_id=category_id,
                    name=name,
                    value=by_category.get((owner, category_id), ZERO),
                    average=None if fixed_out(group) else category_average((owner, category_id)),
                )
                for (owner, category_id), name in names.items()
                if owner == group_id
            ),
            key=_order,
        )
        result.append(
            GroupSpending(
                group=group,
                value=by_group.get(group_id, ZERO),
                average=None if fixed_out(group) else group_average(group_id),
                categories=tuple(categories),
            )
        )
    result.sort(key=lambda entry: (-entry.value, -(entry.average or ZERO), normalized(entry.group.name)))
    return tuple(result)


def _order(entry: CategorySpending) -> tuple[Decimal, Decimal, str]:
    return -entry.value, -(entry.average or ZERO), normalized(entry.name)


def _merchant_key(line: ExpenseLine) -> str:
    return normalized(line.merchant)


def _merchants(current: list[ExpenseLine], base: _Base) -> tuple[MerchantVisits, ...]:
    """The :data:`TOP_MERCHANTS` merchants with the most purchases in the month."""
    counts: dict[str, int] = defaultdict(int)
    values: dict[str, Decimal] = defaultdict(Decimal)
    shown: dict[str, str] = {}
    for line in current:
        key = _merchant_key(line)
        values[key] += line.value
        shown[key] = line.merchant
        if line.value > 0:
            counts[key] += 1
    average = base.average(_merchant_key, _purchase)
    top = sorted(counts, key=lambda key: (-counts[key], -values[key], key))[:TOP_MERCHANTS]
    return tuple(
        MerchantVisits(name=shown[key], count=counts[key], average=average(key), value=values[key]) for key in top
    )


# --------------------------------------------------------------- tendencia


@dataclass(frozen=True, slots=True)
class TrendPoint:
    month: date
    fixed: Decimal
    variable: Decimal
    missing: tuple[str, ...] = ()
    """The chosen accounts whose history had not started yet: the month sums only the others."""

    @property
    def total(self) -> Decimal:
        return self.fixed + self.variable


@dataclass(frozen=True, slots=True)
class Trend:
    points: tuple[TrendPoint, ...]
    """The covered months of the window, oldest first; a covered month without spending is zero."""
    today: date
    group: CategoryGroup | None
    accounts: tuple[str, ...]
    """As in :class:`MonthSummary`."""

    @property
    def partial(self) -> bool:
        """Whether the last point is the running month."""
        return bool(self.points) and self.points[-1].month == month_of(self.today)


def monthly_trend(
    lines: Iterable[ExpenseLine],
    coverage: Coverage,
    *,
    today: date,
    months: int = TREND_MONTHS,
    group: CategoryGroup | None = None,
    accounts: tuple[str, ...] = (),
) -> Trend:
    """The spending of each of the ``months`` months up to the running one (only ``group``'s, if given)."""
    last = month_of(today)
    window = [add_months(last, -back) for back in range(months - 1, -1, -1)]
    fixed: dict[date, Decimal] = defaultdict(Decimal)
    variable: dict[date, Decimal] = defaultdict(Decimal)
    for line in lines:
        if line.local_date > today or not coverage.counts(line.account_id, line.local_date):
            continue
        if group is not None and line.group.id != group.id:
            continue
        target = fixed if line.group.nature == GroupNature.FIXED else variable
        target[month_of(line.local_date)] += line.value
    points = tuple(
        TrendPoint(month, fixed[month], variable[month], coverage.missing(month))
        for month in window
        if coverage.covers(month)
    )
    return Trend(points=points, today=today, group=group, accounts=accounts)


# ------------------------------------------------------------- do banco


def _chosen(conn: Connection, account: str | None) -> tuple[list[StoredAccount], tuple[str, ...]]:
    stored = repository.accounts(conn)
    chosen = select_accounts(stored, account)
    labels = tuple(dict.fromkeys(entry.label for entry in chosen)) if account is not None else ()
    return chosen, labels


def month_summary(
    conn: Connection,
    *,
    today: date,
    month: date | None = None,
    account: str | None = None,
    compare: int = COMPARE_MONTHS,
) -> MonthSummary:
    """``bogle spending summary``: the month (the running one by default) of the accounts ``account`` names."""
    if compare < 1:
        raise ValidationError("--compare precisa de pelo menos 1 mês.")
    chosen, labels = _chosen(conn, account)
    target = month_of(month or today)
    ids = [entry.id for entry in chosen]
    since = min(add_months(target, -compare), add_months(target, -12))
    until = min(month_end(target), today)
    reserve = next(group for group in repository.category_groups(conn) if group.fallback)
    return summarize(
        repository.expense_lines(conn, ids, since, until) if until >= since else [],
        repository.income_lines(conn, ids, target, until) if until >= target else [],
        Coverage.of(chosen),
        target,
        today=today,
        fallback=reserve.name,
        compare=compare,
        accounts=labels,
    )


def spending_trend(
    conn: Connection,
    *,
    today: date,
    months: int = TREND_MONTHS,
    group: str | None = None,
    account: str | None = None,
) -> Trend:
    """``bogle spending trend``: the last ``months`` months, of every group or of ``group``."""
    if months < 1:
        raise ValidationError("--months precisa de pelo menos 1 mês.")
    chosen, labels = _chosen(conn, account)
    found = find_group(conn, group) if group is not None else None
    since = add_months(month_of(today), -(months - 1))
    lines = repository.expense_lines(conn, [entry.id for entry in chosen], since, today)
    return monthly_trend(lines, Coverage.of(chosen), today=today, months=months, group=found, accounts=labels)
