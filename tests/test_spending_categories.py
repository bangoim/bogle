"""Tests for category groups, the user's category and category rules (issue #116).

Through the sync and the test database, with Pluggy's whole category tree
(``fixtures/pluggy_categories_tree.json``). The groups start every test as
migration 012 writes them (``conftest``).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from psycopg.rows import DictRow

from bogle.data.pluggy import parse_category
from bogle.domain.errors import SyncInProgressError, ValidationError
from bogle.spending import repository
from bogle.spending.categories import (
    create_group,
    find_category,
    find_group,
    group_summaries,
    map_category,
    parse_nature,
    remove_group,
    rename_group,
    set_category,
    set_nature,
    unmapped_categories,
)
from bogle.spending.models import DescriptionMatch, GroupNature, RuleConditions, RuleLine
from bogle.spending.rules import (
    RuleBasis,
    RuleDraft,
    check,
    create_rule,
    describe,
    draft_for,
    matches,
    preview,
    remove_rule,
    rules,
    undo_last,
)
from bogle.spending.sync import confirm_duplicate, run_sync
from tests.conftest import TEST_DATABASE_URL
from tests.pluggy_fakes import ITEM, NOW, FakePluggy, account, category_tree, item, tx

Conn = psycopg.Connection[DictRow]

CARD = "acc-card"
BANK = "acc-bank"
ME = "000.000.000-00"
IFOOD = "14.380.200/0001-21"
"""A made-up CNPJ for the delivery app."""
LANDLORD = "111.222.333-44"

DELIVERY = ("11020000", "Food delivery")
PHARMACY = ("18020000", "Pharmacy")
STREAMING = ("09020000", "Video streaming")
THIRD_PARTY_PIX = ("05090004", "Third party transfer - PIX")
BRAND_NEW = parse_category({"id": "30000000", "description": "Brand new", "descriptionTranslated": "Novidade"})
"""A category Pluggy might add one day: no default maps it."""


def pix(receiver: str) -> dict[str, Any]:
    def party(document: str) -> dict[str, Any]:
        return {"documentNumber": {"type": "CPF", "value": document}}

    return {"payer": party(ME), "receiver": party(receiver), "paymentMethod": "PIX"}


def ifood(tx_id: str, day: date, amount: str, description: str) -> Any:
    return tx(tx_id, CARD, day, amount, description=description, category=DELIVERY, merchant_cnpj=IFOOD)


@pytest.fixture
def pluggy() -> FakePluggy:
    fake = FakePluggy()
    fake.categories = category_tree()
    fake.add_item(item(), account(CARD, name="Cartão"), account(BANK, kind="BANK", number="12345-6", name="Conta"))
    fake.set_transactions(
        CARD,
        ifood("ifood-1", date(2026, 10, 5), "45.90", "IFOOD *Restaurante"),
        ifood("ifood-2", date(2026, 10, 12), "80", "IFOOD *Mercado"),
        tx("drug", CARD, date(2026, 10, 15), "30", description="Drogaria Sao Paulo", category=PHARMACY),
        tx("netflix", CARD, date(2026, 10, 20), "55.90", description="Netflix.com", category=STREAMING),
    )
    fake.set_transactions(
        BANK,
        tx(
            "rent",
            BANK,
            date(2026, 11, 11),
            "-1800",
            description="Pix enviado Fulano",
            category=THIRD_PARTY_PIX,
            operation="PIX",
            payment_data=pix(LANDLORD),
            at="16:00:00.000",
        ),
        tx(
            "cleaner",
            BANK,
            date(2026, 11, 12),
            "-200",
            description="Pix enviado Ciclana",
            category=THIRD_PARTY_PIX,
            operation="PIX",
            payment_data=pix("555.666.777-88"),
            at="16:00:00.000",
        ),
    )
    return fake


def sync(conn: Conn, fake: FakePluggy, **kw: Any) -> Any:
    report = run_sync(conn, fake, (ITEM,), now=kw.pop("now", NOW), **kw)
    assert report.failures == []
    return report


def local_id(conn: Conn, pluggy_id: str) -> int:
    row = conn.execute("SELECT id FROM spending.transactions WHERE pluggy_id = %s", (pluggy_id,)).fetchone()
    assert row is not None
    return row["id"]


def categories(conn: Conn) -> dict[str, str | None]:
    """The category that counts, per line."""
    rows = conn.execute("SELECT pluggy_id, effective_category_id FROM spending.transactions").fetchall()
    return {row["pluggy_id"]: row["effective_category_id"] for row in rows}


def ruled(conn: Conn) -> dict[str, int | None]:
    rows = conn.execute("SELECT pluggy_id, rule_id FROM spending.transactions").fetchall()
    return {row["pluggy_id"]: row["rule_id"] for row in rows}


def expense(conn: Conn, pluggy_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT e.category_id, e.category, e.category_source, e.pluggy_category_id, e.group_name, e.group_nature "
        "FROM spending.expenses e JOIN spending.transactions t ON t.id = e.transaction_id WHERE t.pluggy_id = %s",
        (pluggy_id,),
    ).fetchone()
    assert row is not None
    return dict(row)


def group_of(conn: Conn, category_id: str) -> str:
    row = conn.execute(
        "SELECT g.name FROM spending.category_group_of r JOIN spending.category_groups g ON g.id = r.group_id "
        "WHERE r.category_id = %s",
        (category_id,),
    ).fetchone()
    assert row is not None
    return row["name"]


def draft(conn: Conn, pluggy_id: str, category: str, **kw: Any) -> RuleDraft:
    return draft_for(conn, local_id(conn, pluggy_id), find_category(conn, category), **kw)


# ------------------------------------------------------------------ grupos


class TestDefaultGroups:
    def test_every_pluggy_category_has_a_group_of_its_own(self, conn: Conn, pluggy: FakePluggy) -> None:
        """Acceptance 3: the default maps all 130 categories (itself or through a parent); none is unmapped."""
        sync(conn, pluggy)
        resolution = repository.category_resolution(conn)
        assert len(resolution) == 130
        assert all(origin is not None for _, _, origin in resolution)
        assert unmapped_categories(conn) == []

    def test_a_category_nobody_mapped_falls_in_outros_and_is_warned(self, conn: Conn, pluggy: FakePluggy) -> None:
        """Acceptance 3: a category the default does not know still has a group, the reserve one."""
        pluggy.categories.append(BRAND_NEW)
        pluggy.set_transactions(
            CARD, *pluggy.transactions[CARD], tx("new", CARD, date(2026, 10, 25), "12", category=("30000000", "x"))
        )
        report = sync(conn, pluggy)
        assert report.unmapped_categories == ["Novidade"]
        assert [category.id for category in unmapped_categories(conn)] == ["30000000"]
        assert expense(conn, "new")["group_name"] == "Outros"

    def test_an_unknown_category_id_on_a_line_falls_in_outros(self, conn: Conn, pluggy: FakePluggy) -> None:
        pluggy.set_transactions(CARD, tx("odd", CARD, date(2026, 10, 25), "12", category=("31000000", "Odd")))
        sync(conn, pluggy)
        assert expense(conn, "odd")["group_name"] == "Outros"

    def test_once_mapped_it_is_no_longer_warned(self, conn: Conn, pluggy: FakePluggy) -> None:
        pluggy.categories.append(BRAND_NEW)
        sync(conn, pluggy)
        map_category(conn, "novidade", "Compras")
        assert unmapped_categories(conn) == []
        assert group_of(conn, "30000000") == "Compras"

    @pytest.mark.parametrize(
        ("category_id", "group"),
        [
            ("11020000", "Alimentação"),  # filho segue o pai
            ("10000000", "Alimentação"),
            ("17010000", "Moradia"),
            ("17030000", "Compras"),  # excecao dentro de Moradia
            ("07010001", "Serviços"),  # neto
            ("07040003", "Lazer"),  # excecao dentro de Serviços
            ("09020000", "Serviços"),  # assinatura nao e grupo
            ("200200000", "Moradia"),  # id de nove digitos
            ("200300000", "Serviços"),
            ("02030001", "Financiamentos"),  # o pai declarado e 02000000
            ("02020000", "Impostos e taxas"),
            ("05090004", "Outros"),
            ("99999999", "Outros"),
        ],
    )
    def test_the_default_mapping(self, conn: Conn, pluggy: FakePluggy, category_id: str, group: str) -> None:
        sync(conn, pluggy)
        assert group_of(conn, category_id) == group

    def test_the_mapping_exists_before_the_first_sync(self, conn: Conn) -> None:
        assert group_of(conn, "17000000") == "Moradia"

    def test_every_group_has_a_nature(self, conn: Conn) -> None:
        """Acceptance 5."""
        natures = {group.name: group.nature for group in repository.category_groups(conn)}
        assert set(natures.values()) <= {"fixo", "variavel"}
        assert (natures["Moradia"], natures["Serviços"]) == ("fixo", "fixo")
        assert {natures[name] for name in ("Alimentação", "Lazer", "Compras")} == {"variavel"}
        assert "Assinaturas" not in natures

    def test_the_expenses_carry_the_category_and_its_group(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        assert expense(conn, "ifood-1") == {
            "category_id": "11020000",
            "category": "Delivery de alimentos",
            "category_source": "pluggy",
            "pluggy_category_id": "11020000",
            "group_name": "Alimentação",
            "group_nature": "variavel",
        }


class TestGroups:
    def test_a_new_group_needs_a_nature(self, conn: Conn) -> None:
        """Acceptance 5: the database refuses a group without one, the parser anything but the two."""
        with pytest.raises(psycopg.errors.NotNullViolation):
            conn.execute("INSERT INTO spending.category_groups (name) VALUES ('Pets')")
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("INSERT INTO spending.category_groups (name, nature) VALUES ('Pets', 'mensal')")
        with pytest.raises(ValidationError, match="Natureza 'mensal' inválida"):
            parse_nature("mensal")

    @pytest.mark.parametrize("text", ["variavel", "Variável", " VARIAVEL "])
    def test_reads_a_nature(self, text: str) -> None:
        assert parse_nature(text) == GroupNature.VARIABLE

    def test_creates_a_group(self, conn: Conn) -> None:
        group = create_group(conn, "  Pets   e vet ", GroupNature.VARIABLE)
        assert (group.name, group.nature, group.fallback) == ("Pets e vet", "variavel", False)
        assert find_group(conn, "pets E VET").id == group.id

    def test_a_name_is_taken_regardless_of_case_and_accents(self, conn: Conn) -> None:
        with pytest.raises(ValidationError, match="Já existe o grupo 'Saúde'"):
            create_group(conn, "saude", GroupNature.FIXED)

    def test_renames_and_changes_the_nature(self, conn: Conn) -> None:
        rename_group(conn, "lazer", "Diversão")
        set_nature(conn, "diversao", GroupNature.FIXED)
        group = find_group(conn, "Diversão")
        assert (group.name, group.nature) == ("Diversão", "fixo")

    def test_the_reserve_group_may_be_renamed_and_still_takes_the_unmapped(
        self, conn: Conn, pluggy: FakePluggy
    ) -> None:
        pluggy.categories.append(BRAND_NEW)
        sync(conn, pluggy)
        rename_group(conn, "Outros", "Diversos")
        assert group_of(conn, "30000000") == "Diversos"
        assert find_group(conn, "Diversos").fallback

    def test_remaps_a_category(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        category, group = map_category(conn, "Farmácia", "Compras")
        assert (category.id, group.name) == ("18020000", "Compras")
        assert expense(conn, "drug")["group_name"] == "Compras"
        assert group_of(conn, "18010000") == "Saúde"  # o irmao fica com o pai

    def test_removing_a_group_moves_its_categories_to_the_destination(self, conn: Conn, pluggy: FakePluggy) -> None:
        """Acceptance 4."""
        sync(conn, pluggy)
        group, target, moved = remove_group(conn, "Lazer", "outros")
        assert (group.name, target.name, moved) == ("Lazer", "Outros", 5)
        assert group_of(conn, "21000000") == "Outros"
        assert group_of(conn, "12010000") == "Outros"
        assert "Lazer" not in {summary.group.name for summary in group_summaries(conn)}
        assert unmapped_categories(conn) == []  # foram mapeadas, nao caíram

    def test_the_destination_must_exist_and_be_another_group(self, conn: Conn) -> None:
        with pytest.raises(ValidationError, match="Grupo 'Nada' não encontrado"):
            remove_group(conn, "Lazer", "Nada")
        with pytest.raises(ValidationError, match="O destino precisa ser outro grupo"):
            remove_group(conn, "Lazer", "lazer")
        assert find_group(conn, "Lazer").name == "Lazer"
        assert group_of(conn, "21000000") == "Lazer"

    def test_the_reserve_group_cannot_be_removed(self, conn: Conn) -> None:
        with pytest.raises(ValidationError, match="é o de reserva e não pode ser removido"):
            remove_group(conn, "Outros", "Lazer")

    def test_the_summary_lists_the_mapped_categories_by_name(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        summary = next(s for s in group_summaries(conn) if s.group.name == "Alimentação")
        assert summary.mapped == ("Supermercado", "Alimentos e bebidas")
        assert summary.categories == 4  # os dois e os dois filhos de Alimentos e bebidas
        assert group_summaries(conn)[-1].group.fallback


# ------------------------------------------------------------ categoria a mao


class TestFindCategory:
    @pytest.mark.parametrize("text", ["18020000", "farmacia", "FARMÁCIA", " Pharmacy "])
    def test_by_id_or_name(self, conn: Conn, pluggy: FakePluggy, text: str) -> None:
        sync(conn, pluggy)
        assert find_category(conn, text).id == "18020000"

    def test_unknown(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        with pytest.raises(ValidationError, match="Categoria 'Farmacinha' não encontrada"):
            find_category(conn, "Farmacinha")
        with pytest.raises(ValidationError, match="Categoria '123' não encontrada"):
            find_category(conn, "123")

    def test_ambiguous(self, conn: Conn, pluggy: FakePluggy) -> None:
        pluggy.categories.append(
            parse_category({"id": "30000001", "description": "Drugstore", "descriptionTranslated": "Farmácia"})
        )
        sync(conn, pluggy)
        with pytest.raises(ValidationError, match=r"ambígua: 18020000 \(Farmácia\), 30000001 \(Farmácia\). Use o id"):
            find_category(conn, "farmacia")


class TestManualCategory:
    def test_survives_the_next_sync(self, conn: Conn, pluggy: FakePluggy) -> None:
        """Acceptance 1."""
        sync(conn, pluggy)
        drug = local_id(conn, "drug")
        after = set_category(conn, drug, find_category(conn, "Compras"), now=NOW)
        assert (after.category, after.group, after.manual_category, after.pluggy_category) == (
            "Compras",
            "Compras",
            "Compras",
            "Farmácia",
        )
        sync(conn, pluggy, now=NOW + timedelta(days=1), full=True)
        assert categories(conn)["drug"] == "08000000"
        row = conn.execute("SELECT category_id FROM spending.transactions WHERE id = %s", (drug,)).fetchone()
        assert row == {"category_id": "18020000"}  # a da Pluggy segue sendo reescrita
        assert expense(conn, "drug")["category_source"] == "manual"

    def test_survives_a_new_pluggy_id(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        set_category(conn, local_id(conn, "drug"), find_category(conn, "Compras"), now=NOW)
        renamed = [
            tx("drug-2", CARD, date(2026, 10, 15), "30", description="Drogaria Sao Paulo", category=PHARMACY)
            if line.id == "drug"
            else line
            for line in pluggy.transactions[CARD]
        ]
        pluggy.set_transactions(CARD, *renamed)
        sync(conn, pluggy, now=NOW + timedelta(days=1), full=True)
        assert categories(conn)["drug-2"] == "08000000"

    def test_auto_hands_it_back(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        drug = local_id(conn, "drug")
        set_category(conn, drug, find_category(conn, "Compras"), now=NOW)
        after = set_category(conn, drug, None, now=NOW)
        assert (after.category, after.manual_category, after.group) == ("Farmácia", None, "Saúde")
        row = conn.execute("SELECT manual_category_at FROM spending.transactions WHERE id = %s", (drug,)).fetchone()
        assert row == {"manual_category_at": None}

    def test_wins_over_a_rule(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        set_category(conn, local_id(conn, "ifood-1"), find_category(conn, "Lazer"), now=NOW)
        create_rule(conn, draft(conn, "ifood-1", "Restaurantes, bares e lanchonetes"), now=NOW)
        assert categories(conn)["ifood-1"] == "21000000"
        assert categories(conn)["ifood-2"] == "11010000"
        entry = repository.transaction_category(conn, local_id(conn, "ifood-1"))
        assert entry is not None
        assert (entry.manual_category, entry.rule_category) == ("Lazer", "Restaurantes, bares e lanchonetes")

    def test_carried_by_the_newer_side_of_a_merged_duplicate(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        again = tx("drug-again", CARD, date(2026, 10, 15), "30", description="Drogaria Sao Paulo", at="12:00:00.001")
        pluggy.set_transactions(CARD, *[again if line.id == "drug" else line for line in pluggy.transactions[CARD]])
        sync(conn, pluggy, now=NOW + timedelta(days=1), full=True)
        set_category(conn, local_id(conn, "drug-again"), find_category(conn, "Compras"), now=NOW)
        suggestion = conn.execute("SELECT id FROM spending.duplicate_suggestions").fetchone()
        assert suggestion is not None
        confirm_duplicate(conn, suggestion["id"])
        assert categories(conn)["drug-again"] == "08000000"

    def test_an_unknown_transaction_is_refused(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        with pytest.raises(ValidationError, match="Transação 999999 não encontrada"):
            set_category(conn, 999999, find_category(conn, "Compras"), now=NOW)


# ------------------------------------------------------------------ regras


def line(description: str = "IFOOD *Restaurante", **kw: Any) -> RuleLine:
    values: dict[str, Any] = {"id": 1, "account_id": 1, "value": Decimal("45.90"), "documents": ("14380200000121",)}
    return RuleLine(description=description, **(values | kw))


class TestMatching:
    def test_equals_never_takes_a_part(self) -> None:
        """Acceptance 6."""
        rule = RuleConditions(description="IFOOD", match=DescriptionMatch.EQUALS)
        assert not matches(rule, line("IFOOD *Restaurante"))
        assert not matches(rule, line("Pagamento IFOOD"))
        assert matches(rule, line("ifood"))
        assert matches(rule, line("  Ifood "))

    def test_equals_is_the_whole_description_regardless_of_case_and_accents(self) -> None:
        rule = RuleConditions(description="padaria são joão", match=DescriptionMatch.EQUALS)
        assert matches(rule, line("PADARIA  SAO JOAO"))

    def test_contains_and_starts(self) -> None:
        contains = RuleConditions(description="mercado", match=DescriptionMatch.CONTAINS)
        starts = RuleConditions(description="ifood *r", match=DescriptionMatch.STARTS)
        assert matches(contains, line("IFOOD *Mercado"))
        assert not matches(contains, line("IFOOD *Restaurante"))
        assert matches(starts, line("IFOOD *Restaurante"))
        assert not matches(starts, line("IFOOD *Mercado"))

    def test_every_condition_must_hold(self) -> None:
        rule = RuleConditions(
            document="14380200000121",
            description="ifood",
            match=DescriptionMatch.STARTS,
            min_value=Decimal("10"),
            max_value=Decimal("50"),
            account_id=1,
        )
        assert matches(rule, line())
        assert matches(rule, line(value=Decimal("50")))  # as pontas valem
        assert not matches(rule, line(value=Decimal("50.01")))
        assert not matches(rule, line(value=Decimal("9.99")))
        assert not matches(rule, line(account_id=2))
        assert not matches(rule, line(documents=("11122233344",)))
        assert not matches(rule, line("Rappi"))

    def test_the_document_may_be_the_merchants_or_the_other_partys(self) -> None:
        rule = RuleConditions(document="11122233344")
        assert matches(rule, line(documents=("14380200000121", "11122233344")))

    def test_a_rule_needs_a_document_or_a_description(self) -> None:
        with pytest.raises(ValidationError, match="precisa de um documento ou de uma descrição"):
            check(RuleConditions(min_value=Decimal("10")))
        with pytest.raises(ValidationError, match="descrição da regra está vazia"):
            check(RuleConditions(description="  ", match=DescriptionMatch.CONTAINS))
        with pytest.raises(ValidationError, match="maior que o máximo"):
            check(RuleConditions(document="1", min_value=Decimal("20"), max_value=Decimal("10")))

    def test_describes_the_conditions(self) -> None:
        rule = RuleConditions(
            document="14380200000121",
            description="ifood",
            match=DescriptionMatch.CONTAINS,
            max_value=Decimal("50"),
            account_id=3,
        )
        assert describe(rule, "Cartão") == (
            'documento 14.380.200/0001-21, descrição contém "ifood", valor até 50.00, conta Cartão'
        )
        assert describe(RuleConditions(document="11122233344")) == "documento 111.222.333-44"


class TestDraft:
    def test_by_the_merchants_document_by_default(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        built = draft(conn, "ifood-1", "Restaurantes, bares e lanchonetes")
        assert built.conditions == RuleConditions(document="14380200000121")

    def test_by_the_pix_receivers_document(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        assert draft(conn, "rent", "Aluguel").conditions == RuleConditions(document="11122233344")

    def test_without_a_document_the_whole_description(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        built = draft(conn, "netflix", "Video streaming")
        assert built.conditions == RuleConditions(description="Netflix.com", match=DescriptionMatch.EQUALS)

    def test_a_text_contains_by_default(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        built = draft(conn, "ifood-2", "Supermercado", text="mercado", min_value=Decimal("10"), same_account=True)
        assert built.conditions == RuleConditions(
            description="mercado",
            match=DescriptionMatch.CONTAINS,
            min_value=Decimal("10"),
            account_id=local_account(conn, CARD),
        )
        assert built.account == "Cartão"

    def test_a_document_and_a_text_take_both(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        built = draft(conn, "ifood-2", "Supermercado", basis=RuleBasis.DOCUMENT, text="mercado")
        assert (built.conditions.document, built.conditions.description) == ("14380200000121", "mercado")

    def test_a_document_that_is_not_there(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        with pytest.raises(ValidationError, match="sem documento"):
            draft(conn, "netflix", "Video streaming", basis=RuleBasis.DOCUMENT)


def local_account(conn: Conn, pluggy_id: str) -> int:
    row = conn.execute("SELECT id FROM spending.accounts WHERE pluggy_id = %s", (pluggy_id,)).fetchone()
    assert row is not None
    return row["id"]


class TestRules:
    def test_a_rule_takes_the_stored_lines_and_the_ones_that_come_later(self, conn: Conn, pluggy: FakePluggy) -> None:
        """Acceptance 2."""
        sync(conn, pluggy)
        rule_id, changed = create_rule(conn, draft(conn, "ifood-1", "Restaurantes, bares e lanchonetes"), now=NOW)
        assert changed == 2
        assert {key: value for key, value in categories(conn).items() if key.startswith("ifood")} == {
            "ifood-1": "11010000",
            "ifood-2": "11010000",
        }
        pluggy.set_transactions(
            CARD,
            *pluggy.transactions[CARD],
            ifood("ifood-3", date(2026, 10, 26), "33", "IFOOD *Lanches"),
            tx("other", CARD, date(2026, 10, 26), "10", description="IFOOD *Lanches", category=DELIVERY),
        )
        sync(conn, pluggy, now=NOW + timedelta(days=1))
        assert ruled(conn)["ifood-3"] == rule_id
        assert categories(conn)["ifood-3"] == "11010000"
        assert categories(conn)["other"] == "11020000"  # sem o CNPJ, fica com a da Pluggy
        assert expense(conn, "ifood-3")["category_source"] == "regra"

    def test_a_pix_rule_moves_the_rent_to_housing(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        assert expense(conn, "rent")["group_name"] == "Outros"
        create_rule(conn, draft(conn, "rent", "Aluguel"), now=NOW)
        assert expense(conn, "rent")["group_name"] == "Moradia"
        assert expense(conn, "cleaner")["group_name"] == "Outros"

    def test_the_rules_category_survives_the_sync(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        create_rule(conn, draft(conn, "rent", "Aluguel"), now=NOW)
        sync(conn, pluggy, now=NOW + timedelta(days=1), full=True)
        assert categories(conn)["rent"] == "17010000"

    def test_the_preview_counts_what_the_rule_changes(self, conn: Conn, pluggy: FakePluggy) -> None:
        """Acceptance 7, with a line held by an older rule and one with a category set by hand."""
        sync(conn, pluggy)
        create_rule(conn, draft(conn, "ifood-2", "Supermercado", text="mercado"), now=NOW)
        set_category(conn, local_id(conn, "ifood-1"), find_category(conn, "Lazer"), now=NOW)
        newer = draft(conn, "ifood-1", "Restaurantes, bares e lanchonetes", text="ifood", match=DescriptionMatch.STARTS)
        shown = preview(conn, newer)
        assert (shown.count, shown.manual) == (2, 1)
        rule_id, changed = create_rule(conn, newer, now=NOW)
        assert changed == shown.count
        assert sum(1 for value in ruled(conn).values() if value == rule_id) == shown.count
        application = repository.last_application(conn)
        assert application is not None and application.changes == shown.count

    def test_the_preview_of_equals_does_not_count_a_part(self, conn: Conn, pluggy: FakePluggy) -> None:
        """Acceptances 6 and 7 together, on the stored lines."""
        sync(conn, pluggy)
        assert preview(conn, draft(conn, "ifood-1", "Lazer", text="IFOOD", match=DescriptionMatch.EQUALS)).count == 0
        exact = draft(conn, "ifood-1", "Lazer", text="ifood *restaurante", match=DescriptionMatch.EQUALS)
        assert preview(conn, exact).count == 1
        assert create_rule(conn, exact, now=NOW)[1] == 1

    def test_the_newest_rule_wins(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        older, _ = create_rule(conn, draft(conn, "ifood-1", "Restaurantes, bares e lanchonetes"), now=NOW)
        newer, _ = create_rule(conn, draft(conn, "ifood-2", "Supermercado", text="mercado"), now=NOW)
        assert ruled(conn)["ifood-2"] == newer
        pluggy.set_transactions(CARD, ifood("ifood-3", date(2026, 10, 26), "33", "IFOOD *Mercado Extra"))
        sync(conn, pluggy, now=NOW + timedelta(days=1))
        assert ruled(conn)["ifood-3"] == newer
        assert ruled(conn)["ifood-1"] == older

    def test_removing_a_rule_gives_its_lines_to_an_older_one_or_back_to_pluggy(
        self, conn: Conn, pluggy: FakePluggy
    ) -> None:
        sync(conn, pluggy)
        older, _ = create_rule(conn, draft(conn, "ifood-1", "Restaurantes, bares e lanchonetes"), now=NOW)
        newer, _ = create_rule(conn, draft(conn, "ifood-2", "Supermercado", text="mercado"), now=NOW)
        rule, released = remove_rule(conn, newer)
        assert (rule.id, released) == (newer, 1)
        assert ruled(conn)["ifood-2"] == older
        remove_rule(conn, older)
        assert categories(conn)["ifood-1"] == categories(conn)["ifood-2"] == "11020000"
        assert repository.stored_rules(conn) == []
        with pytest.raises(ValidationError, match=f"Regra {older} não encontrada"):
            remove_rule(conn, older)

    def test_undo_puts_back_exactly_what_was_there(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        before = categories(conn)
        older, _ = create_rule(conn, draft(conn, "ifood-1", "Restaurantes, bares e lanchonetes"), now=NOW)
        after_older = (categories(conn), ruled(conn))
        newer, _ = create_rule(conn, draft(conn, "ifood-2", "Supermercado", text="mercado"), now=NOW)
        undone = undo_last(conn)
        assert (undone.rule_id, undone.restored) == (newer, 1)
        assert (categories(conn), ruled(conn)) == after_older
        assert [rule.id for rule in repository.stored_rules(conn)] == [older]
        undone = undo_last(conn)
        assert (undone.rule_id, undone.restored) == (older, 2)
        assert categories(conn) == before
        with pytest.raises(ValidationError, match="Nenhuma aplicação de regra para desfazer"):
            undo_last(conn)

    def test_undo_also_releases_the_lines_the_sync_gave_the_rule(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        create_rule(conn, draft(conn, "ifood-1", "Restaurantes, bares e lanchonetes"), now=NOW)
        pluggy.set_transactions(CARD, ifood("ifood-3", date(2026, 10, 26), "33", "IFOOD *Lanches"))
        sync(conn, pluggy, now=NOW + timedelta(days=1))
        undo_last(conn)
        assert ruled(conn)["ifood-3"] is None
        assert categories(conn)["ifood-3"] == "11020000"

    def test_a_rule_of_one_account_only(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        pix_rule = draft(conn, "rent", "Aluguel", text="pix enviado", same_account=True)
        assert preview(conn, pix_rule).count == 2
        card_rule = RuleDraft(
            pix_rule.category, RuleConditions(description="pix", match=DescriptionMatch.CONTAINS, account_id=0)
        )
        assert preview(conn, card_rule).count == 0

    def test_rules_do_not_change_while_a_sync_runs(self, conn: Conn, pluggy: FakePluggy) -> None:
        """A sync in another process would apply the old rules to the lines it inserts."""
        sync(conn, pluggy)
        rule_id, _ = create_rule(conn, draft(conn, "ifood-1", "Restaurantes, bares e lanchonetes"), now=NOW)
        holder = psycopg.connect(TEST_DATABASE_URL, autocommit=True)
        try:
            holder.execute("SELECT pg_advisory_lock(%s)", (repository.SYNC_LOCK,))
            with pytest.raises(SyncInProgressError):
                create_rule(conn, draft(conn, "ifood-2", "Restaurantes, bares e lanchonetes"), now=NOW)
            with pytest.raises(SyncInProgressError):
                remove_rule(conn, rule_id)
            with pytest.raises(SyncInProgressError):
                undo_last(conn)
            holder.execute("SELECT pg_advisory_unlock(%s)", (repository.SYNC_LOCK,))
        finally:
            holder.close()
        assert [rule.id for rule in rules(conn)] == [rule_id]
        assert ruled(conn)["ifood-1"] == rule_id

    def test_the_database_refuses_a_rule_without_document_or_description(self, conn: Conn, pluggy: FakePluggy) -> None:
        sync(conn, pluggy)
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO spending.category_rules (category_id, min_value, created_at) VALUES ('18020000', 1, now())"
            )
