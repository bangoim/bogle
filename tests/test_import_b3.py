"""Tests for reading the B3's Negociacao statement (issue #34)."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from bogle.domain.errors import ImportFileError
from bogle.domain.transactions import TransactionType
from bogle.imports.b3 import RowError, Statement, key, read_statement
from tests.xlsx_builder import HEADER, HEADER_PLAIN, MOVEMENT_HEADER, Value, build_xlsx, serial, trade_row

FIXTURE = Path(__file__).parent / "fixtures" / "b3_negociacao.xlsx"
SAO_PAULO = ZoneInfo("America/Sao_Paulo")
BUY, SELL = TransactionType.BUY, TransactionType.SELL


def midnight(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=SAO_PAULO)


def statement_of(tmp_path: Path, rows: list[list[Value]], *, header: tuple[str, ...] = HEADER, **options) -> Statement:
    return read_statement(build_xlsx(tmp_path / "negociacao.xlsx", [list(header), *rows], **options))


class TestRealExport:
    def test_every_buy_and_sale_becomes_a_trade(self) -> None:
        statement = read_statement(FIXTURE)
        assert len(statement.trades) == 21
        assert statement.errors == ()
        assert statement.ignored_markets == ()
        assert statement.rows == 21
        assert Counter(trade.ticker for trade in statement.trades) == {
            "AUVP11": 2,
            "B5P211": 7,
            "MUND11": 2,
            "NB1011": 2,
            "VWRA11": 8,
        }
        assert Counter(trade.kind for trade in statement.trades) == {BUY: 18, SELL: 3}

    def test_dates_are_the_trade_date_at_midnight_in_sao_paulo(self) -> None:
        statement = read_statement(FIXTURE)
        assert all(trade.when.tzinfo == SAO_PAULO and trade.when.hour == 0 for trade in statement.trades)
        first, last = statement.trades[0], statement.trades[-1]
        assert (first.row, first.when, first.kind, first.ticker) == (2, midnight(2026, 10, 6), BUY, "MUND11")
        assert (last.row, last.when, last.ticker) == (22, midnight(2026, 1, 9), "VWRA11")

    def test_quantities_and_prices_are_exact(self) -> None:
        by_row = {trade.row: trade for trade in read_statement(FIXTURE).trades}
        assert (by_row[2].shares, by_row[2].unit_price) == (Decimal("81"), Decimal("95.27"))
        assert (by_row[7].kind, by_row[7].shares, by_row[7].unit_price) == (SELL, Decimal("392"), Decimal("126.72"))
        assert (by_row[21].shares, by_row[21].unit_price) == (Decimal("20"), Decimal("117.37"))
        assert (by_row[22].shares, by_row[22].unit_price) == (Decimal("52"), Decimal("117.34"))
        assert all(isinstance(trade.unit_price, Decimal) for trade in by_row.values())

    def test_renda_fixa_is_imported_like_mercado_a_vista(self) -> None:
        tickers = {trade.ticker for trade in read_statement(FIXTURE).trades}
        assert {"NB1011", "B5P211"} <= tickers  # ETFs de renda fixa vem como "Renda Fixa"


ROWS: list[list[Value]] = [
    trade_row(date(2026, 1, 9), "VWRA11", 20, Decimal("117.37")),
    trade_row(date(2026, 1, 9), "VWRA11", 52, Decimal("117.34")),
    trade_row(date(2026, 9, 28), "VWRA11", 72, Decimal("126.72"), kind="Venda"),
]


class TestVariants:
    def test_serial_dates_read_like_text_dates(self, tmp_path: Path) -> None:
        text = statement_of(tmp_path, ROWS)
        serials = [[serial(datetime.strptime(str(row[0]), "%d/%m/%Y").date()), *row[1:]] for row in ROWS]
        assert statement_of(tmp_path, serials) == text

    def test_inline_strings_read_like_shared_ones(self, tmp_path: Path) -> None:
        assert statement_of(tmp_path, ROWS, strings="inline") == statement_of(tmp_path, ROWS)

    def test_a_header_without_accents_or_case(self, tmp_path: Path) -> None:
        assert statement_of(tmp_path, ROWS, header=HEADER_PLAIN) == statement_of(tmp_path, ROWS)

    def test_columns_in_another_order(self, tmp_path: Path) -> None:
        order = [5, 0, 1, 2, 6, 7, 8]  # sem Prazo e Instituicao, ticker primeiro
        header = tuple(HEADER[index] for index in order)
        rows = [[row[index] for index in order] for row in ROWS]
        assert statement_of(tmp_path, rows, header=header) == statement_of(tmp_path, ROWS)

    def test_a_sheet_that_is_not_sheet1_and_cells_without_references(self, tmp_path: Path) -> None:
        moved = statement_of(
            tmp_path, ROWS, sheet_part="worksheets/negociacao.xml", decoy_sheet=True, with_refs=False, bom=True
        )
        assert moved == statement_of(tmp_path, ROWS)

    def test_float_noise_of_a_resaved_file_is_dropped(self, tmp_path: Path) -> None:
        rows = [trade_row(date(2026, 1, 9), "VWRA11", 20, Decimal("117.37000000000001"), value=Decimal("2347.4"))]
        [trade] = statement_of(tmp_path, rows).trades
        assert trade.unit_price == Decimal("117.37")

    def test_a_round_quantity_keeps_a_plain_exponent(self, tmp_path: Path) -> None:
        [trade] = statement_of(tmp_path, [trade_row(date(2026, 1, 9), "VWRA11", 100, Decimal("10"))]).trades
        assert str(trade.shares) == "100"


class TestMarkets:
    def test_other_markets_are_counted_by_name(self, tmp_path: Path) -> None:
        rows = [
            *ROWS,
            trade_row(date(2026, 1, 9), "PETR4F", 3, Decimal("30"), market="Mercado Fracionário"),
            trade_row(date(2026, 1, 10), "PETR4F", 2, Decimal("30"), market="Mercado Fracionário"),
            trade_row(date(2026, 1, 9), "PETRA300", 100, Decimal("0.5"), market="Opção de Compra"),
        ]
        statement = statement_of(tmp_path, rows)
        assert len(statement.trades) == 3
        assert statement.ignored_markets == (("Mercado Fracionário", 2), ("Opção de Compra", 1))
        assert statement.errors == ()


class TestRefusedFiles:
    def test_the_movement_statement_has_its_own_message(self, tmp_path: Path) -> None:
        rows = [["Credito", "09/01/2026", "Rendimento", "MXRF11 - FII", "NU", 10, 0.1, 1]]
        with pytest.raises(
            ImportFileError, match="extrato de movimentação: proventos e eventos ainda não são importados"
        ):
            statement_of(tmp_path, rows, header=MOVEMENT_HEADER)

    def test_an_unknown_header(self, tmp_path: Path) -> None:
        with pytest.raises(ImportFileError, match=r"cabeçalho não reconhecido.*investidor\.b3\.com\.br"):
            statement_of(tmp_path, ROWS, header=("Data", "Ativo", "Qtd"))

    def test_a_missing_column_is_named(self, tmp_path: Path) -> None:
        header = tuple(column for column in HEADER if column != "Preço")
        rows = [[cell for index, cell in enumerate(row) if index != 7] for row in ROWS]
        with pytest.raises(ImportFileError, match="sem a coluna Preço"):
            statement_of(tmp_path, rows, header=header)

    def test_an_empty_sheet(self, tmp_path: Path) -> None:
        with pytest.raises(ImportFileError, match="planilha vazia"):
            read_statement(build_xlsx(tmp_path / "a.xlsx", []))

    def test_the_path_expands_the_home_directory(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        build_xlsx(tmp_path / "negociacao.xlsx", [list(HEADER), *ROWS])
        assert len(read_statement(Path("~/negociacao.xlsx")).trades) == 3


class TestRowErrors:
    @pytest.mark.parametrize(
        ("row", "reason"),
        [
            (
                trade_row(date(2026, 1, 9), "VWRA11", "vinte", Decimal("117.37"), value=Decimal("1")),
                "quantidade não numérica",
            ),
            (trade_row(date(2026, 1, 9), "VWRA11", 20, None, value=Decimal("1")), "preço não numérico"),
            (
                trade_row(date(2026, 1, 9), "VWRA11", 20, Decimal("117.37"), value=Decimal("2350")),
                "valor difere de quantidade × preço",  # noqa: RUF001
            ),
            (trade_row("31/02/2026", "VWRA11", 20, Decimal("117.37")), "data inválida"),
            (trade_row("2026-01-09", "VWRA11", 20, Decimal("117.37")), "data inválida"),
            (trade_row(Decimal("45000.5"), "VWRA11", 20, Decimal("117.37")), "data inválida"),
            (
                trade_row(date(2026, 1, 9), "VWRA11", 20, Decimal("117.37"), kind="Transferência"),
                "tipo de movimentação não é compra nem venda",
            ),
            (trade_row(date(2026, 1, 9), "VWRA11", 20, Decimal("117.37"), market=""), "mercado vazio"),
            (trade_row(date(2026, 1, 9), "VWRA11", 20, Decimal("117.37123")), "preço com mais de 4 casas"),
            (
                trade_row(date(2026, 1, 9), "VWRA11", Decimal("0.123456789"), Decimal("10")),
                "quantidade com mais de 8 casas",
            ),
            (trade_row(date(2026, 1, 9), "VWRA11", 0, Decimal("10")), "quantidade deve ser maior que zero"),
            (trade_row(date(2026, 1, 9), "VWRA11", 1, Decimal("-10")), "preço deve ser maior que zero"),
        ],
    )
    def test_the_first_problem_is_reported_with_the_row_number(
        self, tmp_path: Path, row: list[Value], reason: str
    ) -> None:
        statement = statement_of(tmp_path, [ROWS[0], row, ROWS[2]])
        assert statement.errors == (RowError(row=3, ticker="VWRA11", reason=reason),)
        assert [trade.row for trade in statement.trades] == [2, 4]
        assert statement.rows == 3

    def test_a_row_without_a_ticker_has_none(self, tmp_path: Path) -> None:
        statement = statement_of(tmp_path, [trade_row(date(2026, 1, 9), "", 20, Decimal("117.37"))])
        assert statement.errors == (RowError(row=2, ticker=None, reason="código de negociação vazio"),)

    def test_a_ticker_is_read_in_upper_case(self, tmp_path: Path) -> None:
        [trade] = statement_of(tmp_path, [trade_row(date(2026, 1, 9), " vwra11 ", 20, Decimal("117.37"))]).trades
        assert trade.ticker == "VWRA11"


def test_key_drops_accents_case_and_extra_spaces() -> None:
    assert key("  Código  de Negociação ") == "codigo de negociacao"
    assert key("PREÇO") == "preco"
    assert key("Mercado à Vista") == "mercado a vista"
