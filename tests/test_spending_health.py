"""Tests for the health of the Meu Pluggy connections (issue #114): the lines of ``bogle check``."""

from __future__ import annotations

from datetime import timedelta

import psycopg
import pytest
from psycopg.rows import DictRow

from bogle.data.pluggy import load_config
from bogle.db import get_connection
from bogle.domain.errors import NetworkError
from bogle.settings import SPENDING_LAST_SYNC_AT, set_value
from bogle.spending import repository
from bogle.spending.health import check_connections, check_spending, connection_alerts
from bogle.spending.models import ItemState
from bogle.spending.sync import run_sync
from tests.conftest import TEST_DATABASE_URL
from tests.pluggy_fakes import ENV, ITEM, NOW, FakePluggy, account, item, no_client

Conn = psycopg.Connection[DictRow]


def state(
    *,
    label: str = "Nubank",
    status: str = "UPDATED",
    consent: timedelta | None = timedelta(days=300),
    updated: timedelta | None = timedelta(hours=5),
) -> ItemState:
    return ItemState(
        id=ITEM,
        label=label,
        status=status,
        execution_status=None,
        error_code=None,
        last_updated_at=NOW - updated if updated is not None else None,
        consent_expires_at=NOW + consent if consent is not None else None,
        checked_at=NOW,
    )


class TestLines:
    def test_a_healthy_connection_says_nothing(self) -> None:
        assert connection_alerts([state()], NOW, fresh=True) == []

    def test_consent_29_days_away(self) -> None:
        assert connection_alerts([state(consent=timedelta(days=29))], NOW, fresh=True) == [
            "Consentimento de Nubank vence em 29 dias (2026-12-26): renove em meu.pluggy.ai"
        ]

    def test_consent_31_days_away_is_fine(self) -> None:
        assert connection_alerts([state(consent=timedelta(days=31))], NOW, fresh=True) == []

    def test_consent_tomorrow_and_today(self) -> None:
        assert connection_alerts([state(consent=timedelta(days=1))], NOW, fresh=False) == [
            "Consentimento de Nubank vence em 1 dia (2026-11-28): renove em meu.pluggy.ai"
        ]
        assert connection_alerts([state(consent=timedelta(hours=3))], NOW, fresh=False) == [
            "Consentimento de Nubank vence hoje (2026-11-27): renove em meu.pluggy.ai"
        ]

    def test_expired_consent_says_it_all(self) -> None:
        expired = state(consent=timedelta(days=-2), status="LOGIN_ERROR", updated=timedelta(days=5))
        assert connection_alerts([expired], NOW, fresh=True) == [
            "Consentimento de Nubank venceu em 2026-11-25: renove em meu.pluggy.ai"
        ]

    @pytest.mark.parametrize("status", ["LOGIN_ERROR", "OUTDATED", "WAITING_USER_INPUT"])
    def test_an_error(self, status: str) -> None:
        assert connection_alerts([state(status=status)], NOW, fresh=False) == [
            f"Conexão Nubank com erro ({status}): reconecte em meu.pluggy.ai"
        ]

    def test_49_hours_without_updating_on_a_fresh_reading(self) -> None:
        assert connection_alerts([state(updated=timedelta(hours=49))], NOW, fresh=True) == [
            "Conexão Nubank sem atualizar desde 2026-11-25 07:00"
        ]

    def test_47_hours_is_fine(self) -> None:
        assert connection_alerts([state(updated=timedelta(hours=47))], NOW, fresh=True) == []

    def test_an_old_reading_says_nothing_about_a_stop(self) -> None:
        assert connection_alerts([state(updated=timedelta(hours=49))], NOW, fresh=False) == []

    def test_error_beats_stop(self) -> None:
        assert connection_alerts([state(status="OUTDATED", updated=timedelta(days=4))], NOW, fresh=True) == [
            "Conexão Nubank com erro (OUTDATED): reconecte em meu.pluggy.ai"
        ]

    def test_consent_then_state_per_connection_in_order(self) -> None:
        items = [
            state(label="A", consent=timedelta(days=10), status="LOGIN_ERROR"),
            state(label="B", updated=timedelta(hours=50)),
        ]
        assert connection_alerts(items, NOW, fresh=True) == [
            "Consentimento de A vence em 10 dias (2026-12-07): renove em meu.pluggy.ai",
            "Conexão A com erro (LOGIN_ERROR): reconecte em meu.pluggy.ai",
            "Conexão B sem atualizar desde 2026-11-25 06:00",
        ]


