"""What an import writes, decided against the ledger before anything is written (issue #34).

Two phases. :func:`build_plan` (pure) reads the statement against the registered
assets and the ledger and decides, ticker by ticker, what goes in;
:func:`apply_import` writes exactly that, in one transaction. The preview is the
first phase alone, so ``--dry-run`` and the write show the same report.

A ticker is all or nothing. The first reason found keeps all of its rows out,
in this order:

0. a row of it could not be read — writing the rest would leave a day half
   recorded, and that day would then differ from the ledger forever;
1. it is not registered — the type decides the income tax and cannot be guessed;
2. a day's total differs from the ledger's — the file and what was typed by hand
   disagree, and only the user knows which one is right;
3. a sale ends up larger than the position of its day — the history before the
   statement is missing (the B3 only has it from November 2019).

Deduplication needs no migration: rows and ledger are compared by the **total
quantity of a day**, per ``(day, kind)``. A group the ledger does not have goes in,
one transaction per execution; a group with the same total is a duplicate, which
covers both a re-import and an order typed by hand as one line (18) that the B3
lists as two executions (5 + 13).

A group with the same total but a different value (``sum(shares x price)`` off
by more than a cent) is usually that same consolidated order typed with a
rounded average price. It still counts as a duplicate and is reported; with
``replace`` the ledger's rows of the group are removed and the B3's executions
written in their place, in the same transaction, carrying the fees and the tax
withheld the user had recorded for the group, split by the value of each
execution (see :func:`allot`).

Nothing here runs the closeout: the same file may buy a ticker again after
selling it out. Tickers left at zero with a target are listed instead — target is
intention, and the app only changes intention out loud.
"""

from __future__ import annotations

import dataclasses
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import DictRow

from bogle import format as fmt
from bogle.db import DEFAULT_TIMEZONE
from bogle.domain.assets import Asset
from bogle.domain.errors import ImportStaleError
from bogle.domain.ledger import end_of_day_positions, first_uncovered
from bogle.domain.transactions import Transaction, TransactionType
from bogle.imports.b3 import Statement, Trade
from bogle.rebalancing import b3_fee
from bogle.repositories.assets import AssetRepository
from bogle.repositories.transactions import TransactionRepository

_ZERO = Decimal("0")
_ZONE = ZoneInfo(DEFAULT_TIMEZONE)
_TRADES = (TransactionType.BUY, TransactionType.SELL)
_KIND_LABEL = {TransactionType.BUY: "compra", TransactionType.SELL: "venda"}
_ADD_EXAMPLE = "--type <STOCK|FII|ETF|BDR> --weight <peso>"
_CENT = Decimal("0.01")
REPLACE_HINT = "--replace troca pelos lançamentos da B3"
"""How the report says what to do about a day whose value differs (the CLI's flag)."""


@dataclass(frozen=True, slots=True)
class PlannedTrade:
    """One row of the statement that will become one transaction."""

    row: int
    ticker: str
    kind: TransactionType
    when: datetime
    shares: Decimal
    unit_price: Decimal
    fees: Decimal
    tax_withheld: Decimal = _ZERO
    """Only on a sale that replaces ledger rows: its share of what they had withheld."""
    replacing: bool = False
    """``True`` when it goes in place of ledger rows (a replaced group), not as new."""


@dataclass(frozen=True, slots=True)
class ValueMismatch:
    """A day with the ledger's quantity but another value: duplicated, or replaced."""

    ticker: str
    day: date
    kind: TransactionType
    in_file: Decimal
    """``sum(shares x price)`` of the file's executions of the group."""
    in_ledger: Decimal
    removed: tuple[Transaction, ...]
    """The ledger's rows of the group, as read: what a replacement deletes."""
    rows: tuple[int, ...]
    """The file's rows of the group."""


@dataclass(frozen=True, slots=True)
class Unregistered:
    ticker: str
    rows: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Divergent:
    """The first day whose total differs from the ledger's, in date order."""

    ticker: str
    day: date
    kind: TransactionType
    in_file: Decimal
    in_ledger: Decimal
    rows: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Uncovered:
    """The first day the ticker would close below zero, and by how much."""

    ticker: str
    day: date
    missing: Decimal
    rows: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Errored:
    """A ticker left out because one of its rows could not be read."""

    ticker: str
    rows: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class TargetToReview:
    """A ticker this import left at zero that still has a target."""

    ticker: str
    target_weight: Decimal


