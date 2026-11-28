"""Tests for the month by group and category and the monthly trend (issue #117).

Through the sync and the test database, with Pluggy's whole category tree; the
rules the sums follow (coverage, the same-day cut, the fixed groups out of the
pace, the same month a year before) also over lines built by hand.

The data, with "today" on 2026-11-20 (the card from July, the account from
September; August has no line). An account is compared only from the month
after its first: the card from August, the account from October.

====== ============================== ===========================================
month  card                           account
====== ============================== ===========================================
07     supermarket 100 (10th)
09     supermarket 200 (10th) and     rent 1500 (5th)
       50 (25th); installment 1 100
10     supermarket 300 (10th), refund rent 1500 (5th), salary 5000, a line marked
       -30 (12th); installment 2 100; ignored (-77)
       a probable duplicate (44)
11     supermarket 240 (5th) and 60   rent 1500 (15th), salary 5000
       (18th); installment 3 100;
       "Outros" 100; a PENDING 999
       on the 25th
====== ============================== ===========================================
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from psycopg.rows import DictRow

from bogle.domain.errors import ValidationError
from bogle.spending.analysis import (
    Coverage,
    YearBefore,
    add_months,
    change,
    cut_end,
    month_summary,
    monthly_trend,
    parse_month,
    select_accounts,
    spending_trend,
    summarize,
)
from bogle.spending.categories import map_category, set_nature
from bogle.spending.classify import Kind
from bogle.spending.marking import set_kind
from bogle.spending.models import CategoryGroup, ExpenseLine, GroupNature, IncomeLine, StoredAccount
from bogle.spending.sync import run_sync
from tests.pluggy_fakes import ITEM, NOW, FakePluggy, account, category_tree, item, tx

Conn = psycopg.Connection[DictRow]

CARD = "acc-card"
BANK = "acc-bank"
TODAY = date(2026, 11, 20)

GROCERIES = ("10000000", "Groceries")
SHOPPING = ("08000000", "Shopping")
HOUSING = ("17000000", "Housing")
OTHER = ("99999999", "Other")
SALARY = ("01010000", "Salary")

D = Decimal


def market(tx_id: str, day: date, amount: str, **kw: Any) -> Any:
    return tx(
        tx_id, CARD, day, amount, description="MERCADO BOM 123", category=GROCERIES, business_name="Mercado Bom", **kw
    )


def installment(number: int, post: date, *, status: str = "POSTED") -> Any:
    """One of ten installments of 1000 bought on 2026-09-03: on the card, each counts on its ``billPostDate``."""
    return tx(
        f"inst-{number}",
        CARD,
        date(2026, 9, 3),
        "100",
        description="Loja Parcelada",
        category=SHOPPING,
        installment=number,
        post_date=post,
        status=status,
    )


def rent(tx_id: str, day: date) -> Any:
    return tx(tx_id, BANK, day, "-1500", description="Aluguel", category=HOUSING, at="15:00:00.000")


def salary(tx_id: str, day: date) -> Any:
    return tx(tx_id, BANK, day, "5000", description="Salario", category=SALARY, kind="CREDIT", at="15:00:00.000")


@pytest.fixture
def pluggy() -> FakePluggy:
    fake = FakePluggy()
    fake.categories = category_tree()
    fake.add_item(item(), account(CARD, name="Cartão"), account(BANK, kind="BANK", number="12345-6", name="Conta"))
    fake.set_transactions(
        CARD,
        market("jul", date(2026, 7, 10), "100"),
        market("sep", date(2026, 9, 10), "200"),
        market("sep-late", date(2026, 9, 25), "50"),
        market("oct", date(2026, 10, 10), "300"),
        market("oct-refund", date(2026, 10, 12), "-30", kind="CREDIT", operation="ESTORNO"),
        tx("oct-dup", CARD, date(2026, 10, 11), "44", description="Duplicada", category=GROCERIES),
        market("nov", date(2026, 11, 5), "240"),
        market("nov-2", date(2026, 11, 18), "60"),
        tx("nov-other", CARD, date(2026, 11, 8), "100", description="Coisa", category=OTHER),
        market("nov-future", date(2026, 11, 25), "999", status="PENDING"),
        installment(1, date(2026, 9, 15)),
        installment(2, date(2026, 10, 15)),
        installment(3, date(2026, 11, 15)),
        installment(4, date(2026, 12, 15), status="PENDING"),
        installment(5, date(2027, 1, 15), status="PENDING"),
    )
    fake.set_transactions(
        BANK,
        rent("rent-sep", date(2026, 9, 5)),
        rent("rent-oct", date(2026, 10, 5)),
        rent("rent-nov", date(2026, 11, 15)),
        salary("salary-oct", date(2026, 10, 1)),
        salary("salary-nov", date(2026, 11, 1)),
        tx("oct-ignored", BANK, date(2026, 10, 20), "-77", description="Ignorar", at="15:00:00.000"),
    )
    return fake


def local_id(conn: Conn, pluggy_id: str) -> int:
    row = conn.execute("SELECT id FROM spending.transactions WHERE pluggy_id = %s", (pluggy_id,)).fetchone()
    assert row is not None
    return row["id"]


@pytest.fixture
def synced(conn: Conn, pluggy: FakePluggy) -> FakePluggy:
    report = run_sync(conn, pluggy, (ITEM,), now=NOW, full=True)
    assert report.failures == []
    set_kind(conn, local_id(conn, "oct-ignored"), Kind.IGNORED, now=NOW)
    conn.execute(
        "INSERT INTO spending.duplicate_suggestions (transaction_id, duplicate_of_id) VALUES (%s, %s)",
        (local_id(conn, "oct-dup"), local_id(conn, "oct")),
    )
    conn.commit()
    pluggy.calls.clear()
    return pluggy


def groups_of(summary: Any) -> dict[str, tuple[Decimal, Decimal | None]]:
    return {entry.group.name: (entry.value, entry.average) for entry in summary.groups}


def thirds(total: str) -> Decimal:
    return D(total) / 3


class TestTotals:
    """Criterion 1: the totals are the sum of the lines that count as spending, refunds netted."""

    def test_the_total_is_the_sum_of_spending_and_refunds(self, conn: Conn, synced: FakePluggy) -> None:
        october = month_summary(conn, today=TODAY, month=date(2026, 10, 1))
        # 300 - 30 de estorno + 100 de parcela + 1500 de aluguel; fora: a duplicata, a ignorada, o salario.
        assert october.total == D("1870")
        row = conn.execute(
            """
            SELECT sum(CASE WHEN a.type = 'CREDIT' THEN t.amount ELSE -t.amount END) AS total
            FROM spending.transactions t JOIN spending.accounts a ON a.id = t.account_id
            WHERE t.kind IN ('gasto', 'abatimento') AND t.local_date BETWEEN '2026-10-01' AND '2026-10-31'
              AND t.pluggy_id NOT IN ('oct-dup')
            """
        ).fetchone()
        assert row is not None and row["total"] == october.total
        assert sum((entry.value for entry in october.groups), D(0)) == october.total
        for entry in october.groups:
            assert sum((category.value for category in entry.categories), D(0)) == entry.value
        assert groups_of(october)["Alimentação"][0] == D("270")
        assert october.fixed + october.variable == october.total

    def test_income_is_shown_apart(self, conn: Conn, synced: FakePluggy) -> None:
        october = month_summary(conn, today=TODAY, month=date(2026, 10, 1))
        assert october.income == D("5000")
        assert october.leftover == D("3130")
        assert all(entry.group.name != "Outros" for entry in october.groups)

    def test_a_whole_month_is_compared_account_by_account(self, conn: Conn, synced: FakePluggy) -> None:
        november = month_summary(conn, today=date(2026, 12, 5), month=date(2026, 11, 1))
        assert not november.partial
        assert november.compared == (date(2026, 8, 1), date(2026, 9, 1), date(2026, 10, 1))
        assert november.pace is None
        # Cartao em agosto (0), setembro (350) e outubro (370); a conta so em outubro (1500).
        assert november.total_average == D("1740")
        assert groups_of(november)["Alimentação"][1] == thirds("520")
        assert groups_of(november)["Moradia"][1] == D("1500")
        # O PENDING de 25/11 ja passou: entra.
        assert november.total == D("2999")
        assert change(november.total, november.total_average) == (D("2999") - D("1740")) / D("1740")

    def test_compare_takes_another_number_of_months(self, conn: Conn, synced: FakePluggy) -> None:
        november = month_summary(conn, today=date(2026, 12, 5), month=date(2026, 11, 1), compare=1)
        assert november.compared == (date(2026, 10, 1),)
        assert november.total_average == D("1870")
        with pytest.raises(ValidationError):
            month_summary(conn, today=TODAY, compare=0)

    def test_an_account_with_no_month_to_compare_leaves_what_it_touches_without_average(
        self, conn: Conn, synced: FakePluggy
    ) -> None:
        october = month_summary(conn, today=TODAY, month=date(2026, 10, 1))
        # Julho e a entrada do cartao; a conta entrou em setembro e so e comparada de novembro em diante.
        assert october.compared == (date(2026, 8, 1), date(2026, 9, 1))
        assert groups_of(october)["Alimentação"] == (D("270"), D("125"))
        assert groups_of(october)["Moradia"] == (D("1500"), None)
        assert october.total_average is None


class TestCoverage:
    """Criterion 2: a month without data is zero, but only inside the coverage of the accounts."""

    def test_a_covered_month_without_data_is_zero(self, conn: Conn, synced: FakePluggy) -> None:
        trend = spending_trend(conn, today=TODAY)
        assert [point.month for point in trend.points] == [date(2026, month, 1) for month in (7, 8, 9, 10, 11)]
        assert [point.total for point in trend.points] == [D("100"), D("0"), D("1850"), D("1870"), D("2000")]
        # Julho e agosto somam so o cartao: a conta ainda nao tinha historico.
        assert [point.missing for point in trend.points] == [("Conta",), ("Conta",), (), (), ()]
        september = month_summary(conn, today=TODAY, month=date(2026, 9, 1))
        assert september.compared == (date(2026, 8, 1),)
        assert groups_of(september)["Alimentação"] == (D("250"), D("0"))

    def test_months_before_the_coverage_stay_out_of_the_average(self, conn: Conn, synced: FakePluggy) -> None:
        august = month_summary(conn, today=TODAY, month=date(2026, 8, 1))
        # Maio e junho estao fora da cobertura; julho e o mes de entrada do cartao: nenhum entra como zero.
        assert august.compared == ()
        assert august.total == 0
        assert august.groups == ()
        july = month_summary(conn, today=TODAY, month=date(2026, 7, 1))  # o mes de entrada ainda e mostrado
        assert (july.total, july.compared, july.total_average) == (D("100"), (), None)

    def test_an_account_that_starts_later_is_averaged_over_its_own_months(self) -> None:
        """The review's case: card since July at 300 a month, account since October with 1500 of rent."""
        card = [expense(date(2026, month, 10), "300") for month in (7, 8, 9, 10, 11)]
        bank = [expense(date(2026, month, 5), "1500", account_id=2, group=FIXED_GROUP) for month in (10, 11)]
        november = summarize(
            [*card, *bank],
            [],
            Coverage({1: date(2026, 7, 1), 2: date(2026, 10, 1)}),
            date(2026, 11, 1),
            today=date(2026, 12, 1),
            fallback="Outros",
        )
        assert november.compared == (date(2026, 8, 1), date(2026, 9, 1), date(2026, 10, 1))
        # A conta entrou em outubro e so e comparada de novembro em diante: nada a comparar, e nao 1500 contra 500.
        assert groups_of(november)["Moradia"] == (D("1500"), None)
        assert november.total_average is None
        assert groups_of(november)["Alimentação"] == (D("300"), D("300"))
        # Com a conta desde setembro, outubro e a base dela: cada conta na sua media, somadas.
        november = summarize(
            [*card, expense(date(2026, 9, 5), "1500", account_id=2, group=FIXED_GROUP), *bank],
            [],
            Coverage({1: date(2026, 7, 1), 2: date(2026, 9, 1)}),
            date(2026, 11, 1),
            today=date(2026, 12, 1),
            fallback="Outros",
        )
        assert groups_of(november)["Moradia"] == (D("1500"), D("1500"))
        assert november.total_average == D("1800")
        assert change(november.total, november.total_average) == 0

    def test_pace_merchants_and_last_year_are_per_account_too(self) -> None:
        card = [expense(date(2026, month, 10), "300") for month in range(1, 12)]
        card.append(expense(date(2025, 11, 10), "100"))
        bakery = [
            replace(expense(date(2026, month, 10), "50", account_id=2), merchant="Padaria") for month in (9, 10, 11)
        ]
        november = summarize(
            [*card, *bakery],
            [],
            Coverage({1: date(2025, 6, 1), 2: date(2026, 9, 1)}),
            date(2026, 11, 1),
            today=TODAY,
            fallback="Outros",
        )
        assert november.pace is not None
        # O cartao em agosto, setembro e outubro (300); a conta so em outubro (50).
        assert (november.pace.value, november.pace.average) == (D("350"), D("350"))
        visits = {entry.name: entry.average for entry in november.merchants}
        assert visits == {"Mercado": D("1"), "Padaria": D("1")}
        # Em 2025-11 so o cartao tinha historico: compara com o cartao de agora, sem a conta.
        assert november.last_year == YearBefore(D("100"), D("300"), ("2",))  # sem rotulo, a conta vai pelo id

    def test_the_entry_month_stays_out_of_the_comparisons(self) -> None:
        """The owner's case: a history from 2025-11-27 with 40 of spending, seen on 2026-11-28."""
        lines = [expense(date(2025, 11, 27), "40")]
        lines += [expense(add_months(date(2025, 12, 1), back).replace(day=10), "300") for back in range(12)]
        coverage = Coverage({1: date(2025, 11, 1)})
        november = summarize(lines, [], coverage, date(2026, 11, 1), today=date(2026, 11, 28), fallback="Outros")
        assert november.last_year is None  # e nao "40,00 (+4900%)"
        february = summarize(lines, [], coverage, date(2026, 2, 1), today=date(2026, 11, 28), fallback="Outros")
        assert february.compared == (date(2025, 12, 1), date(2026, 1, 1))
        assert february.total_average == D("300")
        entry = summarize(lines, [], coverage, date(2025, 11, 1), today=date(2026, 11, 28), fallback="Outros")
        assert (entry.total, entry.compared) == (D("40"), ())
        trend = monthly_trend(lines, coverage, today=date(2026, 11, 28), months=13)
        assert trend.points[0].month == date(2025, 11, 1) and trend.points[0].total == D("40")

    def test_a_month_before_the_coverage_is_refused(self, conn: Conn, synced: FakePluggy) -> None:
        with pytest.raises(ValidationError, match="fora da cobertura: os dados começam em 2026-07"):
            month_summary(conn, today=TODAY, month=date(2026, 6, 1))
        with pytest.raises(ValidationError, match="ainda não começou"):
            month_summary(conn, today=TODAY, month=date(2026, 12, 1))

    def test_each_account_has_its_own_coverage(self, conn: Conn, synced: FakePluggy) -> None:
        trend = spending_trend(conn, today=TODAY, account="corrente")
        assert [point.month for point in trend.points] == [date(2026, month, 1) for month in (9, 10, 11)]
        assert trend.accounts == ("Conta",)
        november = month_summary(conn, today=TODAY, account="corrente")
        assert november.compared == (date(2026, 10, 1),)
        assert november.income == D("5000")
        card = month_summary(conn, today=TODAY, month=date(2026, 10, 1), account="Cartão")
        assert card.total == D("370")
        assert card.income == 0
        assert card.accounts == ("Cartão",)

    def test_lines_before_the_coverage_never_count(self) -> None:
        """An account counts from the month of its first transaction; a line before it is ignored."""
        line = expense(date(2026, 1, 10), "50", account_id=2)
        coverage = Coverage({1: date(2026, 1, 1), 2: date(2026, 2, 1)})
        trend = monthly_trend([line], coverage, today=date(2026, 2, 10))
        assert [point.total for point in trend.points] == [D("0"), D("0")]


