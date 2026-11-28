"""Tests for ``bogle spending`` (issue #114): sync, status and the automatic sync before them;
``mark`` (#115); ``categorize``, ``groups`` and ``rules`` (#116).

Pluggy is always the fake of :mod:`tests.pluggy_fakes`, injected by swapping the
entry points the CLI module calls; the end-to-end runs go through the real
executable with the ``PLUGGY_*`` variables empty (see ``conftest``).
"""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import DictRow
from typer.testing import CliRunner, Result

from bogle import cli as cli_mod
from bogle.cli import app
from bogle.cli import spending as spending_cli
from bogle.data.pluggy import parse_category
from bogle.domain.errors import NetworkError, ValidationError
from bogle.settings import SPENDING_LAST_SYNC_AT, set_value
from bogle.spending.models import AutoSync, TransactionKind
from bogle.spending.sync import sync_if_due, sync_now
from tests.pluggy_fakes import ENV, ITEM, FakePluggy, account, category_tree, item, no_client, tx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BOGLE_BIN = PROJECT_ROOT / ".venv" / "bin" / "bogle"

Conn = psycopg.Connection[DictRow]


@pytest.fixture
def pluggy(monkeypatch: pytest.MonkeyPatch) -> FakePluggy:
    """A fake Pluggy behind both entry points of the CLI, with the variables set."""
    fake = FakePluggy()
    today = date.today()
    fake.add_item(
        item(consent_expires_at=datetime.now(UTC) + timedelta(days=300)),
        account("acc-card", name="Cartão"),
        account("acc-bank", kind="BANK", number="12345-6", name="Conta"),
    )
    fake.set_transactions(
        "acc-card",
        tx("c1", "acc-card", date(2026, 3, 5), "50"),
        tx("c2", "acc-card", today - timedelta(days=1), "30"),
        tx("p1", "acc-card", today + timedelta(days=60), "100", status="PENDING"),
    )
    fake.set_transactions("acc-bank", tx("b1", "acc-bank", date(2026, 1, 10), "-20"))
    monkeypatch.setattr(
        spending_cli, "sync_now", lambda full=False: sync_now(full=full, env=ENV, client_factory=fake.factory())
    )
    monkeypatch.setattr(spending_cli, "sync_if_due", lambda: sync_if_due(env=ENV, client_factory=fake.factory()))
    return fake


def spending(*args: str, input: str | None = None) -> Result:
    return CliRunner().invoke(app, ["spending", *args], input=input)


class TestSync:
    def test_shows_each_account_with_its_freshness_and_coverage(self, conn: Conn, pluggy: FakePluggy) -> None:
        result = spending("sync")
        assert result.exit_code == 0, result.output
        out = result.stdout
        assert "Sync de " in out
        for header in ("Conta", "Novas", "Atualizadas", "Removidas", "Última transação", "Desde"):
            assert header in out
        assert "Cartão" in out
        assert (date.today() - timedelta(days=1)).isoformat() in out  # a ultima transacao, nao a parcela futura
        assert "2026-03-05" in out
        assert "2026-01-10" in out
        assert "Atenção" not in out

    def test_a_second_run_inserts_nothing(self, conn: Conn, pluggy: FakePluggy) -> None:
        spending("sync")
        result = spending("sync")
        assert result.exit_code == 0, result.output
        card = next(line for line in result.stdout.splitlines() if "Cartão" in line)
        assert card.split("│")[2].strip() == "0"

    def test_full_asks_for_everything(self, conn: Conn, pluggy: FakePluggy) -> None:
        spending("sync")
        pluggy.calls.clear()
        assert spending("sync", "--full").exit_code == 0
        assert {call[2:] for call in pluggy.requests("list_transactions")} == {(None, None)}

    def test_a_failure_is_listed_and_exits_one(self, conn: Conn, pluggy: FakePluggy) -> None:
        pluggy.fail["list_transactions:acc-card"] = NetworkError("pluggy", "sem rede")
        result = spending("sync")
        assert result.exit_code == 1
        assert "Atenção:" in result.stdout
        assert "Conta Cartão: Falha de rede ao acessar pluggy" in result.stdout

    def test_sync_does_not_run_the_automatic_sync_first(self, conn: Conn, pluggy: FakePluggy) -> None:
        result = spending("sync")
        assert result.exit_code == 0
        assert len(pluggy.requests("factory")) == 1