@dataclass(frozen=True, slots=True)
class ImportPlan:
    statement: Statement
    fee_rate: Decimal | None
    inserts: tuple[PlannedTrade, ...]
    """In the order they are written: by date, purchases before sales, then row."""
    duplicates: tuple[Trade, ...]
    unregistered: tuple[Unregistered, ...]
    divergent: tuple[Divergent, ...]
    uncovered: tuple[Uncovered, ...]
    errored: tuple[Errored, ...]
    targets: tuple[TargetToReview, ...]
    mismatched: tuple[ValueMismatch, ...] = ()
    """Groups whose value differs and were left as duplicates (no ``replace``)."""
    replaced: tuple[ValueMismatch, ...] = ()
    """Groups whose value differs and are replaced by the file's executions."""
    replace: bool = False

    @property
    def new(self) -> tuple[PlannedTrade, ...]:
        """What the file adds to the ledger, the replacements left out."""
        return tuple(trade for trade in self.inserts if not trade.replacing)

    @property
    def removals(self) -> tuple[Transaction, ...]:
        return tuple(txn for group in self.replaced for txn in group.removed)

    @property
    def sales(self) -> int:
        return sum(1 for trade in self.new if trade.kind is TransactionType.SELL)

    @property
    def charges_fees(self) -> bool:
        return bool(self.fee_rate)


class Outcome(StrEnum):
    """What became of a ticker, one per ticker (the preview table's column)."""

    NEW = "novas"
    DUPLICATE = "duplicadas"
    REPLACED = "substituídas"
    MISMATCHED = "valor difere"
    UNREGISTERED = "sem cadastro"
    DIVERGENT = "diverge do ledger"
    UNCOVERED = "venda descoberta"
    ERRORED = "linha com erro"


@dataclass(frozen=True, slots=True)
class TickerOutcome:
    ticker: str
    outcome: Outcome
    new: int
    duplicated: int
    detail: str


# ------------------------------------------------------------------ plano


def _day(when: datetime) -> date:
    return when.astimezone(_ZONE).date()


def _as_transaction(trade: Trade) -> Transaction:
    """A throwaway row for the ledger rules, which only read type, date and shares."""
    gross = trade.shares * trade.unit_price
    return Transaction(
        id=0,
        ticker=trade.ticker,
        transaction_type=trade.kind,
        date=trade.when,
        shares=trade.shares,
        unit_price=trade.unit_price,
        total_investment=gross,
        fees=_ZERO,
        total_cost=gross if trade.kind is TransactionType.BUY else _ZERO,
        tax_withheld=_ZERO,
    )


def _totals(entries: Iterable[tuple[date, TransactionType, Decimal]]) -> dict[tuple[date, TransactionType], Decimal]:
    totals: dict[tuple[date, TransactionType], Decimal] = defaultdict(lambda: _ZERO)
    for day, kind, shares in entries:
        totals[day, kind] += shares
    return totals


def _group_key(group: tuple[date, TransactionType]) -> tuple[date, bool]:
    return group[0], group[1] is TransactionType.SELL


def _in_zone(txn: Transaction) -> Transaction:
    """The row with its date in Sao Paulo, which is how the ledger rules read a day."""
    return dataclasses.replace(txn, date=txn.date.astimezone(_ZONE))


def _writing_order(trade: Trade | PlannedTrade) -> tuple[datetime, bool, int]:
    return trade.when, trade.kind is TransactionType.SELL, trade.row


def allot(total: Decimal, weights: list[Decimal]) -> list[Decimal]:
    """``total`` split by ``weights``, each part rounded half up to the cent.

    The rounding remainder goes to the last part, so the parts add up to the
    total exactly. A part never takes more than what is still left, which keeps
    the last one from going negative when the ones before it all rounded up.
    """
    whole = sum(weights, _ZERO)
    parts: list[Decimal] = []
    left = total
    for weight in weights[:-1]:
        part = min((total * weight / whole).quantize(_CENT, rounding=ROUND_HALF_UP), left)
        parts.append(part)
        left -= part
    parts.append(left)
    return parts


