"""Tests for ``bogle spending`` (issue #114): sync, status and the automatic sync before them;
``mark`` (#115); ``categorize``, ``groups`` and ``rules`` (#116); ``summary`` and ``trend`` (#117);
``bills`` and ``installments`` (#118).

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
from typing import Any

import psycopg
import pytest
from psycopg.rows import DictRow
from rich.console import Console
from rich.text import Text
from typer.testing import CliRunner, Result

from bogle import cli as cli_mod
from bogle import format as fmt
from bogle.cli import app
from bogle.cli import spending as spending_cli
from bogle.data.pluggy import parse_category
from bogle.domain.errors import NetworkError, ValidationError
from bogle.settings import SPENDING_LAST_SYNC_AT, set_value
from bogle.spending.analysis import CategorySpending, GroupSpending, MonthSummary, YearBefore
from bogle.spending.models import AutoSync, CategoryGroup, TransactionKind
from bogle.spending.sync import sync_if_due, sync_now
from tests.pluggy_fakes import ENV, ITEM, FakePluggy, account, bill, category_tree, item, no_client, tx
from tests.test_spending_bills import history as card_history

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
        # No CI (GITHUB_ACTIONS) o Typer força cor e o Rich pinta o nome da opção.
        assert "Missing option '--to'" in Text.from_ansi(result.stderr).plain
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


# ---- summary e trend (#117)

TODAY = date(2026, 11, 20)
GROCERIES = ("10000000", "Groceries")


@pytest.fixture
def months(pluggy: FakePluggy, monkeypatch: pytest.MonkeyPatch) -> FakePluggy:
    """Card from August, account from October, and "today" on 2026-11-20.

    Each account is compared from the month after its first: the card from
    September, the account from November.
    """
    pluggy.categories = category_tree()

    def market(tx_id: str, day: date, amount: str, name: str = "Mercado Bom") -> Any:
        return tx(tx_id, "acc-card", day, amount, category=GROCERIES, business_name=name)

    pluggy.set_transactions(
        "acc-card",
        market("aug", date(2026, 8, 10), "200"),
        market("sep", date(2026, 9, 10), "200"),
        market("oct", date(2026, 10, 10), "300"),
        market("nov", date(2026, 11, 5), "240"),
        tx("other", "acc-card", date(2026, 11, 8), "100", description="Coisa", category=("99999999", "Other")),
    )
    pluggy.set_transactions(
        "acc-bank",
        tx("sal-oct", "acc-bank", date(2026, 10, 1), "5000", kind="CREDIT", category=("01010000", "Salary")),
        tx("sal-nov", "acc-bank", date(2026, 11, 1), "5000", kind="CREDIT", category=("01010000", "Salary")),
        tx("rent", "acc-bank", date(2026, 11, 15), "-1500", description="Aluguel", category=("17000000", "Housing")),
    )
    monkeypatch.setattr(spending_cli, "_today", lambda: TODAY)
    return pluggy


class TestSummary:
    def test_the_running_month(self, conn: Conn, months: FakePluggy) -> None:
        result = spending("summary")
        assert result.exit_code == 0, result.output
        out = result.stdout
        for text in (
            "Gastos de 2026-11 até o dia 20",
            "Média até o dia 20",
            "Alimentação",
            "Supermercado",
            "Moradia (fixo)",
            "1.840,00",
            "Estabelecimentos mais frequentes",
            "Mercado Bom",
        ):
            assert text in out, text
        assert (
            "O mês em uma conta: entrou 5.000,00; fixos 1.500,00; variável 340,00; sobra +3.160,00.\n"
            "Ritmo do variável: 340,00 até o dia 20, contra 250,00 na média de 2 meses até o mesmo dia (+36,00%).\n"
            "Em Outros (sem grupo próprio): 5,43% do gasto do mês.\n"
        ) in out
        assert "2025-11" not in out  # o mesmo mes de 2025 esta fora da cobertura

    def test_a_past_month(self, conn: Conn, months: FakePluggy) -> None:
        result = spending("summary", "--month", "2026-10")
        assert result.exit_code == 0, result.output
        out = result.stdout
        assert "Gastos de 2026-10" in out
        assert "Média de 1 mês" in out
        assert "+50,00%" in out  # 300 contra 200
        assert "Ritmo" not in out

    def test_an_account(self, conn: Conn, months: FakePluggy) -> None:
        result = spending("summary", "--account", "cartao")
        assert result.exit_code == 0, result.output
        assert "(Cartão)" in result.stdout
        assert "Moradia" not in result.stdout
        assert "entrou 0,00" in result.stdout

    def test_an_invalid_month_is_refused_before_the_sync(self, conn: Conn, months: FakePluggy) -> None:
        result = spending("summary", "--month", "2026-13")
        assert isinstance(result.exception, ValidationError)
        assert months.calls == []

    def test_a_month_out_of_the_coverage(self, conn: Conn, months: FakePluggy) -> None:
        result = spending("summary", "--month", "2026-07")
        assert isinstance(result.exception, ValidationError)
        assert str(result.exception) == "Mês 2026-07 fora da cobertura: os dados começam em 2026-08."

    def test_nothing_yet(self, conn: Conn, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(spending_cli, "sync_if_due", lambda: AutoSync(ran=False))
        for command in ("summary", "trend"):
            result = spending(command)
            assert result.exit_code == 0, result.output
            assert result.stdout == f"{spending_cli.NO_ACCOUNTS}\n"

    def test_pluggy_names_are_printed_without_control_characters(self, conn: Conn, months: FakePluggy) -> None:
        months.set_transactions(
            "acc-card",
            tx("evil", "acc-card", date(2026, 11, 3), "10", category=GROCERIES, business_name="Loja\x1b[2J\nFalsa"),
        )
        spending("sync")
        result = spending("summary")
        assert result.exit_code == 0, result.output
        assert "\x1b" not in result.stdout
        assert "Loja[2JFalsa" in result.stdout

    def test_the_same_month_a_year_before_and_a_group_of_one_category(self, capsys: pytest.CaptureFixture[str]) -> None:
        food = CategoryGroup(1, "Alimentação", "variavel")
        result = MonthSummary(
            month=date(2026, 3, 1),
            until=date(2026, 3, 10),
            partial=True,
            compared=(),
            accounts=(),
            groups=(
                GroupSpending(
                    food, Decimal("120"), None, (CategorySpending("1", "Supermercado", Decimal("120"), None),)
                ),
            ),
            total=Decimal("120"),
            total_average=None,
            income=Decimal("0"),
            fixed=Decimal("0"),
            variable=Decimal("120"),
            pace=None,
            fallback="Outros",
            fallback_value=Decimal("0"),
            last_year=YearBefore(Decimal("100"), Decimal("120")),
            merchants=(),
        )
        fmt.configure(",")
        spending_cli._render_summary(result, Console(width=120))  # pyright: ignore[reportPrivateUsage]
        out = capsys.readouterr().out
        assert "Alimentação · Supermercado" in out
        assert "Média" not in out
        assert "Mesmo mês de 2025 até o dia 10: 100,00 (agora +20,00%).\n" in out
        assert "Estabelecimentos" not in out

    def test_account_names_in_the_title_are_not_markup(self, capsys: pytest.CaptureFixture[str]) -> None:
        result = MonthSummary(
            month=date(2026, 3, 1),
            until=date(2026, 3, 31),
            partial=False,
            compared=(),
            accounts=("Conta [/bold]x", "Cartao [red]R[/red]"),
            groups=(),
            total=Decimal("0"),
            total_average=None,
            income=Decimal("0"),
            fixed=Decimal("0"),
            variable=Decimal("0"),
            pace=None,
            fallback="Outros",
            fallback_value=Decimal("0"),
            last_year=None,
            merchants=(),
        )
        spending_cli._render_summary(result, Console(width=120))  # pyright: ignore[reportPrivateUsage]
        title = " ".join(capsys.readouterr().out.split())  # a tabela vazia é estreita e quebra o título
        assert "Gastos de 2026-03 (Conta [/bold]x, Cartao [red]R[/red])" in title

    @pytest.mark.parametrize("month", ["0000-05", "0001-01", "1899-12", "3000-01", "9999-12"])
    def test_a_month_out_of_the_supported_years(self, conn: Conn, months: FakePluggy, month: str) -> None:
        result = spending("summary", "--month", month)
        assert isinstance(result.exception, ValidationError)
        assert "ano vai de 1900 a 2999" in str(result.exception)
        assert months.calls == []

    @pytest.mark.parametrize(
        "args",
        [
            ("summary", "--compare", "121"),
            ("summary", "--compare", "30000"),
            ("trend", "--months", "121"),
            ("trend", "--months", "30000"),
            ("trend", "--months", "999999999999"),
        ],
    )
    def test_too_many_months_is_a_usage_error(self, conn: Conn, months: FakePluggy, args: tuple[str, ...]) -> None:
        result = spending(*args)
        assert result.exit_code == 2
        assert months.calls == []

    def test_the_limits_still_work(self, conn: Conn, months: FakePluggy, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(spending_cli, "render_line_chart", lambda *args: None)
        assert spending("summary", "--compare", "120").exit_code == 0
        assert spending("trend", "--months", "120").exit_code == 0
        result = spending("summary", "--month", "1900-01", "--compare", "120")
        assert isinstance(result.exception, ValidationError)
        assert "fora da cobertura" in str(result.exception)
        result = spending("summary", "--month", "2999-12", "--compare", "120")
        assert isinstance(result.exception, ValidationError)
        assert "ainda não começou" in str(result.exception)

    def test_says_which_accounts_a_comparison_leaves_out(
        self, conn: Conn, pluggy: FakePluggy, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The review's case: card since 2025 at 300 a month, account since March 2026 with 1500 of rent."""
        pluggy.categories = category_tree()
        card_months = [date(2025, month, 10) for month in range(1, 13)] + [date(2026, m, 10) for m in range(1, 12)]
        pluggy.set_transactions(
            "acc-card",
            *(tx(f"c{day:%Y%m}", "acc-card", day, "300", category=GROCERIES) for day in card_months),
        )
        pluggy.set_transactions(
            "acc-bank",
            *(
                tx(f"r{month}", "acc-bank", date(2026, month, 5), "-1500", category=("17000000", "Housing"))
                for month in range(3, 12)
            ),
        )
        monkeypatch.setattr(spending_cli, "_today", lambda: date(2026, 12, 5))
        result = spending("summary", "--month", "2026-11")
        assert result.exit_code == 0, result.output
        assert "1.800,00" in result.stdout
        # Em 2025 so o cartao existia: a linha diz quem ficou de fora e compara as mesmas contas.
        assert "Mesmo mês de 2025 (sem Conta): 300,00, contra 300,00 agora (+0,00%).\n" in result.stdout
        assert "média de" not in result.stdout  # as duas contas tem os 3 meses
        result = spending("summary", "--month", "2026-11", "--compare", "12")
        assert result.exit_code == 0, result.output
        assert "Média de 12 meses" in result.stdout
        assert "Conta: média de 7 meses.\n" in result.stdout  # comparada de abril a outubro

    def test_left_out_account_names_are_filtered(self, capsys: pytest.CaptureFixture[str]) -> None:
        evil = "Conta\x1b[2J\n[red]X"
        result = MonthSummary(
            month=date(2026, 11, 1),
            until=date(2026, 11, 30),
            partial=False,
            compared=(date(2026, 10, 1), date(2026, 9, 1)),
            accounts=(),
            groups=(),
            total=Decimal("0"),
            total_average=Decimal("0"),
            income=Decimal("0"),
            fixed=Decimal("0"),
            variable=Decimal("0"),
            pace=None,
            fallback="Outros",
            fallback_value=Decimal("0"),
            last_year=YearBefore(Decimal("300"), Decimal("300"), (evil,)),
            merchants=(),
            shorter=((evil, 1),),
        )
        fmt.configure(",")
        spending_cli._render_summary(result, Console(width=120))  # pyright: ignore[reportPrivateUsage]
        out = capsys.readouterr().out
        assert "\x1b" not in out
        assert "Conta[2J[red]X: média de 1 mês.\n" in out
        assert "Mesmo mês de 2025 (sem Conta[2J[red]X): 300,00, contra 300,00 agora (+0,00%).\n" in out

    def test_help_does_not_sync(self, conn: Conn, monkeypatch: pytest.MonkeyPatch) -> None:
        def never() -> AutoSync:
            raise AssertionError("o --help nao sincroniza")

        monkeypatch.setattr(spending_cli, "sync_if_due", never)
        for command in ("summary", "trend"):
            result = spending(command, "--help")
            assert result.exit_code == 0, result.output


