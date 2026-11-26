"""Tests for the standard-library xlsx reader (issue #34)."""

from __future__ import annotations

import zipfile
from decimal import Decimal
from pathlib import Path

import pytest

from bogle.domain.errors import ImportFileError
from bogle.imports.xlsx import Row, read_first_sheet
from tests.xlsx_builder import HEADER, build_xlsx, trade_row

FIXTURE = Path(__file__).parent / "fixtures" / "b3_negociacao.xlsx"

ROWS = [
    list(HEADER),
    trade_row("06/10/2026", "MUND11", 81, Decimal("95.27")),
    trade_row("09/01/2026", "VWRA11", 20, Decimal("117.37")),
]


def cells(rows: list[Row]) -> list[tuple[object, ...]]:
    return [row.cells for row in rows]


class TestRealExport:
    def test_reads_every_row_of_the_b3_file(self) -> None:
        rows = read_first_sheet(FIXTURE)
        assert len(rows) == 22
        assert rows[0].cells == HEADER
        assert [row.number for row in rows] == list(range(1, 23))

    def test_numbers_are_decimals_from_the_cell_text(self) -> None:
        first = read_first_sheet(FIXTURE)[1]
        assert first.cells == (
            "06/10/2026",
            "Compra",
            "Mercado à Vista",
            "-",
            "NU INVESTIMENTOS S.A. - CTVM",
            "MUND11",
            Decimal("81"),
            Decimal("95.27"),
            Decimal("7716.87"),
        )


class TestVariants:
    def test_inline_strings_read_like_shared_ones(self, tmp_path: Path) -> None:
        shared = read_first_sheet(build_xlsx(tmp_path / "a.xlsx", ROWS))
        inline = read_first_sheet(build_xlsx(tmp_path / "b.xlsx", ROWS, strings="inline"))
        assert inline == shared

    def test_cells_without_references_are_placed_in_order(self, tmp_path: Path) -> None:
        with_refs = read_first_sheet(build_xlsx(tmp_path / "a.xlsx", ROWS))
        without = read_first_sheet(build_xlsx(tmp_path / "b.xlsx", ROWS, with_refs=False))
        assert without == with_refs

    def test_the_first_sheet_is_found_through_the_workbook(self, tmp_path: Path) -> None:
        # A aba-isca fica em sheet1.xml: quem supoe o nome le a planilha errada.
        path = build_xlsx(tmp_path / "a.xlsx", ROWS, sheet_part="worksheets/negociacao.xml", decoy_sheet=True)
        assert cells(read_first_sheet(path)) == cells(read_first_sheet(build_xlsx(tmp_path / "b.xlsx", ROWS)))

    def test_an_absolute_target_is_rooted_at_the_package(self, tmp_path: Path) -> None:
        path = build_xlsx(tmp_path / "a.xlsx", ROWS, sheet_part="/xl/worksheets/negociacao.xml")
        assert cells(read_first_sheet(path)) == [tuple(row) for row in ROWS]

    def test_a_bom_in_the_parts_is_read_past(self, tmp_path: Path) -> None:
        plain = read_first_sheet(build_xlsx(tmp_path / "a.xlsx", ROWS))
        assert read_first_sheet(build_xlsx(tmp_path / "b.xlsx", ROWS, bom=True)) == plain

    def test_empty_rows_are_dropped_and_the_numbering_kept(self, tmp_path: Path) -> None:
        rows = [list(HEADER), [None, "", "  "], ROWS[1]]
        read = read_first_sheet(build_xlsx(tmp_path / "a.xlsx", rows))
        assert [row.number for row in read] == [1, 3]

    def test_a_number_never_goes_through_float(self, tmp_path: Path) -> None:
        path = build_xlsx(tmp_path / "a.xlsx", [["Valor"], [Decimal("0.1")], ["x"]])
        assert read_first_sheet(path)[1].cells == (Decimal("0.1"),)

    def test_text_typed_cells_stay_text(self, tmp_path: Path) -> None:
        path = build_xlsx(tmp_path / "a.xlsx", [["Valor"]])
        rebuilt = tmp_path / "b.xlsx"
        with zipfile.ZipFile(path) as source, zipfile.ZipFile(rebuilt, "w") as target:
            for item in source.namelist():
                content = source.read(item)
                if item == "xl/worksheets/sheet1.xml":
                    content = content.replace(
                        b"</sheetData>",
                        b'<row r="2"><c r="A2" t="str"><v>81</v></c><c r="B2"><v>abc</v></c><c r="C2"/></row></sheetData>',
                    )
                target.writestr(item, content)
        assert read_first_sheet(rebuilt)[1].cells == ("81", "abc", None)


def _write(path: Path, entries: dict[str, str | bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return path


class TestRefusals:
    def test_a_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(ImportFileError, match="arquivo não encontrado"):
            read_first_sheet(tmp_path / "nada.xlsx")

    def test_a_csv_is_not_an_xlsx(self, tmp_path: Path) -> None:
        path = tmp_path / "negociacao.csv"
        path.write_text(";".join(HEADER) + "\n06/10/2026;Compra\n", encoding="utf-8")
        with pytest.raises(ImportFileError, match=r"negociacao\.csv não é um \.xlsx válido: use o \.xlsx baixado"):
            read_first_sheet(path)

    def test_an_old_xls_is_not_an_xlsx(self, tmp_path: Path) -> None:
        path = tmp_path / "negociacao.xls"
        path.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"\0" * 504)
        with pytest.raises(ImportFileError, match=r"não é um \.xlsx válido"):
            read_first_sheet(path)

    def test_an_xls_renamed_to_xlsx_is_refused_by_content(self, tmp_path: Path) -> None:
        path = tmp_path / "negociacao.xlsx"
        path.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"\0" * 504)
        with pytest.raises(ImportFileError, match=r"não é um \.xlsx válido"):
            read_first_sheet(path)

    def test_a_zip_without_a_workbook(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "a.xlsx", {"word/document.xml": "<document/>"})
        with pytest.raises(ImportFileError, match=r"não é um \.xlsx válido"):
            read_first_sheet(path)

    def test_broken_xml(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "a.xlsx", {"xl/workbook.xml": "<workbook><sheets>"})
        with pytest.raises(ImportFileError, match=r"não é um \.xlsx válido"):
            read_first_sheet(path)

    def test_a_shared_string_that_does_not_exist(self, tmp_path: Path) -> None:
        path = build_xlsx(tmp_path / "a.xlsx", ROWS)
        broken = tmp_path / "b.xlsx"
        with zipfile.ZipFile(path) as source, zipfile.ZipFile(broken, "w") as target:
            for item in source.namelist():
                content = source.read(item)
                if item == "xl/worksheets/sheet1.xml":
                    content = content.replace(b'<c r="A1" t="s"><v>0</v>', b'<c r="A1" t="s"><v>999</v>')
                target.writestr(item, content)
        with pytest.raises(ImportFileError, match=r"não é um \.xlsx válido"):
            read_first_sheet(broken)