class TestStatus:
    def test_nothing_yet(self, conn: Conn, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(spending_cli, "sync_if_due", lambda: AutoSync(ran=False))
        result = spending("status")
        assert result.exit_code == 0, result.output
        assert result.stdout == f"{spending_cli.NO_ACCOUNTS}\n"

    def test_each_account_and_connection(self, conn: Conn, pluggy: FakePluggy) -> None:
        result = spending("status")  # o sync automatico roda antes: nunca houve sync
        assert result.exit_code == 0, result.output
        out = result.stdout
        for header in ("Último sync", "Última transação", "Desde", "Conexões", "UPDATED", "Consentimento até"):
            assert header in out
        assert "2026-03-05" in out
        assert len(pluggy.requests("list_transactions")) == 2

    def test_a_recent_sync_does_not_call_the_api(self, conn: Conn, pluggy: FakePluggy) -> None:
        spending("sync")
        pluggy.calls.clear()
        result = spending("status")
        assert result.exit_code == 0, result.output
        assert pluggy.calls == []
        assert result.stderr == ""

    def test_past_24_hours_it_syncs_first(self, conn: Conn, pluggy: FakePluggy) -> None:
        spending("sync")
        set_value(conn, SPENDING_LAST_SYNC_AT, datetime.now(UTC) - timedelta(hours=25))
        pluggy.calls.clear()
        assert spending("status").exit_code == 0
        assert len(pluggy.requests("list_transactions")) == 2

    def test_a_failing_automatic_sync_warns_and_shows_what_is_stored(self, conn: Conn, pluggy: FakePluggy) -> None:
        spending("sync")
        set_value(conn, SPENDING_LAST_SYNC_AT, datetime(2026, 11, 25, 11, tzinfo=UTC))
        pluggy.fail[f"get_item:{ITEM}"] = NetworkError("pluggy", "sem rede")
        result = spending("status")
        assert result.exit_code == 0, result.output
        assert result.stderr.startswith("aviso: gastos não sincronizados (conexão Cartão, Conta: Falha de rede")
        assert result.stderr.rstrip().endswith("dados de 25/11 08:00")
        assert "Cartão" in result.stdout

    def test_pending_duplicates_are_counted(self, conn: Conn, pluggy: FakePluggy) -> None:
        spending("sync")
        rows = conn.execute("SELECT id FROM spending.transactions ORDER BY id LIMIT 2").fetchall()
        conn.execute(
            "INSERT INTO spending.duplicate_suggestions (transaction_id, duplicate_of_id) VALUES (%s, %s)",
            (rows[1]["id"], rows[0]["id"]),
        )
        result = spending("status")
        assert "1 duplicata provável pendente" in result.stdout

    def test_help_does_not_sync(self, conn: Conn, monkeypatch: pytest.MonkeyPatch) -> None:
        def never() -> AutoSync:
            raise AssertionError("o --help nao sincroniza")

        monkeypatch.setattr(spending_cli, "sync_if_due", never)
        result = spending("status", "--help")
        assert result.exit_code == 0, result.output


class TestMark:
    """``bogle spending mark`` (issue #115): the user's kind, over the rules."""

    def bank_line(self, conn: Conn) -> int:
        row = conn.execute("SELECT id FROM spending.transactions WHERE pluggy_id = 'b1'").fetchone()
        assert row is not None
        return row["id"]

    def test_marks_a_line_and_says_what_the_rule_was(self, conn: Conn, pluggy: FakePluggy) -> None:
        spending("sync")
        line = self.bank_line(conn)
        result = spending("mark", str(line), "ignorada")
        assert result.exit_code == 0, result.output
        assert result.stdout == (
            f"Transação {line} (Conta, 2026-01-10, Compra, -20,00): ignorada, à mão; "
            "pela regra seria gasto (débito na conta)\n"
        )
        row = conn.execute("SELECT kind FROM spending.transactions WHERE id = %s", (line,)).fetchone()
        assert row == {"kind": "ignorada"}

    def test_auto_hands_it_back(self, conn: Conn, pluggy: FakePluggy) -> None:
        spending("sync")
        line = self.bank_line(conn)
        spending("mark", str(line), "ignorada")
        result = spending("mark", str(line), "--auto")
        assert result.exit_code == 0, result.output
        assert result.stdout.endswith(": gasto (débito na conta), pela regra\n")

    def test_an_unknown_kind_lists_the_valid_ones(self, conn: Conn, pluggy: FakePluggy) -> None:
        spending("sync")
        result = spending("mark", str(self.bank_line(conn)), "transferencia")
        assert isinstance(result.exception, ValidationError)
        assert "Tipo 'transferencia' inválido. Use um destes: gasto, receita" in str(result.exception)

    @pytest.mark.parametrize("args", [(), ("ignorada", "--auto")])
    def test_needs_exactly_one_of_a_kind_or_auto(self, conn: Conn, pluggy: FakePluggy, args: tuple[str, ...]) -> None:
        result = spending("mark", "1", *args)
        assert result.exit_code == 2
        assert "Informe um tipo" in result.stderr
        assert pluggy.calls == []

    def test_an_unknown_transaction(self, conn: Conn, pluggy: FakePluggy) -> None:
        result = spending("mark", "999999", "gasto")
        assert isinstance(result.exception, ValidationError)
        assert str(result.exception) == "Transação 999999 não encontrada."

    def test_the_description_is_printed_without_control_characters(self) -> None:
        """The description comes from Pluggy: an escape or a line break must not reach the terminal."""
        entry = TransactionKind(
            id=7,
            account="Conta",
            local_date=date(2026, 1, 10),
            description="Compra\x1b[2J\nTransação 8 (Conta): falsa\r\x07",
            amount=Decimal("-20"),
            kind="gasto",
            auto_kind="gasto",
            auto_reason="débito na conta",
            manual_kind=None,
        )
        shown = spending_cli._marked(entry)  # pyright: ignore[reportPrivateUsage]
        assert shown.isprintable()
        assert "(Conta, 2026-01-10, Compra[2JTransação 8 (Conta): falsa, " in shown


IFOOD = "14.380.200/0001-21"


@pytest.fixture
def tree(pluggy: FakePluggy) -> FakePluggy:
    """The CLI's fake with Pluggy's whole category tree and two lines of one merchant."""
    pluggy.categories = category_tree()
    pluggy.set_transactions(
        "acc-card",
        *pluggy.transactions["acc-card"],
        tx(
            "if1",
            "acc-card",
            date(2026, 10, 5),
            "45.90",
            description="IFOOD *Restaurante",
            category=("11020000", "Food delivery"),
            merchant_cnpj=IFOOD,
        ),
        tx(
            "if2",
            "acc-card",
            date(2026, 10, 12),
            "80",
            description="IFOOD *Mercado",
            category=("11020000", "Food delivery"),
            merchant_cnpj=IFOOD,
        ),
    )
    spending("sync")
    return pluggy


def line_id(conn: Conn, pluggy_id: str) -> int:
    row = conn.execute("SELECT id FROM spending.transactions WHERE pluggy_id = %s", (pluggy_id,)).fetchone()
    assert row is not None
    return row["id"]


def rule_ids(conn: Conn) -> list[int]:
    return [row["id"] for row in conn.execute("SELECT id FROM spending.category_rules ORDER BY id").fetchall()]


def rule_count(conn: Conn) -> int:
    row = conn.execute("SELECT count(*) AS n FROM spending.category_rules").fetchone()
    assert row is not None
    return row["n"]


class TestCategorize:
    """``bogle spending categorize`` (issue #116): the user's category, over the rule and Pluggy."""

    def test_sets_the_category_and_says_what_it_was(self, conn: Conn, tree: FakePluggy) -> None:
        line = line_id(conn, "if1")
        result = spending("categorize", str(line), "restaurantes, bares e lanchonetes")
        assert result.exit_code == 0, result.output
        assert result.stdout == (
            f"Transação {line} (Cartão, 2026-10-05, IFOOD *Restaurante, +45,90): "
            "Restaurantes, bares e lanchonetes (Alimentação), à mão; sem ela: Delivery de alimentos, da Pluggy\n"
        )

    def test_auto_hands_it_back(self, conn: Conn, tree: FakePluggy) -> None:
        line = line_id(conn, "if1")
        spending("categorize", str(line), "Lazer")
        result = spending("categorize", str(line), "--auto")
        assert result.exit_code == 0, result.output
        assert result.stdout.endswith(": Delivery de alimentos, da Pluggy (Alimentação)\n")

    def test_an_unknown_category(self, conn: Conn, tree: FakePluggy) -> None:
        result = spending("categorize", str(line_id(conn, "if1")), "Restaurante")
        assert isinstance(result.exception, ValidationError)
        assert str(result.exception).startswith("Categoria 'Restaurante' não encontrada.")

    @pytest.mark.parametrize("args", [(), ("Lazer", "--auto"), ("--auto", "--rule")])
    def test_needs_exactly_one_of_a_category_or_auto(
        self, conn: Conn, pluggy: FakePluggy, args: tuple[str, ...]
    ) -> None:
        result = spending("categorize", "1", *args)
        assert result.exit_code == 2
        assert result.stderr
        assert pluggy.calls == []

    def test_rule_options_need_rule(self, conn: Conn, pluggy: FakePluggy) -> None:
        result = spending("categorize", "1", "Lazer", "--text", "ifood")
        assert result.exit_code == 2
        assert "só valem com --rule" in result.stderr

    def test_a_rule_shows_its_preview_and_is_created_on_yes(self, conn: Conn, tree: FakePluggy) -> None:
        line = line_id(conn, "if1")
        result = spending("categorize", str(line), "11010000", "--rule", input="s\n")
        assert result.exit_code == 0, result.output
        out = result.stdout
        assert "Regra: documento 14.380.200/0001-21 -> Restaurantes, bares e lanchonetes\n" in out
        assert "Muda 2 transações já gravadas.\n" in out
        assert "Criar a regra? [s/N]" in out
        assert f"Regra {rule_ids(conn)[0]} criada; 2 transações mudaram.\n" in out
        rows = conn.execute(
            "SELECT pluggy_id, effective_category_id, manual_category_id FROM spending.transactions "
            "WHERE pluggy_id IN ('if1', 'if2') ORDER BY pluggy_id"
        ).fetchall()
        assert [tuple(row.values()) for row in rows] == [("if1", "11010000", "11010000"), ("if2", "11010000", None)]

    def test_without_confirmation_nothing_changes(self, conn: Conn, tree: FakePluggy) -> None:
        line = line_id(conn, "if1")
        result = spending("categorize", str(line), "11010000", "--rule", "--text", "mercado", input="n\n")
        assert result.exit_code == 1
        assert 'descrição contém "mercado"' in result.stdout
        assert "Muda 1 transação já gravada." in result.stdout
        assert "nada foi alterado." in result.stderr
        assert rule_count(conn) == 0
        row = conn.execute("SELECT manual_category_id FROM spending.transactions WHERE id = %s", (line,)).fetchone()
        assert row == {"manual_category_id": None}

    def test_yes_skips_the_question(self, conn: Conn, tree: FakePluggy) -> None:
        result = spending(
            "categorize", str(line_id(conn, "if2")), "Supermercado", "--rule", "--text", "mercado", "--max", "100", "-y"
        )
        assert result.exit_code == 0, result.output
        assert "Criar a regra?" not in result.stdout
        assert f"Regra {rule_ids(conn)[0]} criada; 1 transação mudou." in result.stdout

    def test_a_negative_bound_is_refused(self, conn: Conn, tree: FakePluggy) -> None:
        result = spending("categorize", str(line_id(conn, "if2")), "Supermercado", "--rule", "--min", "-5", "-y")
        assert isinstance(result.exception, ValidationError)
        assert rule_count(conn) == 0


class TestGroupsCommand:
    def test_lists_the_groups_and_warns_of_the_unmapped(self, conn: Conn, tree: FakePluggy) -> None:
        tree.categories.append(
            parse_category({"id": "30000000", "description": "Brand new", "descriptionTranslated": "Novidade"})
        )
        spending("sync")
        result = spending("groups")
        assert result.exit_code == 0, result.output
        out = result.stdout
        for text in ("Grupos", "Natureza", "Moradia", "fixo", "Outros (reserva)", "Supermercado, Alimentos e bebidas"):
            assert text in out
        assert "1. 1 categoria sem grupo (mapeie com 'bogle spending groups map" in out
        assert "Novidade (30000000)" in out

    def test_all_lists_every_category(self, conn: Conn, tree: FakePluggy) -> None:
        result = spending("groups", "--all")
        assert result.exit_code == 0, result.output
        assert "200300000" in result.stdout
        assert "Atenção" not in result.stdout

    def test_create_needs_a_nature(self, conn: Conn, tree: FakePluggy) -> None:
        result = spending("groups", "create", "Pets")
        assert result.exit_code == 2
        assert "Missing argument" in result.stderr
        result = spending("groups", "create", "Pets", "mensal")
        assert isinstance(result.exception, ValidationError)
        result = spending("groups", "create", "Pets", "Variável")
        assert result.exit_code == 0, result.output
        assert result.stdout == "Grupo Pets criado (variavel).\n"

    def test_rename_nature_and_map(self, conn: Conn, tree: FakePluggy) -> None:
        assert spending("groups", "rename", "outros", "Diversos").stdout == "Grupo Outros agora se chama Diversos.\n"
        assert spending("groups", "nature", "lazer", "fixo").stdout == "Grupo Lazer: fixo.\n"
        result = spending("groups", "map", "Pharmacy", "Compras")
        assert result.stdout == "Categoria Farmácia (18020000) no grupo Compras.\n"

    def test_remove_needs_a_destination(self, conn: Conn, tree: FakePluggy) -> None:
        result = spending("groups", "remove", "Lazer")
        assert result.exit_code == 2
        assert "--to" in result.stderr
        result = spending("groups", "remove", "Lazer", "--to", "Outros")
        assert result.exit_code == 0, result.output
        assert result.stdout == "Grupo Lazer removido; 5 categorias foram para Outros.\n"

    def test_the_reserve_group_stays(self, conn: Conn, tree: FakePluggy) -> None:
        result = spending("groups", "remove", "Outros", "--to", "Lazer")
        assert isinstance(result.exception, ValidationError)

    def test_help_does_not_sync(self, conn: Conn, monkeypatch: pytest.MonkeyPatch) -> None:
        def never() -> AutoSync:
            raise AssertionError("o --help nao sincroniza")

        monkeypatch.setattr(spending_cli, "sync_if_due", never)
        for args in (("groups", "--help"), ("groups", "create", "--help"), ("rules", "undo", "--help")):
            result = spending(*args)
            assert result.exit_code == 0, result.output


class TestRulesCommand:
    def test_nothing_yet(self, conn: Conn, tree: FakePluggy) -> None:
        result = spending("rules")
        assert result.stdout == f"{spending_cli.NO_RULES}\n"

    def test_lists_removes_and_undoes(self, conn: Conn, tree: FakePluggy) -> None:
        line = line_id(conn, "if1")
        spending("categorize", str(line), "11010000", "--rule", "-y")
        spending("categorize", str(line_id(conn, "if2")), "Supermercado", "--rule", "--text", "mercado", "-y")
        first, second = rule_ids(conn)
        assert [rule.id for rule in spending_cli.list_rules(conn)] == [second, first]
        listed = spending("rules")
        assert listed.exit_code == 0, listed.output
        for text in ("Regras (a mais nova vence)", "Condições", "Transações", "Supermercado", "mercado"):
            assert text in listed.stdout
        undone = spending("rules", "undo")
        assert undone.stdout == f"Regra {second} desfeita e removida; 1 transação voltou ao que era.\n"
        removed = spending("rules", "remove", str(first))
        assert removed.stdout == f"Regra {first} removida; 2 transações voltam a outra regra ou à Pluggy.\n"
        assert rule_count(conn) == 0
        nothing = spending("rules", "undo")
        assert isinstance(nothing.exception, ValidationError)


class TestPortfolioNoticesStayOut:
    def test_the_portfolio_reminders_are_not_shown(self, conn: Conn, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            cli_mod, "_read_preferences", lambda: (".", "ciclo vencido", "rebalanceamento anual de 2061 pendente")
        )
        monkeypatch.setattr(spending_cli, "sync_if_due", lambda: AutoSync(ran=False))
        result = spending("status")
        assert result.exit_code == 0, result.output
        assert "ciclo vencido" not in result.output
        assert "rebalanceamento anual" not in result.output

    def test_other_commands_still_show_them(self, conn: Conn, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cli_mod, "_read_preferences", lambda: (".", "ciclo vencido", None))
        result = CliRunner().invoke(app, ["list"])
        assert "aviso: ciclo vencido" in result.stderr


class TestDefaultFactoryIsNeverReachedInTests:
    def test_the_variables_are_empty(self) -> None:
        for name in ("PLUGGY_CLIENT_ID", "PLUGGY_CLIENT_SECRET", "PLUGGY_ITEM_IDS"):
            assert os.environ.get(name) == ""

    def test_without_configuration_the_automatic_sync_never_builds_a_client(self, conn: Conn) -> None:
        auto = sync_if_due(client_factory=no_client)
        assert auto.notice is not None and "faltam PLUGGY_CLIENT_ID" in auto.notice


# ---- De ponta a ponta, pelo executavel, sem nenhuma variavel da Pluggy.


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(BOGLE_BIN), *args],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        cwd=str(PROJECT_ROOT),
        check=False,
    )


class TestWithoutConfiguration:
    def test_sync_says_what_is_missing_without_a_traceback(self, conn: Conn) -> None:
        result = run_cli("spending", "sync")
        assert result.returncode == 1
        assert result.stderr.startswith(
            "erro: faltam PLUGGY_CLIENT_ID, PLUGGY_CLIENT_SECRET, PLUGGY_ITEM_IDS no ambiente"
        )
        assert "Traceback" not in result.stderr

    def test_status_warns_and_goes_on(self, conn: Conn) -> None:
        result = run_cli("spending", "status")
        assert result.returncode == 0, result.stderr
        assert "aviso: gastos não sincronizados (faltam PLUGGY_CLIENT_ID" in result.stderr
        assert result.stdout == f"{spending_cli.NO_ACCOUNTS}\n"

    def test_the_rest_of_bogle_is_untouched(self, conn: Conn) -> None:
        listed = run_cli("list")
        assert listed.returncode == 0, listed.stderr
        assert "PLUGGY" not in listed.stderr
        checked = run_cli("check")
        assert checked.returncode == 0, checked.stderr
        assert checked.stdout == "Nenhuma pendência.\n"
