from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

import typer
from rich.console import Console
from rich.table import Table

from bogle.cli.parsing import parse_date, parse_rate, parse_weight
from bogle.db import get_connection
from bogle.domain.assets import AssetType, Indexer
from bogle.domain.errors import AssetNotFoundError, ValidationError
from bogle.domain.validation import validate_asset_metadata, validate_type_change
from bogle.repositories.assets import AssetRepository


def _parse_provided[T](
    value: str | None,
    parser: Callable[[str], T],
    parse_errors: list[str],
    placeholder: T,
) -> T | None:
    """Parse an optional CLI value, accumulating failures.

    On parse failure the error is recorded and ``placeholder`` is returned
    so the validator still sees the field as *provided* (and can report
    missing/irrelevant fields in the same message). The placeholder is
    never persisted: any accumulated error aborts before the database.
    """
    if value is None:
        return None
    try:
        return parser(value)
    except ValidationError as exc:
        parse_errors.append(str(exc))
        return placeholder


def add(
    ticker: str = typer.Argument(..., help="Ticker do ativo (ex: VTI)."),
    weight: str = typer.Option(
        ...,
        "--weight",
        "-w",
        help="Peso-alvo em decimal entre 0 e 1 (ex: 0.6 = 60%).",
    ),
    asset_type: AssetType = typer.Option(  # noqa: B008 — padrao do typer, OptionInfo e sentinela imutavel
        AssetType.STOCK,
        "--type",
        "-t",
        case_sensitive=False,
        help="Tipo do ativo.",
    ),
    issuer: str | None = typer.Option(
        None,
        "--issuer",
        help="Banco/emissor (renda fixa privada).",
    ),
    indexer: Indexer | None = typer.Option(  # noqa: B008 — padrao do typer, OptionInfo e sentinela imutavel
        None,
        "--indexer",
        case_sensitive=False,
        help="Indexador (renda fixa pos-fixada).",
    ),
    rate: str | None = typer.Option(
        None,
        "--rate",
        help="Taxa contratada em decimal (ex: 1.10 = 110% do CDI).",
    ),
    prefixed: bool | None = typer.Option(
        None,
        "--prefixed/--no-prefixed",
        help="Titulo prefixado (sem indexador). Default: pos-fixado.",
    ),
    daily_liquidity: bool | None = typer.Option(
        None,
        "--daily-liquidity/--no-daily-liquidity",
        help="Liquidez diaria (renda fixa privada).",
    ),
    purchase_date: str | None = typer.Option(
        None,
        "--purchase-date",
        help="Data da compra (YYYY-MM-DD).",
    ),
    maturity_date: str | None = typer.Option(
        None,
        "--maturity-date",
        help="Data de vencimento (YYYY-MM-DD).",
    ),
) -> None:
    weight_dec = parse_weight(weight, "--weight")
    parse_errors: list[str] = []
    placeholder_date = datetime(1970, 1, 1, tzinfo=UTC)
    metadata = validate_asset_metadata(
        asset_type,
        issuer=issuer,
        indexer=indexer,
        rate=_parse_provided(rate, lambda v: parse_rate(v, "--rate"), parse_errors, Decimal("1")),
        is_prefixed=prefixed,
        daily_liquidity=daily_liquidity,
        purchase_date=_parse_provided(
            purchase_date, lambda v: parse_date(v, "--purchase-date"), parse_errors, placeholder_date
        ),
        maturity_date=_parse_provided(
            maturity_date, lambda v: parse_date(v, "--maturity-date"), parse_errors, placeholder_date
        ),
        extra_errors=parse_errors,
    )
    conn = get_connection()
    try:
        asset = AssetRepository(conn).add(
            ticker,
            weight_dec,
            asset_type=asset_type,
            issuer=metadata.issuer,
            indexer=metadata.indexer,
            rate=metadata.rate,
            is_prefixed=metadata.is_prefixed,
            daily_liquidity=metadata.daily_liquidity,
            purchase_date=metadata.purchase_date,
            maturity_date=metadata.maturity_date,
        )
    finally:
        conn.close()
    typer.echo(f"asset {asset.ticker} ({asset.asset_type}) adicionado com peso {asset.target_weight:.2%}.")


def update(
    ticker: str = typer.Argument(..., help="Ticker do ativo a atualizar."),
    weight: str | None = typer.Option(
        None,
        "--weight",
        "-w",
        help="Novo peso-alvo em decimal entre 0 e 1.",
    ),
    asset_type: AssetType | None = typer.Option(  # noqa: B008 — padrao do typer, OptionInfo e sentinela imutavel
        None,
        "--type",
        "-t",
        case_sensitive=False,
        help="Novo tipo do ativo (apenas entre renda variavel: STOCK/BDR/FII/ETF).",
    ),
) -> None:
    # `update` so mexe em target_weight e asset_type. A troca de tipo e
    # limitada a renda variavel (validate_type_change): mudar de/para renda
    # fixa exige adicionar ou limpar metadados, o que este comando nao faz.
    if weight is None and asset_type is None:
        raise ValidationError("Nada para atualizar. Informe --weight e/ou --type.")
    weight_dec = parse_weight(weight, "--weight") if weight is not None else None
    conn = get_connection()
    try:
        repo = AssetRepository(conn)
        asset = repo.get(ticker)
        if asset is None:
            raise AssetNotFoundError(ticker.upper())
        if asset_type is not None and asset_type != asset.asset_type:
            validate_type_change(asset.ticker, asset.asset_type, asset_type)
            asset = repo.update_type(ticker, asset_type)
        if weight_dec is not None:
            asset = repo.update_weight(ticker, weight_dec)
    finally:
        conn.close()
    typer.echo(f"asset {asset.ticker} atualizado: tipo {asset.asset_type}, peso {asset.target_weight:.2%}.")


def remove(
    ticker: str = typer.Argument(..., help="Ticker do ativo a remover."),
) -> None:
    conn = get_connection()
    try:
        AssetRepository(conn).remove(ticker)
    finally:
        conn.close()
    typer.echo(f"asset {ticker.upper()} removido.")


def list_assets() -> None:
    conn = get_connection()
    try:
        assets = AssetRepository(conn).list()
    finally:
        conn.close()

    if not assets:
        typer.echo("Nenhum ativo cadastrado. Use 'bogle add' para comecar.")
        return

    table = Table(title="Carteira", title_style="bold")
    table.add_column("Ticker", style="cyan", no_wrap=True)
    table.add_column("Target Weight", justify="right")
    for asset in assets:
        table.add_row(asset.ticker, f"{asset.target_weight:.2%}")

    total = sum((a.target_weight for a in assets), start=Decimal("0"))
    table.caption = f"Soma dos pesos: {total:.2%}"

    Console().print(table)