class TestRegrouping:
    """Criterion 3: moving a category to another group changes the summary, with no sync."""

    def test_mapping_a_category_moves_its_spending(self, conn: Conn, synced: FakePluggy) -> None:
        before = groups_of(month_summary(conn, today=TODAY, month=date(2026, 10, 1)))
        assert "Lazer" not in before
        map_category(conn, "Supermercado", "Lazer")
        after = groups_of(month_summary(conn, today=TODAY, month=date(2026, 10, 1)))
        assert "Alimentação" not in after
        assert after["Lazer"] == before["Alimentação"]
        trend = spending_trend(conn, today=TODAY, group="lazer")
        assert [point.total for point in trend.points] == [D("100"), D("0"), D("250"), D("270"), D("300")]
        assert synced.calls == []

    def test_a_group_turned_fixed_leaves_the_pace(self, conn: Conn, synced: FakePluggy) -> None:
        set_nature(conn, "Alimentação", GroupNature.FIXED)
        november = month_summary(conn, today=TODAY)
        assert november.pace is not None
        assert november.pace.value == D("200")  # so a parcela e o Outros
        assert groups_of(november)["Alimentação"] == (D("300"), None)
        assert synced.calls == []


class TestRunningMonth:
    """Criterion 4: the running month is compared up to the same day of the months before."""

    def test_the_pace_takes_the_same_day_and_only_the_variable_groups(self, conn: Conn, synced: FakePluggy) -> None:
        november = month_summary(conn, today=TODAY)
        assert november.month == date(2026, 11, 1)
        assert november.partial
        assert november.until == TODAY
        # O PENDING do dia 25 ainda nao conta.
        assert november.total == D("2000")
        assert (november.fixed, november.variable) == (D("1500"), D("500"))
        assert november.pace is not None
        # Ate o dia 20: agosto 0; setembro 200 + 100 (sem os 50 do dia 25); outubro 300 - 30 + 100.
        assert november.pace.value == D("500")
        assert november.pace.average == thirds("670")
        assert november.total_average is None
        groups = groups_of(november)
        assert groups["Alimentação"] == (D("300"), thirds("470"))
        assert groups["Moradia"] == (D("1500"), None)  # o fixo fica fora do ritmo

    def test_the_day_is_held_to_the_end_of_a_shorter_month(self) -> None:
        lines = [
            expense(date(2026, 1, 30), "10"),
            expense(date(2026, 1, 31), "1000"),
            expense(date(2026, 2, 28), "20"),
            expense(date(2026, 3, 2), "5"),
        ]
        coverage = Coverage({1: date(2025, 12, 1)})
        march = summarize(lines, [], coverage, date(2026, 3, 1), today=date(2026, 3, 30), fallback="Outros")
        assert march.compared == (date(2026, 1, 1), date(2026, 2, 1))
        assert march.pace is not None
        # Janeiro ate o dia 30 (sem o 31); fevereiro ate o dia 28.
        assert march.pace.average == D("15")
        assert cut_end(date(2026, 2, 1), 30) == date(2026, 2, 28)
        assert cut_end(date(2026, 2, 1), None) == date(2026, 2, 28)

    def test_the_same_month_a_year_before_up_to_the_same_day(self) -> None:
        lines = [expense(date(2025, 3, 5), "100"), expense(date(2025, 3, 20), "50"), expense(date(2026, 3, 8), "120")]
        coverage = Coverage({1: date(2025, 1, 1)})
        march = summarize(lines, [], coverage, date(2026, 3, 1), today=date(2026, 3, 10), fallback="Outros")
        assert march.last_year == YearBefore(D("100"), D("120"))
        full = summarize(lines, [], coverage, date(2026, 3, 1), today=date(2026, 4, 2), fallback="Outros")
        assert full.last_year == YearBefore(D("150"), D("120"))
        without = summarize(lines, [], Coverage({1: date(2025, 6, 1)}), date(2026, 3, 1), today=TODAY, fallback="x")
        assert without.last_year is None

    def test_merchants_by_purchases_against_the_average(self, conn: Conn, synced: FakePluggy) -> None:
        november = month_summary(conn, today=TODAY)
        top = [(entry.name, entry.count, entry.value) for entry in november.merchants]
        assert top == [
            ("Mercado Bom", 2, D("300")),
            ("Aluguel", 1, D("1500")),
            ("Coisa", 1, D("100")),
            ("Loja Parcelada", 1, D("100")),
        ]
        # agosto 0, setembro 1 (o do dia 25 fica fora), outubro 1 (o estorno nao e visita)
        assert november.merchants[0].average == thirds("2")

    def test_the_share_of_the_reserve_group(self, conn: Conn, synced: FakePluggy) -> None:
        november = month_summary(conn, today=TODAY)
        assert november.fallback == "Outros"
        assert november.fallback_share == D("0.05")


