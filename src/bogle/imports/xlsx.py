"""A minimal xlsx reader on the standard library (issue #34).

The B3 portal exports its statements as ``.xlsx`` and nothing else, and the user
has no spreadsheet program to convert them: the file has to be read as it comes.
It is simple enough for ``zipfile`` + ``xml.etree`` — one sheet, strings in the
shared table, numbers as plain cells — so no dependency comes in for it.

Only what is needed to get cell values out of the first sheet:

- the first sheet is the first ``<sheet>`` of ``xl/workbook.xml``, followed
  through its relationship (not assumed to be ``sheet1.xml``);
- strings come from the shared table (``t="s"``) or inline (``inlineStr``,
  ``str``); numbers are read as ``Decimal`` from the cell's text, never through
  ``float``;
- a cell's column comes from its reference (``C7``) when it has one, and a row's
  number from its ``r`` — the ``<dimension>`` the B3's generator writes is wrong
  (``A1``), so nothing here trusts it;
- rows with nothing in them are dropped, keeping the numbering of the others.

Dates are not interpreted here: a cell is text or a number, and what it means is
the statement's business (:mod:`bogle.imports.b3`).
"""

from __future__ import annotations

import posixpath
import zipfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree as ET

from bogle.domain.errors import ImportFileError

Cell = str | Decimal | None
"""A cell's value: text, a number, or nothing."""

PORTAL_HINT = "use o .xlsx baixado em investidor.b3.com.br (Extratos → Negociação → baixar → Excel), sem converter nem salvar de novo."
"""What to do about a file that cannot be read, said by every refusal."""

_WORKBOOK = "xl/workbook.xml"
_WORKBOOK_RELS = "xl/_rels/workbook.xml.rels"
_SHARED_STRINGS = "xl/sharedStrings.xml"
_SHARED_STRINGS_TYPE = "/sharedStrings"
_TEXT_TYPES = frozenset({"str", "e", "b", "d"})


@dataclass(frozen=True, slots=True)
class Row:
    """One row of the sheet that has something in it."""

    number: int
    """The row number the spreadsheet shows (1-based), for error messages."""
    cells: tuple[Cell, ...]
    """Values by column, ``A`` first; a gap is ``None``."""


def read_first_sheet(path: Path) -> list[Row]:
    """Every non-empty row of the workbook's first sheet.

    ``ImportFileError`` for anything that is not a readable xlsx — a missing
    file, a CSV, an old ``.xls``, a zip without a workbook, broken XML — checked
    by content, not by the extension.
    """
    if not path.is_file():
        raise ImportFileError(f"arquivo não encontrado: {path}.")
    if not zipfile.is_zipfile(path):
        raise _not_xlsx(path)
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if _WORKBOOK not in names:
                raise _not_xlsx(path)
            relationships = _relationships(archive, names)
            sheet = _first_sheet_part(archive, relationships)
            shared = _shared_strings(archive, names, relationships)
            return _rows(ET.fromstring(archive.read(sheet)), shared)
    except (zipfile.BadZipFile, KeyError, ET.ParseError, ValueError, IndexError):
        raise _not_xlsx(path) from None


def _not_xlsx(path: Path) -> ImportFileError:
    return ImportFileError(f"{path.name} não é um .xlsx válido: {PORTAL_HINT}")


def _local(tag: str) -> str:
    """``{namespace}row`` -> ``row``: transitional and strict OOXML differ only there."""
    return tag.rpartition("}")[2]


def _attribute(element: ET.Element, name: str) -> str | None:
    """An attribute by local name, whatever namespace prefixes it (``r:id``)."""
    for key, value in element.attrib.items():
        if _local(key) == name:
            return value
    return None


def _part_path(target: str) -> str:
    """A relationship target as a name inside the zip.

    Absolute targets (``/xl/worksheets/sheet1.xml``) are rooted at the package;
    relative ones at ``xl/``, where the workbook lives.
    """
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join("xl", target))