class TestTrend:
    def test_table_of_the_covered_months(self, conn: Conn, months: FakePluggy) -> None:
        result = spending("trend", "--no-chart")
        assert result.exit_code == 0, result.output
        out = result.stdout
        for text in (
            "Gastos por mês",
            "Fixos",
            "Variável",
            "2026-08 (sem Conta)",
            "2026-09 (sem Conta)",
            "2026-10 ",
            "2026-11 (até o dia 20)",
            "1.840,00",
        ):
            assert text in out, text
        assert "2026-07" not in out
        assert "2026-10 (sem" not in out

    def test_the_chart(self, conn: Conn, months: FakePluggy, monkeypatch: pytest.MonkeyPatch) -> None:
        drawn: list[tuple[Any, ...]] = []
        monkeypatch.setattr(spending_cli, "render_line_chart", lambda *args: drawn.append(args))
        result = spending("trend", "--months", "3")
        assert result.exit_code == 0, result.output
        assert drawn == [("Gastos por mês", ["2026-09", "2026-10", "2026-11"], [("Total", [200.0, 300.0, 1840.0])])]

    def test_a_group(self, conn: Conn, months: FakePluggy, monkeypatch: pytest.MonkeyPatch) -> None:
        drawn: list[tuple[Any, ...]] = []
        monkeypatch.setattr(spending_cli, "render_line_chart", lambda *args: drawn.append(args))
        result = spending("trend", "--group", "alimentacao", "--account", "Cartão")
        assert result.exit_code == 0, result.output
        assert "Gastos por mês: Alimentação" in result.stdout
        assert "Fixos" not in result.stdout
        assert drawn[0][0] == "Gastos por mês: Alimentação (Cartão)"
        assert drawn[0][2] == [("Alimentação", [200.0, 200.0, 300.0, 240.0])]

    def test_an_unknown_account(self, conn: Conn, months: FakePluggy) -> None:
        result = spending("trend", "--account", "poupanca")
        assert isinstance(result.exception, ValidationError)
        assert "ou o nome de uma conta: Cartão, Conta." in str(result.exception)