def _replacement(trades: list[Trade], removed: list[Transaction]) -> list[PlannedTrade]:
    """The file's executions of a group, carrying the fees and tax of the rows they replace.

    Split by the value of each execution, in writing order (the last one is the
    highest row of the file on that day and side).
    """
    ordered = sorted(trades, key=_writing_order)
    weights = [trade.shares * trade.unit_price for trade in ordered]
    fees = allot(sum((txn.fees for txn in removed), _ZERO), weights)
    taxes = allot(sum((txn.tax_withheld for txn in removed), _ZERO), weights)
    return [
        PlannedTrade(
            row=trade.row,
            ticker=trade.ticker,
            kind=trade.kind,
            when=trade.when,
            shares=trade.shares,
            unit_price=trade.unit_price,
            fees=fee,
            tax_withheld=tax,
            replacing=True,
        )
        for trade, fee, tax in zip(ordered, fees, taxes, strict=True)
    ]


def build_plan(
    statement: Statement,
    *,
    assets: Iterable[Asset],
    ledger: Iterable[Transaction],
    fee_rate: Decimal | None,
    replace: bool = False,
) -> ImportPlan:
    """Decide, ticker by ticker, what of ``statement`` goes into the ledger.

    ``replace`` swaps the ledger's rows of a day whose value differs for the
    file's executions (``--replace``); a day whose *quantity* differs still
    leaves the whole ticker out.
    """
    registered = {asset.ticker: asset for asset in assets}
    history: dict[str, list[Transaction]] = defaultdict(list)
    for txn in ledger:
        if txn.transaction_type in _TRADES:
            history[txn.ticker].append(txn)
    by_ticker: dict[str, list[Trade]] = defaultdict(list)
    for trade in statement.trades:
        by_ticker[trade.ticker].append(trade)
    error_rows: dict[str, list[int]] = defaultdict(list)
    for error in statement.errors:
        if error.ticker is not None:
            error_rows[error.ticker].append(error.row)

    inserts: list[PlannedTrade] = []
    duplicates: list[Trade] = []
    unregistered: list[Unregistered] = []
    divergent: list[Divergent] = []
    uncovered: list[Uncovered] = []
    errored: list[Errored] = []
    targets: list[TargetToReview] = []
    mismatched: list[ValueMismatch] = []
    replaced: list[ValueMismatch] = []

    for ticker in sorted(by_ticker.keys() | error_rows.keys()):
        trades = by_ticker.get(ticker, [])
        rows = tuple(sorted([trade.row for trade in trades] + error_rows.get(ticker, [])))
        if ticker in error_rows:
            errored.append(Errored(ticker, rows))
            continue
        asset = registered.get(ticker)
        if asset is None:
            unregistered.append(Unregistered(ticker, rows))
            continue
        known = history.get(ticker, [])
        in_file = _totals((_day(trade.when), trade.kind, trade.shares) for trade in trades)
        # O dia e o de Sao Paulo, como o das linhas do arquivo: um ledger lido em
        # outro fuso nao pode mudar de dia uma operacao.
        in_ledger = _totals((_day(txn.date), txn.transaction_type, txn.shares) for txn in known)
        clash = next(
            (
                group
                for group in sorted(in_file, key=_group_key)
                if group in in_ledger and in_ledger[group] != in_file[group]
            ),
            None,
        )
        if clash is not None:
            day, kind = clash
            divergent.append(Divergent(ticker, day, kind, in_file[clash], in_ledger[clash], rows))
            continue
        fresh = [trade for trade in trades if (_day(trade.when), trade.kind) not in in_ledger]
        duplicated = [trade for trade in trades if (_day(trade.when), trade.kind) in in_ledger]
        groups = _value_mismatches(ticker, duplicated, known)
        swapped = groups if replace else []
        gone = {txn.id for group in swapped for txn in group.removed}
        swapped_rows = {row for group in swapped for row in group.rows}
        swap_in = [
            planned
            for group in swapped
            for planned in _replacement([trade for trade in duplicated if trade.row in group.rows], list(group.removed))
        ]
        if fresh or swap_in:
            # A guarda roda sobre o ledger ja com a troca: as execucoes da B3 no
            # lugar dos lancamentos que saem.
            combined = (
                [_in_zone(txn) for txn in known if txn.id not in gone]
                + [_as_transaction(trade) for trade in fresh]
                + [_as_transaction(trade) for trade in duplicated if trade.row in swapped_rows]
            )
            short = first_uncovered(combined)
            if short is not None:
                uncovered.append(Uncovered(ticker, short[0], short[1], rows))
                continue
            closes = end_of_day_positions(combined)
            if fresh and closes and closes[-1][1] == 0 and asset.target_weight > 0:
                targets.append(TargetToReview(ticker, asset.target_weight))
        duplicates.extend(trade for trade in duplicated if trade.row not in swapped_rows)
        (replaced if replace else mismatched).extend(groups)
        inserts.extend(swap_in)
        inserts.extend(
            PlannedTrade(
                row=trade.row,
                ticker=ticker,
                kind=trade.kind,
                when=trade.when,
                shares=trade.shares,
                unit_price=trade.unit_price,
                fees=b3_fee(trade.shares * trade.unit_price, fee_rate) if fee_rate else _ZERO,
            )
            for trade in fresh
        )

    inserts.sort(key=_writing_order)
    return ImportPlan(
        statement=statement,
        fee_rate=fee_rate,
        inserts=tuple(inserts),
        duplicates=tuple(duplicates),
        unregistered=tuple(unregistered),
        divergent=tuple(divergent),
        uncovered=tuple(uncovered),
        errored=tuple(errored),
        targets=tuple(targets),
        mismatched=tuple(mismatched),
        replaced=tuple(replaced),
        replace=replace,
    )


