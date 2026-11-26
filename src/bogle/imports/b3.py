"""The B3's Negociacao statement, as trades (issue #34).

The Area do Investidor exports two statements with different headers; which one a
file is comes from its header, never from its name. Negociacao has one row per
execution, dated on the trade date — the date average cost, the sale guard and
the TWR need — and is the one imported here. Movimentacao (income and corporate
events, issue #92) is recognized and refused with its own message until it has
an importer.

Columns are matched by name, without accents or case, in whatever order they
come; ``Prazo/Vencimento`` and ``Instituição`` are read past (bogle does not
model brokers). Every row is checked on its own and the first problem found
becomes a :class:`RowError` with the spreadsheet's row number: nothing here knows
about the ledger, so whether a ticker is skipped because of it is decided by
:mod:`bogle.imports.plan`.

Pure, apart from :func:`read_statement`, which opens the file.
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Context, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from bogle.db import DEFAULT_TIMEZONE
from bogle.domain.errors import ImportFileError
from bogle.domain.transactions import TransactionType
from bogle.imports.xlsx import PORTAL_HINT, Cell, Row, read_first_sheet

DATE = "data do negocio"
KIND = "tipo de movimentacao"
MARKET = "mercado"
TICKER = "codigo de negociacao"
QUANTITY = "quantidade"
PRICE = "preco"
VALUE = "valor"

_REQUIRED = (DATE, KIND, MARKET, TICKER, QUANTITY, PRICE, VALUE)
_HEADER_LABELS = {
    DATE: "Data do Negócio",
    KIND: "Tipo de Movimentação",
    MARKET: "Mercado",
    TICKER: "Código de Negociação",
    QUANTITY: "Quantidade",
    PRICE: "Preço",
    VALUE: "Valor",
}

MOVEMENT_MARKER = "entrada/saida"
"""The column that tells the Movimentacao statement apart (issue #92)."""

IMPORTED_MARKETS = frozenset({"mercado a vista", "renda fixa"})
"""Fixed-income ETFs come as ``Renda Fixa``; anything else (fractional, options,
forward) is counted by name and left out."""

_KINDS = {"compra": TransactionType.BUY, "venda": TransactionType.SELL}

_EXCEL_EPOCH = date(1899, 12, 30)
_MAX_SERIAL = 2958465  # 9999-12-31

_SIGNIFICANT = Context(prec=15)
"""A number re-saved by Excel carries the noise of a double (``95.27000000000001``);
15 significant digits is what a double holds exactly."""

PRICE_PLACES = 4
"""``unit_price`` is ``NUMERIC(20, 4)``: a fifth place would be rounded by the database."""
QUANTITY_PLACES = 8
"""``shares`` is ``NUMERIC(20, 8)``."""

_VALUE_TOLERANCE = Decimal("0.01")

_AMOUNTS = (
    (QUANTITY, "quantidade", "não numérica"),
    (PRICE, "preço", "não numérico"),
    (VALUE, "valor", "não numérico"),
)


@dataclass(frozen=True, slots=True)
class Trade:
    """One execution, as the statement states it."""

    row: int
    when: datetime
    """The trade date at midnight, America/Sao_Paulo."""
    kind: TransactionType
    ticker: str
    shares: Decimal
    unit_price: Decimal


@dataclass(frozen=True, slots=True)
class RowError:
    """A row that could not be read, and why."""

    row: int
    ticker: str | None
    """The row's ticker when it could be read; its whole ticker is then left out."""
    reason: str


@dataclass(frozen=True, slots=True)
class Statement:
    """Everything a Negociacao file says, row by row, before the ledger is consulted."""

    trades: tuple[Trade, ...]
    ignored_markets: tuple[tuple[str, int], ...]
    """``(market as written, rows)`` for each market that is not imported."""
    errors: tuple[RowError, ...]
    rows: int
    """Data rows in the file (the header excluded), whatever became of them."""


def read_statement(path: Path) -> Statement:
    """Open ``path`` (``~`` expanded) and read it as a Negociacao statement."""
    return parse_negociacao(read_first_sheet(path.expanduser()))


def key(text: str) -> str:
    """How headers and labels are compared: no accents, no case, single spaces."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(stripped.casefold().split())


def parse_negociacao(rows: Sequence[Row]) -> Statement:
    """Recognize the statement by its header and read every row after it.

    ``ImportFileError`` when the first row is not the Negociacao header — before
    any data row is looked at.
    """
    if not rows:
        raise ImportFileError(f"planilha vazia: {PORTAL_HINT}")
    header, *data = rows
    columns = _columns(header)
    trades: list[Trade] = []
    errors: list[RowError] = []
    ignored: Counter[str] = Counter()
    for row in data:
        outcome = _read_row(row, columns)
        if isinstance(outcome, Trade):
            trades.append(outcome)
        elif isinstance(outcome, RowError):
            errors.append(outcome)
        else:
            ignored[outcome] += 1
    return Statement(
        trades=tuple(trades),
        ignored_markets=tuple(sorted(ignored.items())),
        errors=tuple(errors),
        rows=len(data),
    )


def _columns(header: Row) -> dict[str, int]:
    names: dict[str, int] = {}
    for index, cell in enumerate(header.cells):
        if isinstance(cell, str):
            names.setdefault(key(cell), index)
    if TICKER not in names:
        if MOVEMENT_MARKER in names:
            raise ImportFileError("extrato de movimentação: proventos e eventos ainda não são importados.")
        raise ImportFileError(f"cabeçalho não reconhecido como extrato de Negociação da B3: {PORTAL_HINT}")
    missing = [_HEADER_LABELS[column] for column in _REQUIRED if column not in names]
    if missing:
        raise ImportFileError(f"extrato de Negociação sem a coluna {', '.join(missing)}: {PORTAL_HINT}")
    return names


def _cell(row: Row, column: int) -> Cell:
    return row.cells[column] if column < len(row.cells) else None


def _text(value: Cell) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    return value.strip()


def _read_row(row: Row, columns: dict[str, int]) -> Trade | RowError | str:
    """The row as a trade, an error, or the name of the market it was left out for."""
    ticker = _text(_cell(row, columns[TICKER])).upper() or None

    def fail(reason: str) -> RowError:
        return RowError(row=row.number, ticker=ticker, reason=reason)

    market = _text(_cell(row, columns[MARKET]))
    if not market:
        return fail("mercado vazio")
    if key(market) not in IMPORTED_MARKETS:
        return market
    kind = _KINDS.get(key(_text(_cell(row, columns[KIND]))))
    if kind is None:
        return fail("tipo de movimentação não é compra nem venda")
    if ticker is None:
        return fail("código de negociação vazio")
    when = _trade_date(_cell(row, columns[DATE]))
    if when is None:
        return fail("data inválida")
    numbers: list[Decimal] = []
    for column, label, numeric in _AMOUNTS:
        number = _number(_cell(row, columns[column]))
        if number is None:
            return fail(f"{label} {numeric}")
        if number <= 0:
            return fail(f"{label} deve ser maior que zero")
        numbers.append(number)
    shares, price, value = numbers
    if _places(price) > PRICE_PLACES:
        return fail(f"preço com mais de {PRICE_PLACES} casas")
    if _places(shares) > QUANTITY_PLACES:
        return fail(f"quantidade com mais de {QUANTITY_PLACES} casas")
    if abs(shares * price - value) > _VALUE_TOLERANCE:
        return fail("valor difere de quantidade × preço")  # noqa: RUF001 — sinal de vezes, como na spec
    return Trade(row=row.number, when=when, kind=kind, ticker=ticker, shares=shares, unit_price=price)


def _trade_date(value: Cell) -> datetime | None:
    """``dd/mm/yyyy`` as the B3 writes it, or an Excel serial from a re-saved file."""
    if isinstance(value, str):
        try:
            day = datetime.strptime(value.strip(), "%d/%m/%Y").date()
        except ValueError:
            return None
    elif isinstance(value, Decimal):
        if not value.is_finite() or value != value.to_integral_value() or not (0 < value <= _MAX_SERIAL):
            return None
        day = _EXCEL_EPOCH + timedelta(days=int(value))
    else:
        return None
    # Meia-noite de Sao Paulo, o mesmo que `cli.parsing.parse_date` faz com uma
    # data digitada; montada aqui porque o pacote da CLI importa este modulo.
    return datetime.combine(day, time(), tzinfo=ZoneInfo(DEFAULT_TIMEZONE))


def _number(value: Cell) -> Decimal | None:
    """A number cell, without the noise of a float that went through Excel."""
    if not isinstance(value, Decimal) or not value.is_finite():
        return None
    cleaned = _SIGNIFICANT.create_decimal(value).normalize()
    # normalize() escreve 100 como 1E+2; o expoente volta a zero para o numero
    # ser o mesmo que se digitaria.
    exponent = cleaned.as_tuple().exponent
    if isinstance(exponent, int) and exponent > 0:
        cleaned = cleaned.quantize(Decimal(1))
    return cleaned


def _places(value: Decimal) -> int:
    exponent = value.as_tuple().exponent
    assert isinstance(exponent, int)  # numeros finitos tem expoente inteiro
    return max(-exponent, 0)