# ---- faturas e parcelas (#118)


@pytest.fixture
def cards(pluggy: FakePluggy, monkeypatch: pytest.MonkeyPatch) -> FakePluggy:
    """The card of :mod:`tests.test_spending_bills` (closes on the 3rd, due on the 10th), "today" on 2026-11-28."""
    pluggy.categories = category_tree()
    lines, bills = card_history()
    pluggy.set_transactions("acc-card", *lines)
    pluggy.set_bills("acc-card", *bills)
    pluggy.set_transactions(
        "acc-bank",
        *(
            tx(f"sal-{month}", "acc-bank", date(2026, month, 1), "5000", kind="CREDIT", category=("01010000", "Salary"))
            for month in (8, 9, 10, 11)
        ),
    )
    monkeypatch.setattr(spending_cli, "_today", lambda: date(2026, 11, 28))
    return pluggy


def bill_local_id(conn: Conn, pluggy_id: str) -> int:
    row = conn.execute("SELECT id FROM spending.bills WHERE pluggy_id = %s", (pluggy_id,)).fetchone()
    assert row is not None
    return row["id"]


class TestBills:
    def test_closed_bills_the_open_one_and_what_is_committed(self, conn: Conn, cards: FakePluggy) -> None:
        result = spending("bills")
        assert result.exit_code == 0, result.output
        out = result.stdout
        for text in (
            "Faturas de Cartão",
            "Vencimento",
            "Transações",
            "Diferença",
            "2026-10-10",
            "515,00",
            "200,00",
            "+315,00",
            "paga parcial",
            "vencida",
            "Parcelas a lançar por fatura",
            "2026-12 (aberta)",
            "Da receita média",
            "1,50%",
            "2027-04",
        ):
            assert text in out, text
        assert (
            "Fatura aberta de Cartão: 305,00 em 4 compras; fecha em 5 dias (2026-12-03), "
            "vence em 2026-12-10 (datas estimadas).\n"
        ) in out
        assert "Receita média dos 3 meses anteriores: 5.000,00.\n" in out
        november = bill_local_id(conn, "bill-nov")
        assert (
            f"1. Fatura 2026-11 de Cartão: diferença não identificada de +10,00; "
            f"detalhe em 'bogle spending bills {november}'"
        ) in " ".join(out.split())

    def test_one_bill_with_its_reconciliation(self, conn: Conn, cards: FakePluggy) -> None:
        result = spending("bills", "2026-10")
        assert result.exit_code == 0, result.output
        out = result.stdout
        assert out.startswith(
            "Fatura de Cartão com vencimento em 2026-10-10: paga parcial; fechou em 2026-10-03; 2 compras.\n"
        )
        for text in (
            "Gastos por grupo",
            "Compras",
            "500,00",
            "Geladeira",
            "1/3",
            "Conciliação",
            "Total da Pluggy",
            "Transações (3)",
            "pagamentos lançados (saldo anterior)",
            "IOF",
            "Pago em 2026-10-09",
        ):
            assert text in out, text
        assert "não identificado" not in out
        by_id = spending("bills", str(bill_local_id(conn, "bill-oct")))
        assert by_id.stdout == out

    def test_the_open_bill_and_a_forecast(self, conn: Conn, cards: FakePluggy) -> None:
        opened = spending("bills", "2026-12")
        assert opened.exit_code == 0, opened.output
        assert opened.stdout.startswith(
            "Fatura de Cartão com vencimento em 2026-12-10: aberta; fecha em 5 dias (2026-12-03); "
            "4 compras (datas estimadas).\n"
        )
        assert "Gastos por grupo" in opened.stdout
        assert "2026-12-02 │ Tênis" in opened.stdout  # a parcela a lancar ate o fechamento
        assert "Conciliação" not in opened.stdout
        forecast = spending("bills", "2027-01")
        assert forecast.exit_code == 0, forecast.output
        assert "prevista; fecha em 2027-01-03 (datas estimadas)." in forecast.stdout
        assert "Parcelas por grupo" in forecast.stdout
        assert "3/4" in forecast.stdout

    def test_two_cards(self, conn: Conn, cards: FakePluggy) -> None:
        black = account("acc-black", name="Black", number="1111")
        cards.add_item(item(), *cards.accounts[ITEM], black)
        cards.set_transactions(
            "acc-black",
            tx("k1", "acc-black", date(2026, 11, 2), "10", bill_id="black-nov"),
            tx("k2", "acc-black", date(2026, 12, 20), "40", status="PENDING", installment=2, installments=2),
        )
        cards.set_bills("acc-black", bill("black-nov", date(2026, 11, 15), "10", closing=date(2026, 11, 5)))
        spending("sync")
        result = spending("bills")
        assert result.exit_code == 0, result.output
        out = result.stdout
        assert "Faturas de Black" in out
        assert "Fatura aberta de Black: 0,00 em 0 compras; fecha em 7 dias (2026-12-05)" in out
        # Uma coluna por cartao e o total: a parcela do Black (20/12) fecha em 05/01, vence em 15/01.
        row = next(line for line in out.splitlines() if line.startswith("│ 2027-01"))
        assert [cell.strip() for cell in row.split("│")[1:-1]] == ["2027-01", "125,00", "40,00", "165,00", "3,30%"]
        ambiguous = spending("bills", "2026-11")
        assert isinstance(ambiguous.exception, ValidationError)
        assert "Escolha um com --account" in str(ambiguous.exception)
        chosen = spending("bills", "2026-11", "--account", "black")
        assert chosen.exit_code == 0, chosen.output
        assert chosen.stdout.startswith("Fatura de Black com vencimento em 2026-11-15: vencida;")

    @pytest.mark.parametrize("ref", ["2026-13", "abc", "0000-01"])
    def test_an_invalid_bill_is_refused_before_the_sync(self, conn: Conn, cards: FakePluggy, ref: str) -> None:
        result = spending("bills", ref)
        assert isinstance(result.exception, ValidationError)
        assert cards.calls == []

    @pytest.mark.parametrize("ref", ["99999999999999999999999999", "9223372036854775807", "2031-01"])
    def test_an_unknown_bill(self, conn: Conn, cards: FakePluggy, ref: str) -> None:
        result = spending("bills", ref)
        assert isinstance(result.exception, ValidationError)

    def test_only_cards(self, conn: Conn, cards: FakePluggy) -> None:
        for command in ("bills", "installments"):
            result = spending(command, "--account", "corrente")
            assert isinstance(result.exception, ValidationError)
            assert "não é um cartão" in str(result.exception)

    def test_nothing_yet(self, conn: Conn, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(spending_cli, "sync_if_due", lambda: AutoSync(ran=False))
        for command in ("bills", "installments"):
            result = spending(command)
            assert result.exit_code == 0, result.output
            assert result.stdout == f"{spending_cli.NO_ACCOUNTS}\n"

    def test_help_does_not_sync(self, conn: Conn, monkeypatch: pytest.MonkeyPatch) -> None:
        def never() -> AutoSync:
            raise AssertionError("o --help nao sincroniza")

        monkeypatch.setattr(spending_cli, "sync_if_due", never)
        for command in ("bills", "installments"):
            result = spending(command, "--help")
            assert result.exit_code == 0, result.output

    def test_pluggy_text_is_printed_without_control_characters_or_markup(self, conn: Conn, cards: FakePluggy) -> None:
        cards.accounts[ITEM][0] = account("acc-card", name="Cartão\x1b[2J [red]X")
        lines, bills = card_history()
        evil = tx(
            "evil",
            "acc-card",
            date(2026, 11, 26),
            "10",
            description="Loja\x1b[2J\n[bold]Falsa 1/2",
            installment=1,
            installments=2,
        )
        cards.set_transactions("acc-card", *lines, evil)
        cards.set_bills("acc-card", *bills)
        spending("sync")
        for args in (("bills",), ("bills", "2026-12"), ("bills", "2026-10"), ("installments",)):
            result = spending(*args)
            assert result.exit_code == 0, result.output
            assert "\x1b" not in result.stdout, args
        for args in (("bills",), ("bills", "2026-12"), ("bills", "2026-10")):
            assert "Cartão[2J [red]X" in " ".join(spending(*args).stdout.split()), args
        assert "Loja[2J[bold]Falsa" in spending("installments").stdout
        assert "Loja[2J[bold]Falsa" in spending("bills", "2026-12").stdout


class TestInstallmentsCommand:
    def test_the_running_purchases(self, conn: Conn, cards: FakePluggy) -> None:
        result = spending("installments")
        assert result.exit_code == 0, result.output
        out = result.stdout
        for text in ("Parcelas em andamento", "CURSO ONLINE", "2/10", "400,00", "2027-07", "Tênis", "1/4", "625,00"):
            assert text in out, text
        assert "Geladeira" not in out  # a ultima parcela ja foi lancada
        assert "Cartão" not in out.split("Parcelas em andamento")[1]  # um cartao so: sem a coluna

    def test_nothing_running(self, conn: Conn, pluggy: FakePluggy, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(spending_cli, "_today", lambda: date(2026, 11, 28))
        result = spending("installments")
        assert result.exit_code == 0, result.output
        assert result.stdout == "Nenhuma compra parcelada em andamento.\n"
