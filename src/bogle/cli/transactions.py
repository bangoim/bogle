from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

import typer
from rich.console import Console
from rich.table import Table

from bogle.cli.parsing import parse_date, parse_decimal
from bogle.db import DEFAULT_TIMEZONE, get_connection
from bogle.domain.errors import ValidationError
from bogle.domain.transactions import Transaction, TransactionType
from bogle.repositories.transactions import TransactionRepository


class IncomeType(StrEnum):
    """Income subset of ``TransactionType``, for the ``--type`` choices.

    Kept separate so ``bogle income --help`` does not offer BUY/SELL.
    """

    DIVIDEND = "DIVIDEND"
    JCP = "JCP"
    RENDIMENTO = "RENDIMENTO"
    INTEREST = "INTEREST"


def _resolve_date(value: str | None) -> datetime:
    """``--date`` informado (ISO) ou agora em America/Sao_Paulo."""
    if value is not None:
        return parse_date(value, "--date")
    return datetime.now(tz=ZoneInfo(DEFAULT_TIMEZONE))


def _fmt(value: Decimal) -> str:
    """Exibe sem a escala crua do NUMERIC (100.00000000 -> 100)."""
    return format(value.normalize(), "f")


def _echo_recorded(tx: Transaction) -> None:
    typer.echo(f"transacao {tx.id} registrada: {tx.transaction_type} {tx.ticker} em {tx.date:%Y-%m-%d}.")


def buy(
    ticker: str = typer.Argument(..., help="Ticker do ativo (precisa estar cadastrado)."),
    shares: str = typer.Option(..., "--shares", "-s", help="Quantidade comprada."),
    price: str = typer.Option(..., "--price", "-p", help="Preco unitario pago."),
    fees: str = typer.Option("0", "--fees", help="Taxas/corretagem da operacao."),
    date: str | None = typer.Option(None, "--date", help="Data da operacao (YYYY-MM-DD). Default: hoje."),
) -> None:
    conn = get_connection()
    try:
        tx = TransactionRepository(conn).add_buy(
            ticker,
            _resolve_date(date),
            shares=parse_decimal(shares, "--shares"),
            unit_price=parse_decimal(price, "--price"),
            fees=parse_decimal(fees, "--fees"),
        )
    finally:
        conn.close()
    _echo_recorded(tx)
    typer.echo(
        f"custo total: {_fmt(tx.total_cost)} ({_fmt(tx.shares)} x {_fmt(tx.unit_price)} + {_fmt(tx.fees)} de fees)."
    )


def sell(
    ticker: str = typer.Argument(..., help="Ticker do ativo."),
    shares: str = typer.Option(..., "--shares", "-s", help="Quantidade vendida."),
    price: str = typer.Option(..., "--price", "-p", help="Preco unitario de venda."),
    fees: str = typer.Option("0", "--fees", help="Taxas/corretagem da operacao."),
    tax_withheld: str = typer.Option("0", "--tax-withheld", help="IR retido na fonte (dedo-duro de 0,005% em vendas)."),
    date: str | None = typer.Option(None, "--date", help="Data da operacao (YYYY-MM-DD). Default: hoje."),
) -> None:
    conn = get_connection()
    try:
        tx = TransactionRepository(conn).add_sale(
            ticker,
            _resolve_date(date),
            shares=parse_decimal(shares, "--shares"),
            unit_price=parse_decimal(price, "--price"),
            fees=parse_decimal(fees, "--fees"),
            tax_withheld=parse_decimal(tax_withheld, "--tax-withheld"),
        )
    finally:
        conn.close()
    _echo_recorded(tx)
    typer.echo(f"produto bruto da venda: {_fmt(tx.total_investment)}; custo da operacao: {_fmt(tx.total_cost)}.")