def _value_mismatches(ticker: str, duplicated: list[Trade], known: list[Transaction]) -> list[ValueMismatch]:
    """The duplicated groups whose value differs from the ledger's by more than a cent."""
    by_group: dict[tuple[date, TransactionType], list[Trade]] = defaultdict(list)
    for trade in duplicated:
        by_group[_day(trade.when), trade.kind].append(trade)
    found: list[ValueMismatch] = []
    for group in sorted(by_group, key=_group_key):
        trades = by_group[group]
        rows = [txn for txn in known if (_day(txn.date), txn.transaction_type) == group]
        in_file = sum((trade.shares * trade.unit_price for trade in trades), _ZERO)
        in_ledger = sum((txn.shares * txn.unit_price for txn in rows), _ZERO)
        if abs(in_file - in_ledger) > _CENT:
            found.append(
                ValueMismatch(
                    ticker=ticker,
                    day=group[0],
                    kind=group[1],
                    in_file=in_file,
                    in_ledger=in_ledger,
                    removed=tuple(rows),
                    rows=tuple(sorted(trade.row for trade in trades)),
                )
            )
    return found


def plan_import(
    conn: psycopg.Connection[DictRow],
    statement: Statement,
    *,
    fee_rate: Decimal | None = None,
    replace: bool = False,
) -> ImportPlan:
    """:func:`build_plan` against the registered assets and the ledger as they are now."""
    return build_plan(
        statement,
        assets=AssetRepository(conn).list(),
        ledger=TransactionRepository(conn).list(),
        fee_rate=fee_rate,
        replace=replace,
    )


def apply_import(conn: psycopg.Connection[DictRow], plan: ImportPlan) -> list[Transaction]:
    """Remove ``plan.removals`` and write ``plan.inserts``, all or nothing.

    The plan is rebuilt inside the transaction and has to come out the same — the
    rows it writes *and* the ledger rows it removes, by id and content: a
    transaction recorded, edited or removed since the preview could turn a new row
    into a duplicate, a covered sale into an uncovered one or make a replacement
    delete something else, and what was confirmed is the plan that was shown. The
    repository's own ``transaction()`` blocks become savepoints inside this one,
    so a failure on any row takes all of them back, removals included.

    Removals go through the repository directly, not :func:`bogle.sales.remove_transaction`:
    that one checks each removal on its own, and a purchase taken out before its
    replacement goes in could look like it uncovers a later sale. The plan already
    checked the ledger as it is after the swap.
    """
    written: list[Transaction] = []
    with conn.transaction():
        fresh = plan_import(conn, plan.statement, fee_rate=plan.fee_rate, replace=plan.replace)
        if fresh.inserts != plan.inserts or fresh.removals != plan.removals:
            raise ImportStaleError()
        repo = TransactionRepository(conn)
        for txn in plan.removals:
            repo.delete(txn.id)
        for trade in plan.inserts:
            if trade.kind is TransactionType.BUY:
                written.append(
                    repo.add_buy(
                        trade.ticker, trade.when, shares=trade.shares, unit_price=trade.unit_price, fees=trade.fees
                    )
                )
            else:
                written.append(
                    repo.add_sale(
                        trade.ticker,
                        trade.when,
                        shares=trade.shares,
                        unit_price=trade.unit_price,
                        fees=trade.fees,
                        tax_withheld=trade.tax_withheld,
                    )
                )
    return written


# --------------------------------------------------------------- relatorio


