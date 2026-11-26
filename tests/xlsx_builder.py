"""A minimal xlsx writer for the import tests (issue #34).

The real export (``tests/fixtures/b3_negociacao.xlsx``) covers what the B3 writes;
this builds the cases it does not have — inline strings, serial dates, a sheet
that is not ``sheet1.xml``, cells without a reference, a BOM, rows with errors —
with the same zip layout, so the reader is exercised on files and not on mocks.
"""

from __future__ import annotations

import zipfile
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal
from xml.sax.saxutils import escape

Value = str | Decimal | int | None

HEADER: tuple[str, ...] = (
    "Data do Negócio",
    "Tipo de Movimentação",
    "Mercado",
    "Prazo/Vencimento",
    "Instituição",
    "Código de Negociação",
    "Quantidade",
    "Preço",
    "Valor",
)
HEADER_PLAIN: tuple[str, ...] = (
    "DATA DO NEGOCIO",
    "tipo de movimentacao",
    "Mercado",
    "Prazo/Vencimento",
    "Instituicao",
    "Codigo  de Negociacao",
    "Quantidade",
    "Preco",
    "Valor",
)
MOVEMENT_HEADER: tuple[str, ...] = (
    "Entrada/Saída",
    "Data",
    "Movimentação",
    "Produto",
    "Instituição",
    "Quantidade",
    "Preço unitário",
    "Valor da Operação",
)

_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
_BOM = "﻿"


def serial(day: date) -> Decimal:
    """``day`` as an Excel serial number (base 1899-12-30)."""
    return Decimal((day - date(1899, 12, 30)).days)


def trade_row(
    day: date | Value,
    ticker: str,
    shares: Value,
    price: Value,
    *,
    kind: str = "Compra",
    market: str = "Mercado à Vista",
    value: Value = None,
) -> list[Value]:
    """One Negociacao row; ``value`` defaults to ``shares × price``."""  # noqa: RUF002 — sinal de vezes
    when = day.strftime("%d/%m/%Y") if isinstance(day, date) else day
    if value is None and isinstance(shares, Decimal | int) and isinstance(price, Decimal | int):
        value = Decimal(shares) * Decimal(price)
    return [when, kind, market, "-", "NU INVESTIMENTOS S.A. - CTVM", ticker, shares, price, value]


def _column(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(ord("A") + rest) + letters
    return letters


def _sheet(rows: Sequence[Sequence[Value]], *, strings: str, shared: list[str], with_refs: bool) -> str:
    out: list[str] = [
        f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{_MAIN}"><dimension ref="A1"/><sheetData>'
    ]
    for number, row in enumerate(rows, start=1):
        out.append(f'<row r="{number}">' if with_refs else "<row>")
        for index, value in enumerate(row):
            ref = f' r="{_column(index)}{number}"' if with_refs else ""
            if value is None:
                out.append(f"<c{ref}/>")
            elif isinstance(value, str):
                if strings == "inline":
                    out.append(f'<c{ref} t="inlineStr"><is><t>{escape(value)}</t></is></c>')
                else:
                    if value not in shared:
                        shared.append(value)
                    out.append(f'<c{ref} t="s"><v>{shared.index(value)}</v></c>')
            else:
                out.append(f"<c{ref}><v>{value}</v></c>")
        out.append("</row>")
    out.append("</sheetData></worksheet>")
    return "".join(out)


def build_xlsx(
    path: Path,
    rows: Sequence[Sequence[Value]],
    *,
    strings: Literal["shared", "inline"] = "shared",
    sheet_part: str = "worksheets/sheet1.xml",
    decoy_sheet: bool = False,
    with_refs: bool = True,
    bom: bool = False,
) -> Path:
    """Write ``rows`` (header included) as the first sheet of a workbook at ``path``.

    ``decoy_sheet`` adds a second sheet at ``worksheets/sheet1.xml`` with junk in
    it, so a reader that assumes the first sheet is ``sheet1.xml`` reads the wrong
    one (``sheet_part`` must then be something else). ``bom`` prefixes the
    relationships and the shared strings with a BOM, as the B3's generator does.
    """
    shared: list[str] = []
    sheet = _sheet(rows, strings=strings, shared=shared, with_refs=with_refs)
    prefix = _BOM if bom else ""
    sheets = ['<sheet name="Negociação" sheetId="1" r:id="rId3"/>']
    relationships = [
        f'<Relationship Id="rId1" Type="{_REL}/sharedStrings" Target="sharedStrings.xml"/>',
        f'<Relationship Id="rId3" Type="{_REL}/worksheet" Target="{sheet_part}"/>',
    ]
    # Um alvo absoluto (/xl/...) parte da raiz do pacote; um relativo, de xl/.
    parts = {sheet_part.lstrip("/") if sheet_part.startswith("/") else f"xl/{sheet_part}": sheet}
    if decoy_sheet:
        sheets.append('<sheet name="Outra" sheetId="2" r:id="rId4"/>')
        relationships.append(f'<Relationship Id="rId4" Type="{_REL}/worksheet" Target="worksheets/sheet1.xml"/>')
        parts["xl/worksheets/sheet1.xml"] = _sheet(
            [["isca"], ["nada"]], strings="inline", shared=[], with_refs=with_refs
        )
    workbook = (
        f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="{_MAIN}" xmlns:r="{_REL}">'
        f"<sheets>{''.join(sheets)}</sheets></workbook>"
    )
    rels = f'{prefix}<?xml version="1.0" encoding="utf-8"?><Relationships xmlns="{_PKG}">{"".join(relationships)}</Relationships>'
    strings_xml = (
        f'{prefix}<?xml version="1.0" encoding="UTF-8"?><sst xmlns="{_MAIN}">'
        + "".join(f'<si><t xml:space="preserve">{escape(text)}</t></si>' for text in shared)
        + "</sst>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types/>')
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", rels.encode("utf-8"))
        archive.writestr("xl/sharedStrings.xml", strings_xml.encode("utf-8"))
        for name, content in parts.items():
            archive.writestr(name, content)
    return path
