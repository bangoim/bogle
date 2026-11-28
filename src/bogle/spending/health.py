"""The health of the Meu Pluggy connections, for ``bogle check`` (issue #114).

A connection fails quietly: Pluggy keeps answering with the last data it had,
and the consent simply stops one year after it was given. ``bogle check`` runs
under launchd, so it is the one place that can say so before the numbers go
stale. Three things are checked per connection:

- the consent expires in less than :data:`CONSENT_WARNING` (or expired);
- the connection is in error (it needs the user at meu.pluggy.ai);
- it has not updated for :data:`STALE_AFTER`. Only on a state read just now:
  an old reading says nothing about how long the connection has been stopped.

The check also runs the automatic sync (past 24 h), which reads every state
anyway; when it did not run, the states are read on their own. Without any
Pluggy configuration and nothing stored, nothing is read at all.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import DictRow

from bogle.data.pluggy import PluggyClient, PluggyConfig, any_config, load_config
from bogle.db import DEFAULT_TIMEZONE, get_connection
from bogle.domain.errors import BogleError, PluggyNotConfiguredError
from bogle.spending import repository
from bogle.spending.models import ItemState
from bogle.spending.sync import ClientFactory, not_synced, sync_if_due

CONSENT_WARNING = timedelta(days=30)
STALE_AFTER = timedelta(hours=48)
ERROR_STATUSES = frozenset({"LOGIN_ERROR", "OUTDATED", "WAITING_USER_INPUT"})
RENEW = "renove em meu.pluggy.ai"
RECONNECT = "reconecte em meu.pluggy.ai"

_LOCAL_TZ = ZoneInfo(DEFAULT_TIMEZONE)

Connection = psycopg.Connection[DictRow]


def _days(count: int) -> str:
    return "1 dia" if count == 1 else f"{count} dias"


def _consent_line(item: ItemState, now: datetime) -> str | None:
    expires = item.consent_expires_at
    if expires is None:
        return None
    day = expires.astimezone(_LOCAL_TZ).date()
    if expires <= now:
        return f"Consentimento de {item.label} venceu em {day.isoformat()}: {RENEW}"
    if expires - now >= CONSENT_WARNING:
        return None
    left = (day - now.astimezone(_LOCAL_TZ).date()).days
    when = "vence hoje" if left <= 0 else f"vence em {_days(left)}"
    return f"Consentimento de {item.label} {when} ({day.isoformat()}): {RENEW}"


def _connection_line(item: ItemState, now: datetime, *, fresh: bool) -> str | None:
    if item.status in ERROR_STATUSES:
        return f"Conexão {item.label} com erro ({item.status}): {RECONNECT}"
    updated = item.last_updated_at
    if fresh and updated is not None and now - updated > STALE_AFTER:
        return f"Conexão {item.label} sem atualizar desde {updated.astimezone(_LOCAL_TZ):%Y-%m-%d %H:%M}"
    return None


def connection_alerts(items: Sequence[ItemState], now: datetime, *, fresh: bool) -> list[str]:
    """The lines of ``bogle check`` for the connections, in their order.

    Per connection, the consent first, then its state, where an error beats a
    stop (it is the reason for it). An expired consent says it all: the error
    that comes with it is not repeated.
    """
    lines: list[str] = []
    for item in items:
        consent = _consent_line(item, now)
        if consent is not None:
            lines.append(consent)
            if item.consent_expires_at is not None and item.consent_expires_at <= now:
                continue
        connection = _connection_line(item, now, fresh=fresh)
        if connection is not None:
            lines.append(connection)
    return lines


def check_connections(
    now: datetime,
    *,
    config: PluggyConfig | None,
    refreshed: bool = False,
    connect: Callable[[], Connection] = get_connection,
    client_factory: ClientFactory = PluggyClient,
) -> list[str]:
    """The alerts of every connection, read again from Pluggy when there is a configuration.

    ``refreshed`` says the sync just read every state, so no request is made.
    Without a configuration, the stored states give the consent and the error,
    never the stop.
    """
    conn = connect()
    try:
        fresh = refreshed
        if config is not None and not refreshed:
            client = client_factory(config.client_id, config.client_secret)
            try:
                for item_id in config.item_ids:
                    repository.upsert_item(conn, client.get_item(item_id), checked_at=now)
            finally:
                client.close()
            repository.delete_items_except(conn, config.item_ids)
            fresh = True
        return connection_alerts(repository.item_states(conn), now, fresh=fresh)
    finally:
        conn.close()


@dataclass(frozen=True, slots=True)
class SpendingCheck:
    alerts: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def check_spending(
    now: datetime | None = None,
    *,
    env: Mapping[str, str] | None = None,
    connect: Callable[[], Connection] = get_connection,
    client_factory: ClientFactory = PluggyClient,
) -> SpendingCheck:
    """The spending stage of ``bogle check``: the automatic sync, then the connections.

    A configuration that is there but broken is an error (and the stored states
    still give their alerts); no configuration at all is not, it is a user who
    does not track spending, or a scheduler started without the variables.
    """
    moment = now or datetime.now(UTC)
    env = os.environ if env is None else env
    errors: list[str] = []
    config: PluggyConfig | None = None
    refreshed = False
    if any_config(env):
        try:
            config = load_config(env)
        except PluggyNotConfiguredError as exc:
            errors.append(not_synced(str(exc), None))
    if config is not None:
        auto = sync_if_due(now=moment, env=env, connect=connect, client_factory=client_factory)
        refreshed = auto.ran
        if auto.problem is not None:
            errors.append(auto.problem)
    try:
        alerts = check_connections(
            moment, config=config, refreshed=refreshed, connect=connect, client_factory=client_factory
        )
    except BogleError:
        if not errors:
            raise
        # O sync ja falhou e disse por que: a mesma falha de novo seria ruido.
        alerts = check_connections(moment, config=None, connect=connect)
    return SpendingCheck(alerts=alerts, errors=errors)