def _relationships(archive: zipfile.ZipFile, names: set[str]) -> dict[str, tuple[str, str]]:
    """The workbook's relationships: ``{id: (type, part)}``.

    Parsed from bytes, not text: the B3's file carries a BOM, which the XML
    parser handles when it sees the encoding itself.
    """
    if _WORKBOOK_RELS not in names:
        return {}
    root = ET.fromstring(archive.read(_WORKBOOK_RELS))
    found: dict[str, tuple[str, str]] = {}
    for relation in root:
        if _local(relation.tag) != "Relationship":
            continue
        rel_id, target = relation.get("Id"), relation.get("Target")
        if rel_id and target:
            found[rel_id] = (relation.get("Type", ""), _part_path(target))
    return found


def _first_sheet_part(archive: zipfile.ZipFile, relationships: dict[str, tuple[str, str]]) -> str:
    workbook = ET.fromstring(archive.read(_WORKBOOK))
    sheet = next((element for element in workbook.iter() if _local(element.tag) == "sheet"), None)
    if sheet is None:
        raise KeyError("sheet")
    rel_id = _attribute(sheet, "id")
    if rel_id is None or rel_id not in relationships:
        raise KeyError(rel_id)
    return relationships[rel_id][1]


def _shared_strings(archive: zipfile.ZipFile, names: set[str], relationships: dict[str, tuple[str, str]]) -> list[str]:
    part = next(
        (part for kind, part in relationships.values() if kind.endswith(_SHARED_STRINGS_TYPE)),
        _SHARED_STRINGS,
    )
    if part not in names:
        return []
    root = ET.fromstring(archive.read(part))
    return [_text_of(item) for item in root if _local(item.tag) == "si"]


def _text_of(item: ET.Element) -> str:
    """A string item's text: its ``<t>``, or the ``<t>`` of each rich-text run.

    Phonetic hints (``<rPh>``) are left out — they are reading aids, not text.
    """
    parts: list[str] = []
    for child in item:
        tag = _local(child.tag)
        if tag == "t":
            parts.append(child.text or "")
        elif tag == "r":
            parts.extend(t.text or "" for t in child if _local(t.tag) == "t")
    return "".join(parts)


def _column_index(reference: str) -> int | None:
    """``C7`` -> 2 (0-based); ``None`` when the reference has no letters."""
    index = 0
    letters = 0
    for char in reference:
        if not ("A" <= char.upper() <= "Z"):
            break
        index = index * 26 + (ord(char.upper()) - ord("A") + 1)
        letters += 1
    return index - 1 if letters else None


def _rows(sheet: ET.Element, shared: list[str]) -> list[Row]:
    data = next((element for element in sheet if _local(element.tag) == "sheetData"), None)
    if data is None:
        return []
    rows: list[Row] = []
    number = 0
    for row in data:
        if _local(row.tag) != "row":
            continue
        reference = row.get("r")
        number = int(reference) if reference else number + 1
        values: dict[int, Cell] = {}
        column = -1
        for cell in row:
            if _local(cell.tag) != "c":
                continue
            position = _column_index(cell.get("r") or "")
            column = position if position is not None else column + 1
            values[column] = _value(cell, shared)
        if not any(_filled(value) for value in values.values()):
            continue
        width = max(values) + 1
        rows.append(Row(number=number, cells=tuple(values.get(index) for index in range(width))))
    return rows


def _filled(value: Cell) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _value(cell: ET.Element, shared: list[str]) -> Cell:
    kind = cell.get("t", "n")
    if kind == "inlineStr":
        inline = next((child for child in cell if _local(child.tag) == "is"), None)
        return _text_of(inline) if inline is not None else None
    raw = next((child.text for child in cell if _local(child.tag) == "v"), None)
    if raw is None:
        return None
    if kind == "s":
        index = int(raw)
        if index < 0:
            raise IndexError(index)
        return shared[index]
    if kind in _TEXT_TYPES:
        return raw
    try:
        return Decimal(raw.strip())
    except InvalidOperation:
        return raw