def income(
    ticker: str = typer.Argument(..., help="Ticker do ativo."),
    income_type: IncomeType = typer.Option(  # noqa: B008 — padrao do typer, OptionInfo e sentinela imutavel
        ...,
        "--type",
        "-t",
        case_sensitive=False,
        help="Tipo do provento.",
    ),
    amount: str = typer.Option(..., "--amount", "-a", help="Valor bruto recebido."),
    tax_withheld: str | None = typer.Option(
        None,
        "--tax-withheld",
        help="IR retido na fonte. Obrigatorio para JCP; nao se aplica a RENDIMENTO.",
    ),
    date: str | None = typer.Option(None, "--date", help="Data do recebimento (YYYY-MM-DD). Default: hoje."),
) -> None:
    # JCP sempre tem 15% retido na fonte; RENDIMENTO de FII e isento para PF.
    if income_type is IncomeType.JCP and tax_withheld is None:
        raise ValidationError("--tax-withheld e obrigatorio para JCP (IR de 15% retido na fonte).")
    if income_type is IncomeType.RENDIMENTO and tax_withheld is not None:
        raise ValidationError("--tax-withheld nao se aplica a RENDIMENTO (isento para PF).")

    amount_dec = parse_decimal(amount, "--amount")
    tax_dec = parse_decimal(tax_withheld, "--tax-withheld") if tax_withheld is not None else None
    when = _resolve_date(date)

    conn = get_connection()
    try:
        repo = TransactionRepository(conn)
        if income_type is IncomeType.DIVIDEND:
            tx = repo.add_dividend(ticker, when, amount_dec, tax_withheld=tax_dec or Decimal("0"))
        elif income_type is IncomeType.JCP:
            assert tax_dec is not None  # garantido pela validacao acima
            tx = repo.add_jcp(ticker, when, amount_dec, tax_dec)
        elif income_type is IncomeType.RENDIMENTO:
            tx = repo.add_rendimento(ticker, when, amount_dec)
        else:
            tx = repo.add_interest(ticker, when, amount_dec, tax_withheld=tax_dec or Decimal("0"))
    finally:
        conn.close()
    _echo_recorded(tx)
    typer.echo(f"valor bruto: {_fmt(tx.total_investment)}; IR retido: {_fmt(tx.tax_withheld)}.")


def list_transactions(
    ticker: str | None = typer.Argument(None, help="Filtra por ticker (opcional)."),
) -> None:
    conn = get_connection()
    try:
        transactions = TransactionRepository(conn).list(ticker)
    finally:
        conn.close()

    if not transactions:
        suffix = f" para {ticker.upper()}" if ticker else ""
        typer.echo(f"Nenhuma transacao registrada{suffix}.")
        return

    table = Table(title="Transacoes", title_style="bold")
    table.add_column("ID", justify="right")
    table.add_column("Data", no_wrap=True)
    table.add_column("Tipo")
    table.add_column("Ticker", style="cyan", no_wrap=True)
    table.add_column("Qtd", justify="right")
    table.add_column("Preco", justify="right")
    table.add_column("Valor", justify="right")
    table.add_column("Fees", justify="right")
    table.add_column("IR retido", justify="right")
    for tx in transactions:
        is_trade = tx.transaction_type in (TransactionType.BUY, TransactionType.SELL)
        table.add_row(
            str(tx.id),
            f"{tx.date:%Y-%m-%d}",
            tx.transaction_type,
            tx.ticker,
            _fmt(tx.shares) if is_trade else "-",
            _fmt(tx.unit_price) if is_trade else "-",
            _fmt(tx.total_investment),
            _fmt(tx.fees),
            _fmt(tx.tax_withheld),
        )
    Console().print(table)


def remove(
    transaction_id: int = typer.Argument(..., help="ID da transacao (veja 'bogle transactions')."),
) -> None:
    conn = get_connection()
    try:
        TransactionRepository(conn).delete(transaction_id)
    finally:
        conn.close()
    typer.echo(f"transacao {transaction_id} removida.")