def _per_ticker(tickers: Iterable[str]) -> str:
    counts = Counter(tickers)
    return ", ".join(f"{ticker} {fmt.count(counts[ticker])}" for ticker in sorted(counts))


def _plural(n: int, one: str, many: str) -> str:
    return f"{fmt.count(n)} {one if n == 1 else many}"


def _group_label(item: ValueMismatch | Divergent) -> str:
    return f"{item.ticker} {item.day.isoformat()} {_KIND_LABEL[item.kind]}"


def replaced_detail(item: ValueMismatch) -> str:
    """``2026-01-09 compra: 1 → 2 lançamentos``: what leaves the ledger and what comes in."""
    entries = _plural(len(item.rows), "lançamento", "lançamentos").split(" ", 1)[1]
    return (
        f"{item.day.isoformat()} {_KIND_LABEL[item.kind]}: "
        f"{fmt.count(len(item.removed))} → {fmt.count(len(item.rows))} {entries}"
    )


def mismatch_detail(item: ValueMismatch) -> str:
    """``2026-01-09 compra: arquivo 1.920,21 × ledger 1.920,24``."""  # noqa: RUF002 — sinal de vezes
    return (
        f"{item.day.isoformat()} {_KIND_LABEL[item.kind]}: "
        f"arquivo {fmt.money(item.in_file)} × ledger {fmt.money(item.in_ledger)}"  # noqa: RUF001 — sinal de vezes, como na spec
    )


def report_lines(plan: ImportPlan, *, replace_hint: str = REPLACE_HINT) -> list[str]:
    """The report, one line per category that has something, for ``fmt.attention``.

    The same in the preview and after the write: "Novas" is what the file adds
    to the ledger, whether or not it has been written yet. ``replace_hint`` says
    how to ask for the replacement of a day whose value differs — the flag in the
    command, the checkbox on the screen.
    """
    added = plan.new
    new = len(added)
    lines = [f"Novas: {fmt.count(new)} ({_per_ticker(t.ticker for t in added)})" if new else "Novas: 0"]
    if plan.duplicates:
        lines.append(
            f"Duplicadas: {fmt.count(len(plan.duplicates))} ({_per_ticker(t.ticker for t in plan.duplicates)})"
        )
    if plan.replaced:
        listed = ", ".join(f"{item.ticker} {replaced_detail(item)}" for item in plan.replaced)
        lines.append(f"Substituídas: {fmt.count(len(plan.replaced))} ({listed})")
    ignored = plan.statement.ignored_markets
    if ignored:
        total = sum(count for _, count in ignored)
        listed = ", ".join(f"{market} {fmt.count(count)}" for market, count in ignored)
        lines.append(f"Ignoradas por mercado: {fmt.count(total)} ({listed})")
    if plan.unregistered:
        names = ", ".join(item.ticker for item in plan.unregistered)
        lines.append(f"Sem cadastro: {names} (bogle add {plan.unregistered[0].ticker} {_ADD_EXAMPLE})")
    if plan.divergent:
        listed = ", ".join(
            f"{item.ticker} ({item.day.isoformat()} {_KIND_LABEL[item.kind]}: "
            f"arquivo {fmt.exact(item.in_file)} × ledger {fmt.exact(item.in_ledger)})"  # noqa: RUF001 — sinal de vezes, como na spec
            for item in plan.divergent
        )
        lines.append(f"Total do dia diverge do ledger: {listed}")
    if plan.mismatched:
        listed = ", ".join(f"{item.ticker} ({mismatch_detail(item)})" for item in plan.mismatched)
        lines.append(f"Valor do dia difere do ledger: {listed}; {replace_hint}")
    if plan.uncovered:
        listed = ", ".join(
            f"{item.ticker} (faltam {fmt.exact(item.missing)} em {item.day.isoformat()})" for item in plan.uncovered
        )
        lines.append(f"Venda descoberta: {listed}: registre a posição de abertura com bogle buy --date e reimporte")
    if plan.targets:
        listed = ", ".join(f"{item.ticker} {fmt.pct(item.target_weight)}" for item in plan.targets)
        lines.append(
            f"Posição zerada com target: {listed} (bogle update {plan.targets[0].ticker} --weight 0 tira do plano)"
        )
    errors = plan.statement.errors
    if errors:
        listed = ", ".join(f"{error.row} ({error.reason})" for error in errors)
        line = f"Linhas com erro: {listed}"
        if plan.errored:
            skipped = ", ".join(item.ticker for item in plan.errored)
            line += f"; {'ticker pulado' if len(plan.errored) == 1 else 'tickers pulados'}: {skipped}"
        lines.append(line)
    if new and not plan.charges_fees:
        lines.append(_plural(new, "operação importada sem taxas", "operações importadas sem taxas"))
    if plan.sales:
        lines.append(_plural(plan.sales, "venda importada sem IR retido", "vendas importadas sem IR retido"))
    return lines


