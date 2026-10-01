"""Headline portfolio overview at a reference date (issue #73).

The four numbers the TUI opens with, all measured at the same reference date.
:func:`compute_current_overview` picks it: today, priced with brapi's D-0 quote
on top of the stored closes (which never include today, see issue #82), or the
previous close (D-1) when there is no quote from today to use, before the
session opens, on a weekend, or with brapi down:

1. **patrimony** — market value of the positions on that date;
2. **variation** — patrimony minus what those positions cost (R$ and %);
3. **twr_12m** / **twr_total** — time-weighted return over the last 12 months
   and since the first transaction.

"What they cost" is the average cost (issue #85): the RFB's sequential replay
of :mod:`bogle.domain.cost_basis`, fees included, times the quantity still held
— the same "Preco medio" the Position screen shows. The variation is then the
unrealized gain alone. The old base, net capital (buys minus gross sale
proceeds), slipped the gain of a *partial* sale into it, and not the gain of a
total one, since the ticker left the view; the realized gain has its own report
in ``bogle profit``.

TWR (issue #20) is the honest lens for a headline return: it removes the size
and the timing of contributions and withdrawals and credits income, so a fresh
aporte never reads as performance.

Everything is measured *at* ``as_of``, invested capital included: reading it off
the ledger as it stands today instead would mix in transactions dated after the
reference date, and a buy registered today would show up as a loss the size of
the aporte (the money is in the base, the shares are not in the patrimony yet).

Tickers without a historical price source (TESOURO — see #17 — or a ticker whose
history fetch failed) are excluded from *every* number, so patrimony and
variation stay comparable; ``excluded`` carries them for the caller to report,
the same policy as the other historical reports (#67). A caller showing
``patrimony`` with a non-empty ``excluded`` is showing a *partial* patrimony and
should say so.

A ticker whose *series starts after its position* is a milder case, and it gets
milder treatment here than in the series reports: patrimony and variation are
single-date numbers, so it takes part in them (the close at ``as_of`` exists),
and only the two TWRs — which have to walk from a date the series does not reach
— leave it out. It lands in ``excluded_from_returns`` instead of ``excluded``:
the alternative was hiding real money from the headline patrimony because a
return could not be computed. The price is that the two pairs of numbers cover
slightly different portfolios, which is why the note names the ticker and the
numbers it is missing from instead of just saying "no history".

``as_of`` is a trading day (``previous_business_day`` walks over weekends and
national holidays), but the provider having published that session's bar is a
separate matter — and one ticker having it while another does not is routine. The
valuator prices the date from the latest bar it has, so a series that stops short
answers with an older close, silently. ``stale_prices`` names those tickers and
the close each one actually used: without it the summary claims a reference date
part of the portfolio was never priced at, which is precisely how it comes to
disagree with the live-quote Position screen for no visible reason.

When the reference is a past close (D-1), a transaction registered *today* is in
none of the four numbers — correct, and completely invisible: the summary simply
does not move, which reads as a screen that failed to refresh. ``pending_entries``
counts them so the caller can say what is waiting for the next close instead of
leaving the user pressing "Atualizar".
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal

import psycopg
from psycopg.rows import DictRow

from bogle.analytics.business_days import is_business_day, previous_business_day
from bogle.analytics.twr import compute_twr
from bogle.data.dispatcher import PriceDispatcher
from bogle.domain.cost_basis import replay_by_ticker
from bogle.domain.transactions import Transaction
from bogle.position import local_time
from bogle.reports.periods import period_start
from bogle.reports.valuation import (
    build_portfolio_valuation,
    first_transaction_date,
    spot_patrimony,
    stale_at_end,
)
from bogle.repositories.transactions import TransactionRepository

_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class PortfolioOverview:
    as_of: date
    """Reference date of every number below (D-1 for the TUI's Home)."""
    inception: date | None
    """First transaction ever; ``None`` when the ledger is empty."""
    invested: Decimal
    """Average cost of the positions that could be valued, as of ``as_of`` (#85)."""
    patrimony: Decimal | None
    """``None`` when nothing could be valued at ``as_of``."""
    twr_12m: Decimal | None
    twr_total: Decimal | None
    twr_12m_start: date | None
    """Where the 12m window actually starts — the inception when the portfolio
    is younger than 12 months, in which case the window is shorter than its name."""
    excluded: list[str]
    """Out of *every* number: nothing here can be priced, not even at ``as_of``."""
    excluded_reasons: dict[str, str] = field(default_factory=dict)
    """Why each excluded ticker is out (see :mod:`bogle.reports.valuation`). The
    Home screen shows it: "no price history" reads like a permanent fact about the
    asset, and two of the four reasons are a provider hiccup worth retrying."""
    excluded_from_returns: list[str] = field(default_factory=list)
    """In ``patrimony``/``variation``, out of the two TWRs: priceable at ``as_of``
    but not from the start of the window (a series that begins after the position)."""
    returns_reasons: dict[str, str] = field(default_factory=dict)
    """Why each ticker in ``excluded_from_returns`` is out of the returns."""
    stale_prices: dict[str, date] = field(default_factory=dict)
    """Tickers priced at a close *older* than ``as_of``, and the one used instead.

    Inside every number — the price is real, just not from the reference day. The
    caller names them: a patrimony where two of three tickers are a day behind is
    still the best available reading, but it is not the reading the panel title
    promises, and the gap is what makes it differ from a live-quote screen."""
    pending_entries: int = 0
    """Transactions dated after ``as_of``, so in none of the numbers above."""
    pending_invested: Decimal = _ZERO
    """Capital those transactions move (see :func:`pending_after`); zero when they
    are all income, which moves neither patrimony nor invested."""
    quote_time: datetime | None = None
    """Set when the summary is D-0: the latest brapi quote behind it, in local
    time. ``as_of`` is then today, and a ticker brapi did not quote today shows up
    in ``stale_prices`` at its last close."""
    quote_failed: list[str] = field(default_factory=list)
    """Tickers brapi could not quote today. When the summary fell back to D-1
    because of them, this is the explanation the caller owes the user."""

    @property
    def is_live(self) -> bool:
        """``True`` when the numbers use today's quotes (D-0), not a past close."""
        return self.quote_time is not None

    @property
    def is_empty(self) -> bool:
        return self.inception is None

    @property
    def has_pending(self) -> bool:
        """``True`` when the ledger moved after the reference close.

        The caller must say so: the alternative is a summary that ignores what the
        user just registered without a word about why."""
        return self.pending_entries > 0

    @property
    def is_partial(self) -> bool:
        """``True`` when a ticker was left out, making ``patrimony`` a subset."""
        return bool(self.excluded)

    @property
    def has_stale_prices(self) -> bool:
        """``True`` when some ticker is priced before ``as_of``; the caller says which."""
        return bool(self.stale_prices)

    @property
    def returns_are_partial(self) -> bool:
        """``True`` when the TWRs cover less of the portfolio than the patrimony does."""
        return bool(self.excluded) or bool(self.excluded_from_returns)

    @property
    def all_reasons(self) -> dict[str, str]:
        """Every exclusion reason in one mapping, whichever numbers it affects."""
        return {**self.excluded_reasons, **self.returns_reasons}

    @property
    def twr_12m_is_shorter(self) -> bool:
        """``True`` when the "12m" window had to anchor on the first transaction."""
        return self.twr_12m_start is not None and self.twr_12m_start == self.inception

    @property
    def variation(self) -> Decimal | None:
        return self.patrimony - self.invested if self.patrimony is not None else None

    @property
    def variation_percent(self) -> Decimal | None:
        # Sem posicao avaliavel o custo e zero, e uma porcentagem sobre ele nao
        # existe.
        variation = self.variation
        if variation is None or self.invested <= _ZERO:
            return None
        return variation / self.invested


def _as_date(value: date) -> date:
    return value.date() if isinstance(value, datetime) else value


def pending_after(transactions: list[Transaction], on: date) -> tuple[int, Decimal]:
    """Ledger rows dated after ``on``, and the invested capital they move.

    None of them is in any of the four numbers — the reference is a past close —
    and that is invisible from the outside: registering a purchase, coming back
    and finding a summary that did not budge reads as a screen that failed to
    refresh, which is exactly what it does not do. The caller says it out loud.

    The amount is what they will do to ``invested_at`` (a buy adds its cost,
    fees included; a sale takes out the average cost of what it sold, not its
    proceeds), so it is comparable with the base it will join. Income moves
    neither, and only shows up in the count.
    """
    count = sum(1 for txn in transactions if _as_date(txn.date) > on)
    if count == 0:
        return 0, _ZERO
    return count, invested_at(transactions, date.max) - invested_at(transactions, on)


def invested_at(transactions: list[Transaction], on: date) -> Decimal:
    """Average cost of the positions still held at ``on`` (issue #85).

    The sequential replay of :mod:`bogle.domain.cost_basis` over the ledger up to
    ``on`` — so it is comparable with a patrimony valued on that same date — and,
    per ticker, average cost times the quantity left. A sale takes out the cost of
    the units it sold and nothing else: its gain is realized, and stays out.

    A ticker the replay refuses is left out; the valuation already excluded it,
    with the reason, from the patrimony this is compared with.
    """
    states, _ = replay_by_ticker([t for t in transactions if _as_date(t.date) <= on])
    return sum((state.invested for state in states.values()), _ZERO)


def compute_overview(
    conn: psycopg.Connection[DictRow],
    dispatcher: PriceDispatcher,
    *,
    as_of: date,
    live: bool = False,
) -> PortfolioOverview:
    """Value the portfolio at ``as_of`` and measure its return up to that date.

    ``live`` prices ``as_of`` (today) with brapi's D-0 quotes; see
    :func:`compute_current_overview`, which is what decides whether to.
    """
    transactions = TransactionRepository(conn).list()
    inception = first_transaction_date(transactions)
    pending_entries, pending_invested = pending_after(transactions, as_of)

    if inception is None or as_of < inception:
        # Empty ledger, or the first transaction is younger than the reference
        # date: there is no earlier close to value. In the second case *every*
        # transaction is pending, which is the whole explanation for the empty
        # summary — hence it travels here too.
        return PortfolioOverview(
            as_of=as_of,
            inception=inception,
            invested=_ZERO,
            patrimony=None,
            twr_12m=None,
            twr_total=None,
            twr_12m_start=None,
            excluded=[],
            pending_entries=pending_entries,
            pending_invested=pending_invested,
        )

    valuation = build_portfolio_valuation(conn, dispatcher, start=inception, end=as_of, live=live)

    twr_total: Decimal | None = None
    twr_12m: Decimal | None = None
    start_12m: date | None = None
    if valuation.valuator is not None and valuation.transactions:
        twr_total = compute_twr(valuation.transactions, None, inception, as_of, valuator=valuation.valuator)
        start_12m = max(inception, period_start("12m", today=as_of) or inception)
        twr_12m = compute_twr(valuation.transactions, None, start_12m, as_of, valuator=valuation.valuator)

    # Patrimonio e variacao saem do universo "spot" (avaliavel em as_of), que
    # inclui tambem quem nao cobre a janela inteira; capital investido vem das
    # transacoes desse mesmo universo, senao a variacao compararia carteiras
    # diferentes.
    spot = valuation.spot
    returns_reasons = {ticker: reason for ticker, reason in valuation.reasons.items() if ticker not in spot.reasons}
    return PortfolioOverview(
        as_of=as_of,
        inception=inception,
        invested=invested_at(spot.transactions, as_of),
        patrimony=spot_patrimony(valuation),
        twr_12m=twr_12m,
        twr_total=twr_total,
        twr_12m_start=start_12m,
        excluded=spot.excluded,
        excluded_reasons=spot.reasons,
        excluded_from_returns=sorted(returns_reasons),
        returns_reasons=returns_reasons,
        stale_prices=stale_at_end(valuation),
        pending_entries=pending_entries,
        pending_invested=pending_invested,
        quote_time=local_time(valuation.quote_time) if valuation.quote_time is not None else None,
        quote_failed=valuation.quote_failed,
    )


def compute_current_overview(
    conn: psycopg.Connection[DictRow], dispatcher: PriceDispatcher, *, today: date
) -> PortfolioOverview:
    """The Home summary: D-0 when brapi has a quote from today, D-1 otherwise.

    On a trading day the summary is measured at today, with the D-0 quote as the
    last point of every variable-income series. It is never stored: the closes
    in the table stop at D-1 (issue #82). The summary falls back to the previous
    close when no ticker got a quote from today:

    - before the session opens, brapi still answers with the previous close,
      which the table already has. Nothing failed, and nothing is said;
    - with brapi down, the fallback carries the tickers it could not quote, so
      the caller can say why the summary is a day behind.

    On a weekend or a holiday there is no session to quote, and the summary goes
    straight to the last close. A ticker that missed its D-0 quote while others
    got theirs stays in the D-0 summary at its last close, named in
    ``stale_prices`` like any series that stops short of ``as_of``.
    """
    previous_close = previous_business_day(today)
    if not is_business_day(today):
        return compute_overview(conn, dispatcher, as_of=previous_close)
    live = compute_overview(conn, dispatcher, as_of=today, live=True)
    if live.is_live:
        return live
    fallback = compute_overview(conn, dispatcher, as_of=previous_close)
    return replace(fallback, quote_failed=live.quote_failed)
