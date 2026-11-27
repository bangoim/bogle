"""What a closed position does to its target weight.

Selling every share of a ticker used to leave the target behind, harmless because
nothing ever looked at it. It stopped being harmless when the contribution engine
started funding targets that have no position yet
(:func:`~bogle.position.get_allocation_summary`): from then on, a target left over
from an asset the user walked away from would quietly take the next contribution.

So the sale that empties a position also clears its target, and both frontends say
so — the TUI with a dialog that offers to put it back, the command with the line
that undoes it. Automatic, and announced: the target is the user's *intention*,
and software does not get to change an intention silently.

With a glide path in force (:mod:`bogle.glide_path`), the sale still clears the
target: the trajectory puts it back on the next 01/01 when it gives the ticker a
target in the coming year, and the notice says so.

The asset row itself is never touched. Deleting it is not even possible while the
ledger points at it (``AssetHasTransactionsError``), and it should not be: the
history is what ``bogle profit`` and the tax report are made of. What the asset
lists do instead is set it apart (:func:`split_closed`): a closed asset stays
registered, under its own heading, out of the way of the ones the plan is made of.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import psycopg
from psycopg.rows import DictRow

from bogle.domain.assets import Asset
from bogle.domain.errors import GlidePathError
from bogle.format import pct
from bogle.glide_path import returning_target
from bogle.repositories.assets import AssetRepository
from bogle.repositories.holdings import HoldingRepository

_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class ClearedTarget:
    """A target weight that was cleared on its own, and what it used to be."""

    ticker: str
    previous_target: Decimal
    """What to restore, for the frontend that offers to undo it."""
    returns_in: int | None = None
    """The year whose 01/01 gives the target back, when the glide path does."""
    returning_target: Decimal | None = None
    """The target it comes back with."""


def clear_closed_target(
    conn: psycopg.Connection[DictRow], ticker: str, *, today: date | None = None
) -> ClearedTarget | None:
    """Clear ``ticker``'s target weight if it no longer has a position.

    Returns what was cleared, or ``None`` when there was nothing to clear — the
    position is still open (a partial sale), the target was already zero, or the
    asset is gone. Called right after a sale is recorded, so it answers the
    question the sale just raised: is this ticker still part of the plan?

    ``today`` sets which 01/01 is the next one, for a ticker the glide path
    brings back.
    """
    if HoldingRepository(conn).get(ticker) is not None:
        return None  # venda parcial: a posicao continua aberta
    asset = AssetRepository(conn).get(ticker)
    if asset is None or asset.target_weight <= _ZERO:
        return None
    AssetRepository(conn).update_weight(asset.ticker, _ZERO)
    today = today if today is not None else date.today()
    try:
        returning = returning_target(conn, asset.ticker, today)
    except GlidePathError:
        # Uma trajetoria invalida nao pode desfazer a venda (isto roda dentro da
        # transacao dela); a aplicacao anual e quem reclama disso.
        returning = None
    return ClearedTarget(
        ticker=asset.ticker,
        previous_target=asset.target_weight,
        returns_in=today.year + 1 if returning is not None else None,
        returning_target=returning,
    )


def cleared_notice(cleared: ClearedTarget) -> str:
    """What both frontends tell the user about it, in one voice."""
    notice = (
        f"{cleared.ticker}: a venda zerou a posição, e o target de {pct(cleared.previous_target)} "
        "foi removido — sem isso o próximo aporte mandaria dinheiro para um ativo "
        "que você não tem mais."
    )
    if cleared.returns_in is None or cleared.returning_target is None:
        return notice
    return f"{notice}\nO alvo volta a {pct(cleared.returning_target)} em 01/01/{cleared.returns_in} pela trajetória."


@dataclass(frozen=True, slots=True)
class AssetRoster:
    """The registered assets, split the way the plan sees them."""

    in_plan: list[Asset]
    """With a position, a target or both: what the portfolio is made of."""
    closed: list[Asset]
    """With neither (:func:`is_closed`): kept for the history, and nothing else."""


def is_closed(asset: Asset, held: Collection[str]) -> bool:
    """Whether ``asset`` is out of the plan: no position, and no target either.

    Both, because either one alone means something else. A target with no position
    is an asset still to be bought — the next contribution goes there. A position
    with no target is one being left to shrink — the drift shows it. Only the two
    together describe an asset the portfolio no longer has and the plan no longer
    wants. ``held`` is the tickers with an open position.
    """
    return asset.target_weight <= _ZERO and asset.ticker not in held


def split_closed(assets: Iterable[Asset], held: Collection[str]) -> AssetRoster:
    """``assets`` as an :class:`AssetRoster`, each side in the order given."""
    in_plan: list[Asset] = []
    closed: list[Asset] = []
    for asset in assets:
        (closed if is_closed(asset, held) else in_plan).append(asset)
    return AssetRoster(in_plan=in_plan, closed=closed)