class TestInstallments:
    """Criterion 5: an installment purchase shows the installment in each month."""

    def test_each_month_has_its_installment(self, conn: Conn, synced: FakePluggy) -> None:
        trend = spending_trend(conn, today=TODAY, group="Compras")
        assert [(point.month.month, point.total) for point in trend.points] == [
            (7, D("0")),
            (8, D("0")),
            (9, D("100")),
            (10, D("100")),
            (11, D("100")),
        ]
        # As parcelas futuras (PENDING) entram quando o relogio chega ao mes delas, uma em cada mes.
        later = spending_trend(conn, today=date(2027, 1, 31), group="Compras")
        assert [point.total for point in later.points][-3:] == [D("100"), D("100"), D("100")]

    def test_the_month_is_the_installments_not_the_purchase_date(self, conn: Conn, synced: FakePluggy) -> None:
        september = month_summary(conn, today=TODAY, month=date(2026, 9, 1))
        assert groups_of(september)["Compras"][0] == D("100")


class TestTrend:
    def test_twelve_months_up_to_the_running_one(self, conn: Conn, synced: FakePluggy) -> None:
        trend = spending_trend(conn, today=date(2027, 6, 10))
        assert trend.points[0].month == date(2026, 7, 1)
        assert trend.points[-1].month == date(2027, 6, 1)
        assert len(spending_trend(conn, today=date(2027, 9, 10)).points) == 12
        assert len(spending_trend(conn, today=TODAY, months=2).points) == 2
        assert spending_trend(conn, today=TODAY).partial

    def test_fixed_and_variable(self, conn: Conn, synced: FakePluggy) -> None:
        trend = spending_trend(conn, today=TODAY)
        assert [(point.fixed, point.variable) for point in trend.points][-1] == (D("1500"), D("500"))

    def test_an_unknown_group(self, conn: Conn, synced: FakePluggy) -> None:
        with pytest.raises(ValidationError, match="Grupo 'Viagens' não encontrado"):
            spending_trend(conn, today=TODAY, group="Viagens")


