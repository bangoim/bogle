"""Tests for the form validators' wording: what is said about a field agrees with
its label in gender and number.
"""

from __future__ import annotations

from decimal import Decimal

from textual.validation import Validator

from bogle.tui.validators import DateField, DecimalField, HeldShares, TextField


def message(validator: Validator, value: str) -> str:
    result = validator.validate(value)
    assert not result.is_valid
    return result.failure_descriptions[0]


class TestRequired:
    def test_a_masculine_label_by_default(self) -> None:
        assert message(TextField("Emissor"), "") == "Emissor é obrigatório."
        assert message(DecimalField("Preço unitário"), "") == "Preço unitário é obrigatório."
        assert message(DateField("Vencimento"), "") == "Vencimento é obrigatório."

    def test_a_feminine_label(self) -> None:
        assert message(DecimalField("Quantidade", feminine=True), "") == "Quantidade é obrigatória."
        assert message(DateField("Data", feminine=True), "") == "Data é obrigatória."

    def test_a_plural_label(self) -> None:
        assert message(DecimalField("Taxas", feminine=True, plural=True), "") == "Taxas são obrigatórias."

    def test_a_sale_quantity_is_feminine(self) -> None:
        assert message(HeldShares("PETR4", Decimal("10")), "") == "Quantidade é obrigatória."


class TestPositive:
    def test_a_plural_label_takes_a_plural_verb(self) -> None:
        assert message(DecimalField("Quantidade", positive=True, feminine=True), "0") == (
            "Quantidade deve ser maior que zero, recebido 0."
        )
        assert message(DecimalField("Cotas", positive=True, feminine=True, plural=True), "0") == (
            "Cotas devem ser maiores que zero, recebido 0."
        )


class TestNegative:
    def test_the_verb_and_the_adjective_agree(self) -> None:
        assert message(DecimalField("IR retido"), "-1") == "IR retido não pode ser negativo, recebido -1."
        assert message(DecimalField("Taxa", feminine=True), "-1") == "Taxa não pode ser negativa, recebido -1."
        assert (
            message(DecimalField("Taxas", feminine=True, plural=True), "-1")
            == "Taxas não podem ser negativas, recebido -1."
        )