def ticker_outcomes(plan: ImportPlan) -> list[TickerOutcome]:
    """One row per ticker of the file, alphabetical: the preview table."""
    new = Counter(trade.ticker for trade in plan.new)
    duplicated = Counter(trade.ticker for trade in plan.duplicates)
    targets = {item.ticker: item for item in plan.targets}
    replaced: dict[str, list[ValueMismatch]] = defaultdict(list)
    for item in plan.replaced:
        replaced[item.ticker].append(item)
    mismatched: dict[str, list[ValueMismatch]] = defaultdict(list)
    for item in plan.mismatched:
        mismatched[item.ticker].append(item)
    rows: list[TickerOutcome] = []
    for ticker in sorted(new.keys() | duplicated.keys() | replaced.keys()):
        # Uma linha por ticker: a troca e a divergencia de valor sao o que ha de
        # mais importante a ver nele, e as colunas de contagem dizem o resto.
        target = targets.get(ticker)
        if replaced[ticker]:
            outcome, detail = Outcome.REPLACED, ", ".join(replaced_detail(item) for item in replaced[ticker])
        elif mismatched[ticker]:
            outcome, detail = Outcome.MISMATCHED, ", ".join(mismatch_detail(item) for item in mismatched[ticker])
        else:
            outcome = Outcome.NEW if new[ticker] else Outcome.DUPLICATE
            detail = ""
        if target is not None:
            detail = ", ".join(part for part in (detail, f"zerada, target {fmt.pct(target.target_weight)}") if part)
        rows.append(TickerOutcome(ticker, outcome, new[ticker], duplicated[ticker], detail))
    rows.extend(
        TickerOutcome(item.ticker, Outcome.UNREGISTERED, 0, 0, f"bogle add {item.ticker}") for item in plan.unregistered
    )
    rows.extend(
        TickerOutcome(
            item.ticker,
            Outcome.DIVERGENT,
            0,
            0,
            f"{item.day.isoformat()} {_KIND_LABEL[item.kind]}: "
            f"arquivo {fmt.exact(item.in_file)} × ledger {fmt.exact(item.in_ledger)}",  # noqa: RUF001 — sinal de vezes, como na spec
        )
        for item in plan.divergent
    )
    rows.extend(
        TickerOutcome(
            item.ticker, Outcome.UNCOVERED, 0, 0, f"faltam {fmt.exact(item.missing)} em {item.day.isoformat()}"
        )
        for item in plan.uncovered
    )
    error_rows: dict[str, list[str]] = defaultdict(list)
    for error in plan.statement.errors:
        if error.ticker is not None:
            error_rows[error.ticker].append(str(error.row))
    rows.extend(
        TickerOutcome(
            item.ticker,
            Outcome.ERRORED,
            0,
            0,
            f"{'linha' if len(error_rows[item.ticker]) == 1 else 'linhas'} {', '.join(error_rows[item.ticker])}",
        )
        for item in plan.errored
    )
    rows.sort(key=lambda row: row.ticker)
    return rows


def describe_rows(plan: ImportPlan) -> tuple[int, int, int]:
    """``(purchases, sales, tickers)`` of what will be written."""
    sales = sum(1 for trade in plan.inserts if trade.kind is TransactionType.SELL)
    return len(plan.inserts) - sales, sales, len({trade.ticker for trade in plan.inserts})


def summary(plan: ImportPlan, source: str) -> str:
    """Two lines for the confirmation and the "done" dialogs."""
    buys, sales, tickers = describe_rows(plan)
    head = f"{_plural(len(plan.inserts), 'operação', 'operações')} de {source}"
    if plan.removals:
        head += f", no lugar de {_plural(len(plan.removals), 'lançamento', 'lançamentos')}"
    return (
        f"{head}\n{_plural(buys, 'compra', 'compras')}, {_plural(sales, 'venda', 'vendas')}, "
        f"{_plural(tickers, 'ticker', 'tickers')}"
    )