# ---- funcoes puras


GROUP = CategoryGroup(1, "Alimentação", "variavel")
FIXED_GROUP = CategoryGroup(2, "Moradia", "fixo")


def expense(day: date, value: str, *, account_id: int = 1, group: CategoryGroup = GROUP) -> ExpenseLine:
    return ExpenseLine(
        account_id=account_id,
        local_date=day,
        value=D(value),
        category_id="10000000",
        category="Supermercado",
        group=group,
        merchant="Mercado",
    )


def stored(account_id: int, kind: str, name: str, marketing: str | None = None) -> StoredAccount:
    return StoredAccount(
        id=account_id,
        pluggy_id=f"acc-{account_id}",
        replaced_pluggy_ids=(),
        item_id=ITEM,
        type=kind,
        subtype="",
        number=None,
        name=name,
        marketing_name=marketing,
        tax_number=None,
        transfer_number=None,
        credit_brand=None,
        last_synced_at=datetime(2026, 11, 1, tzinfo=UTC),
        first_transaction_date=date(2026, 1, 1),
        last_transaction_date=None,
    )


ACCOUNTS: Sequence[StoredAccount] = (
    stored(1, "BANK", "Conta Corrente", "NuConta"),
    stored(2, "CREDIT", "Cartão Gold"),
    stored(3, "CREDIT", "Cartao Black"),
)