def connect() -> Conn:
    return get_connection(TEST_DATABASE_URL)


@pytest.fixture
def pluggy() -> FakePluggy:
    fake = FakePluggy()
    fake.add_item(item(), account("acc-card", name="Cartão"))
    return fake


class TestCheckConnections:
    def test_nothing_configured_nothing_stored_reads_nothing(self, conn: Conn) -> None:
        assert check_spending(NOW, env={}, connect=connect, client_factory=no_client).alerts == []
        assert check_connections(NOW, config=None, connect=connect, client_factory=no_client) == []

    def test_with_credentials_the_states_are_read_again(self, conn: Conn, pluggy: FakePluggy) -> None:
        pluggy.items[ITEM] = item(last_updated_at=NOW - timedelta(hours=60))
        alerts = check_connections(NOW, config=load_config(ENV), connect=connect, client_factory=pluggy.factory())
        assert alerts == ["Conexão MeuPluggy sem atualizar desde 2026-11-24 20:00"]
        assert pluggy.requests("get_item") == [("get_item", ITEM)]

    def test_without_credentials_only_the_stored_states(self, conn: Conn, pluggy: FakePluggy) -> None:
        pluggy.items[ITEM] = item(consent_expires_at=NOW + timedelta(days=5), last_updated_at=NOW - timedelta(days=9))
        run_sync(conn, pluggy, [ITEM], now=NOW)
        assert check_spending(NOW, env={}, connect=connect, client_factory=no_client).alerts == [
            "Consentimento de Cartão vence em 5 dias (2026-12-02): renove em meu.pluggy.ai"
        ]

    def test_after_a_sync_no_second_reading(self, conn: Conn, pluggy: FakePluggy) -> None:
        pluggy.items[ITEM] = item(status="OUTDATED")
        result = check_spending(NOW, env=ENV, connect=connect, client_factory=pluggy.factory())
        assert result.alerts == ["Conexão Cartão com erro (OUTDATED): reconecte em meu.pluggy.ai"]
        assert result.errors == []
        assert len(pluggy.requests("get_item")) == 1  # o do sync

    def test_a_recent_sync_reads_the_states_only(self, conn: Conn, pluggy: FakePluggy) -> None:
        set_value(conn, SPENDING_LAST_SYNC_AT, NOW - timedelta(hours=2))
        result = check_spending(NOW, env=ENV, connect=connect, client_factory=pluggy.factory())
        assert result == check_spending(NOW, env=ENV, connect=connect, client_factory=pluggy.factory())
        assert pluggy.requests("list_transactions") == []
        assert len(pluggy.requests("get_item")) == 2

    def test_a_broken_configuration_is_an_error(self, conn: Conn) -> None:
        result = check_spending(NOW, env={"PLUGGY_ITEM_IDS": ITEM}, connect=connect, client_factory=no_client)
        assert result.errors == [
            "gastos não sincronizados (faltam PLUGGY_CLIENT_ID, PLUGGY_CLIENT_SECRET no ambiente (defina ou rode com op run))"
        ]
        assert result.alerts == []

    def test_a_failing_sync_is_said_once(self, conn: Conn, pluggy: FakePluggy) -> None:
        pluggy.fail[f"get_item:{ITEM}"] = NetworkError("pluggy", "sem rede")
        result = check_spending(NOW, env=ENV, connect=connect, client_factory=pluggy.factory())
        assert len(result.errors) == 1
        assert result.errors[0].startswith("gastos não sincronizados (conexão ")

    def test_a_failing_reading_without_sync_raises(self, conn: Conn, pluggy: FakePluggy) -> None:
        set_value(conn, SPENDING_LAST_SYNC_AT, NOW - timedelta(hours=2))
        pluggy.fail[f"get_item:{ITEM}"] = NetworkError("pluggy", "sem rede")
        with pytest.raises(NetworkError):
            check_spending(NOW, env=ENV, connect=connect, client_factory=pluggy.factory())

    def test_items_are_named_after_their_accounts(self, conn: Conn, pluggy: FakePluggy) -> None:
        run_sync(conn, pluggy, [ITEM], now=NOW)
        assert [entry.label for entry in repository.item_states(conn)] == ["Cartão"]