class TestSelectAccounts:
    def test_by_type(self) -> None:
        assert [entry.id for entry in select_accounts(ACCOUNTS, "corrente")] == [1]
        assert [entry.id for entry in select_accounts(ACCOUNTS, "Cartão")] == [2, 3]
        assert [entry.id for entry in select_accounts(ACCOUNTS, None)] == [1, 2, 3]

    def test_by_name_or_commercial_name(self) -> None:
        assert [entry.id for entry in select_accounts(ACCOUNTS, "nuconta")] == [1]
        assert [entry.id for entry in select_accounts(ACCOUNTS, "conta  corrente")] == [1]
        assert [entry.id for entry in select_accounts(ACCOUNTS, "cartao gold")] == [2]

    def test_an_unknown_account_lists_the_names(self) -> None:
        with pytest.raises(ValidationError) as caught:
            select_accounts([*ACCOUNTS, stored(4, "BANK", "Banco\x1b[2J")], "Poupança")
        message = str(caught.value)
        assert message.startswith("Conta 'Poupança' não encontrada. Use corrente ou cartao, ou o nome de uma conta: ")
        assert "NuConta, Cartão Gold, Cartao Black, Banco[2J." in message


class TestMonths:
    @pytest.mark.parametrize("text", ["2026-13", "2026-00", "11/2026", "2026-1", ""])
    def test_an_invalid_month(self, text: str) -> None:
        with pytest.raises(ValidationError, match="Use AAAA-MM"):
            parse_month(text)

    def test_a_month(self) -> None:
        assert parse_month(" 2026-02 ") == date(2026, 2, 1)
        assert add_months(date(2026, 1, 1), -1) == date(2025, 12, 1)
        assert add_months(date(2026, 11, 1), -12) == date(2025, 11, 1)
        assert add_months(date(2026, 12, 1), 1) == date(2027, 1, 1)

    def test_change(self) -> None:
        assert change(D("150"), D("100")) == D("0.5")
        assert change(D("150"), D("0")) is None
        assert change(D("150"), None) is None

    def test_income_of_an_uncovered_account_does_not_count(self) -> None:
        coverage = Coverage({1: date(2026, 3, 1), 2: None})
        incomes = [IncomeLine(1, date(2026, 3, 5), D("10")), IncomeLine(2, date(2026, 3, 5), D("99"))]
        march = summarize([], incomes, coverage, date(2026, 3, 1), today=date(2026, 3, 31), fallback="Outros")
        assert march.income == D("10")
        assert march.fallback_share is None
