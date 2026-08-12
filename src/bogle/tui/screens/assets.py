"""Assets screens: the portfolio's definition (issue #76).

The list covers ``bogle list`` — and shows more than it does, because the fixed
income metadata (issuer, indexer, rate, dates) has nowhere else to be seen — plus
``bogle add``, ``update`` and ``remove`` as forms.

The registration form is where the interface earns the most over the command: the
fields a type accepts are the only ones on screen. TESOURO shows no issuer, a
prefixed instrument shows no indexer, and a maturity date stops being required
the moment daily liquidity is checked — instead of the command's aggregated
"parametros invalidos para o tipo CDB" after the fact.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import ClassVar, override

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.validation import Validator
from textual.widgets import Button, Checkbox, DataTable, Footer, Header, Select, Static

from bogle import format as fmt
from bogle.cli.parsing import parse_date, parse_rate, parse_weight
from bogle.domain.assets import (
    FIXED_INCOME_TYPES,
    PRIVATE_FIXED_INCOME_TYPES,
    VARIABLE_INCOME_TYPES,
    Asset,
    AssetType,
    Indexer,
)
from bogle.tui import cells, services
from bogle.tui.errors import HANDLED, message_for
from bogle.tui.screens.data import DataScreen
from bogle.tui.screens.modals import ConfirmModal
from bogle.tui.screens.write import Entry, WriteScreen
from bogle.tui.validators import DateField, DecimalField, TextField
from bogle.tui.widgets.form import ControlRow, Field

_COLUMNS = ("Ticker", "Tipo", "Target", "Emissor", "Indexador", "Taxa", "Liquidez", "Compra", "Vencimento")

# PREFIXADO fica fora: um titulo prefixado se declara na caixa "Prefixado", e o
# dominio recusa o indexador com esse nome (validate_asset_metadata).
_INDEXERS = tuple(indexer for indexer in Indexer if indexer is not Indexer.PREFIXADO)

_EMPTY = "Nenhum ativo cadastrado. Use 'a' para adicionar o primeiro."
_WEIGHT_HINT = "fracao decimal: 0.4 = 40%"
_RATE_HINT = "1.10 = 110% do CDI; 0.065 = IPCA + 6,5%"


class AssetsScreen(DataScreen[list[Asset]]):
    SUB_TITLE = "ativos"
    AUTO_FOCUS = "#assets"
    LOADING = "#assets"
    NOTE = "#assets-note"
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("a", "add", "Adicionar"),
        Binding("u", "update", "Atualizar"),
        Binding("d", "remove", "Remover"),
    ]

    @override
    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="assets-screen"):
            table = DataTable(id="assets", cursor_type="row", zebra_stripes=True)
            table.add_columns(*_COLUMNS)
            yield table
            yield Static(id="assets-note")
        yield Footer()

    # --- selecao --------------------------------------------------------

    @property
    def selected(self) -> Asset | None:
        assets = self.report
        table = self.query_one(DataTable)
        if not assets or table.cursor_row < 0 or table.cursor_row >= len(assets):
            return None
        return assets[table.cursor_row]

    # --- acoes ----------------------------------------------------------

    def action_add(self) -> None:
        self.app.push_screen(AssetFormScreen(), lambda _: self.fetch())

    def action_update(self) -> None:
        asset = self.selected
        if asset is None:
            self.notify("Nenhum ativo selecionado.", severity="warning")
            return
        self.app.push_screen(AssetUpdateScreen(asset), lambda _: self.fetch())

    def action_remove(self) -> None:
        asset = self.selected
        if asset is None:
            self.notify("Nenhum ativo selecionado.", severity="warning")
            return
        self.app.push_screen(
            ConfirmModal(
                f"Remover o ativo {asset.ticker}?",
                f"{asset.asset_type}, peso {fmt.pct(asset.target_weight)}."
                "\nSo funciona enquanto o ativo nao tem transacoes.",
                confirm_label="Remover",
            ),
            lambda confirmed: self._on_confirmed(asset.ticker, confirmed),
        )

    # --- carga ----------------------------------------------------------

    @override
    def load(self) -> list[Asset]:
        return services.list_assets()

    @override
    def clear_content(self) -> None:
        self.query_one(DataTable).clear()

    @override
    def render_report(self, report: list[Asset]) -> None:
        table = self.query_one(DataTable)
        table.clear()  # mantem as colunas
        for asset in report:
            table.add_row(
                cells.ticker(asset.ticker),
                cells.text(asset.asset_type.value),
                cells.pct(asset.target_weight),
                cells.text(asset.issuer or fmt.DASH),
                cells.text(_indexer_of(asset)),
                cells.exact(asset.rate) if asset.rate is not None else cells.right(fmt.DASH),
                cells.text(_liquidity_of(asset)),
                cells.text(_date_of(asset.purchase_date)),
                cells.text(_date_of(asset.maturity_date)),
                key=asset.ticker,
            )
        self.show_note(_note_for(report))

    # --- remocao --------------------------------------------------------

    def _on_confirmed(self, ticker: str, confirmed: bool | None) -> None:
        if confirmed:
            self._delete(ticker)

    @work(thread=True, exclusive=True, group="assets-delete")
    def _delete(self, ticker: str) -> None:
        try:
            services.remove_asset(ticker)
        except HANDLED as exc:
            self.app.call_from_thread(self._delete_failed, message_for(exc))
            return
        self.app.call_from_thread(self._deleted, ticker)

    def _delete_failed(self, message: str) -> None:
        # Diferente de uma falha de carga: as linhas continuam validas (o ativo
        # tem transacoes, por exemplo). A tabela fica, e o toast explica.
        self.notify(message, title="erro", severity="error", timeout=10, markup=False)
        self.fetch()

    def _deleted(self, ticker: str) -> None:
        self.notify(f"ativo {ticker} removido.", markup=False)
        self.fetch()


class AssetFormScreen(WriteScreen[Asset]):
    """Register an asset: the fields follow the type, as the field table does."""

    SUB_TITLE = "cadastrar ativo"
    AUTO_FOCUS = "#ticker Input"
    CONFIRM_TITLE = "Confirmar cadastro"
    CONFIRM_LABEL = "Cadastrar"
    WRITING_MESSAGE = "cadastrando o ativo; um instante."

    @override
    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="asset-form"):
            yield Field("Ticker", id="ticker", placeholder="ex: PETR4", validators=[TextField("Ticker")])
            yield ControlRow(
                "Tipo",
                Select(
                    [(kind.value, kind) for kind in AssetType],
                    value=AssetType.STOCK,
                    allow_blank=False,
                    compact=True,
                    id="asset-type",
                ),
                id="type-row",
            )
            yield Field(
                "Peso-alvo",
                id="weight",
                placeholder=_WEIGHT_HINT,
                validators=[DecimalField("Peso-alvo", positive=True, parse=parse_weight)],
            )
            yield Field("Emissor", id="issuer", placeholder="banco/emissor", validators=[TextField("Emissor")])
            yield ControlRow("Prefixado", Checkbox(id="prefixed", compact=True), id="prefixed-row")
            yield ControlRow(
                "Indexador",
                Select(
                    [(indexer.value, indexer) for indexer in _INDEXERS],
                    value=Indexer.CDI,
                    allow_blank=False,
                    compact=True,
                    id="indexer",
                ),
                id="indexer-row",
            )
            yield Field(
                "Taxa",
                id="rate",
                placeholder=_RATE_HINT,
                validators=[DecimalField("Taxa", positive=True, parse=parse_rate)],
            )
            yield ControlRow("Liquidez diaria", Checkbox(id="daily-liquidity", compact=True), id="liquidity-row")
            yield Field(
                "Data de compra", id="purchase-date", placeholder="YYYY-MM-DD", validators=[DateField("Data de compra")]
            )
            yield Field(
                "Vencimento", id="maturity-date", placeholder="YYYY-MM-DD", validators=[DateField("Vencimento")]
            )
            with Horizontal(id="form-buttons"):
                yield Button("Cadastrar", id="submit", variant="primary")
                yield Button("Voltar", id="back")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#asset-form").border_title = "Novo ativo"
        self.apply_type()

    # --- campos condicionais --------------------------------------------

    @property
    def asset_type(self) -> AssetType:
        return self.query_one("#asset-type", Select).value  # type: ignore[return-value]

    @property
    def indexer(self) -> Indexer:
        return self.query_one("#indexer", Select).value  # type: ignore[return-value]

    @property
    def is_prefixed(self) -> bool:
        return self.query_one("#prefixed", Checkbox).value

    @property
    def daily_liquidity(self) -> bool:
        return self.query_one("#daily-liquidity", Checkbox).value

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "asset-type":
            self.apply_type()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id in ("prefixed", "daily-liquidity"):
            self.apply_type()

    def apply_type(self) -> None:
        """Show only what this type accepts, and require only what it demands."""
        kind = self.asset_type
        fixed = kind in FIXED_INCOME_TYPES
        private = kind in PRIVATE_FIXED_INCOME_TYPES
        # Vencimento e opcional so em renda fixa privada com liquidez diaria.
        maturity_required = fixed and not (private and self.daily_liquidity)

        self.query_one("#prefixed-row").display = fixed
        self.query_one("#indexer-row").display = fixed and not self.is_prefixed
        self.query_one("#liquidity-row").display = private

        self._applies("issuer", private, [TextField("Emissor")])
        self._applies("rate", fixed, [DecimalField("Taxa", positive=True, parse=parse_rate)], placeholder=_RATE_HINT)
        self._applies("purchase-date", fixed, [DateField("Data de compra")], placeholder="YYYY-MM-DD")
        self._applies(
            "maturity-date",
            fixed,
            [DateField("Vencimento", allow_blank=not maturity_required)],
            placeholder="YYYY-MM-DD" if maturity_required else "opcional (liquidez diaria)",
        )

    def _applies(self, field_id: str, applicable: bool, validators: list[Validator], *, placeholder: str = "") -> None:
        field = self.field(field_id)
        # As regras acompanham o tipo (o vencimento e obrigatorio ou nao). Nao ha
        # o que relaxar ao esconder: um campo fora do tipo fica desabilitado, e
        # `Field.check` nem chega nele.
        if applicable:
            field.input.validators = list(validators)
        field.set_applicable(applicable, placeholder=placeholder)

    # --- gravacao -------------------------------------------------------

    @override
    def collect(self) -> Entry | None:
        if not self.check_fields():
            return None
        kind = self.asset_type
        entry: Entry = {
            "ticker": self.field("ticker").value.upper(),
            "target_weight": parse_weight(self.field("weight").value, "Peso-alvo"),
            "asset_type": kind,
        }
        if kind in FIXED_INCOME_TYPES:
            maturity = self.field("maturity-date").value
            entry |= {
                "rate": parse_rate(self.field("rate").value, "Taxa"),
                "is_prefixed": self.is_prefixed,
                "indexer": None if self.is_prefixed else self.indexer,
                "purchase_date": parse_date(self.field("purchase-date").value, "Data de compra"),
                "maturity_date": parse_date(maturity, "Vencimento") if maturity else None,
            }
        if kind in PRIVATE_FIXED_INCOME_TYPES:
            entry |= {"issuer": self.field("issuer").value, "daily_liquidity": self.daily_liquidity}
        return entry

    @override
    def describe(self, entry: Entry) -> str:
        lines = [
            f"{entry['ticker']} ({entry['asset_type']}), peso {fmt.pct(entry['target_weight'])}",
        ]
        if "rate" in entry:
            indexer = "prefixado" if entry["is_prefixed"] else str(entry["indexer"])
            lines.append(f"{indexer}, taxa {fmt.exact(entry['rate'])}")
            lines.append(f"Compra {entry['purchase_date']:%Y-%m-%d}")
            maturity = entry["maturity_date"]
            lines[-1] += f", vencimento {maturity:%Y-%m-%d}" if maturity is not None else ", sem vencimento"
        if "issuer" in entry:
            liquidity = "com" if entry["daily_liquidity"] else "sem"
            lines.append(f"Emissor {entry['issuer']}, {liquidity} liquidez diaria")
        return "\n".join(lines)

    @override
    def write(self, entry: Entry) -> Asset:
        return services.add_asset(**entry)

    @override
    def written(self, asset: Asset) -> None:
        self.notify(
            f"ativo {asset.ticker} ({asset.asset_type}) cadastrado com peso {fmt.pct(asset.target_weight)}.",
            title="pronto",
            markup=False,
        )
        self.dismiss()


class AssetUpdateScreen(WriteScreen[Asset]):
    """Change the target weight and/or the type, as ``bogle update`` does."""

    AUTO_FOCUS = "#weight Input"
    CONFIRM_TITLE = "Confirmar alteracao"
    CONFIRM_LABEL = "Atualizar"
    WRITING_MESSAGE = "atualizando o ativo; um instante."

    def __init__(self, asset: Asset) -> None:
        super().__init__()
        self.asset = asset
        self.sub_title = f"atualizar {asset.ticker}"
        self.switchable = asset.asset_type in VARIABLE_INCOME_TYPES
        """Only variable income can change type: the rest would need metadata."""

    @override
    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="asset-form"):
            yield Field(
                "Peso-alvo",
                id="weight",
                value=_weight_text(self.asset.target_weight),
                placeholder=_WEIGHT_HINT,
                validators=[DecimalField("Peso-alvo", positive=True, parse=parse_weight)],
            )
            yield ControlRow(
                "Tipo",
                Select(
                    [(kind.value, kind) for kind in _switchable_types(self.asset)],
                    value=self.asset.asset_type,
                    allow_blank=False,
                    compact=True,
                    disabled=not self.switchable,
                    id="asset-type",
                ),
                id="type-row",
            )
            yield Static(id="update-note")
            with Horizontal(id="form-buttons"):
                yield Button("Atualizar", id="submit", variant="primary")
                yield Button("Voltar", id="back")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#asset-form").border_title = f"Ativo {self.asset.ticker}"
        if not self.switchable:
            self.query_one("#update-note", Static).update(
                Text.from_markup(
                    f"[dim]{self.asset.asset_type} e renda fixa: trocar o tipo deixaria metadados orfaos, "
                    "entao so o peso muda aqui.[/dim]"
                )
            )

    @property
    def asset_type(self) -> AssetType:
        return self.query_one("#asset-type", Select).value  # type: ignore[return-value]

    @override
    def collect(self) -> Entry | None:
        if not self.check_fields():
            return None
        weight = parse_weight(self.field("weight").value, "Peso-alvo")
        kind = self.asset_type
        changed_weight = weight != self.asset.target_weight
        changed_type = kind is not self.asset.asset_type
        if not changed_weight and not changed_type:
            # Mesma recusa do `bogle update` sem nenhuma flag, so mais cedo.
            self.notify("Nada para atualizar: peso e tipo estao como estavam.", severity="warning")
            return None
        entry: Entry = {"ticker": self.asset.ticker}
        if changed_weight:
            entry["target_weight"] = weight
        if changed_type:
            entry["asset_type"] = kind
        return entry

    @override
    def describe(self, entry: Entry) -> str:
        lines = [f"{self.asset.ticker}:"]
        if "target_weight" in entry:
            lines.append(f"peso {fmt.pct(self.asset.target_weight)} -> {fmt.pct(entry['target_weight'])}")
        if "asset_type" in entry:
            lines.append(f"tipo {self.asset.asset_type} -> {entry['asset_type']}")
        return "\n".join(lines)

    @override
    def write(self, entry: Entry) -> Asset:
        return services.update_asset(**entry)

    @override
    def written(self, asset: Asset) -> None:
        self.notify(
            f"ativo {asset.ticker} atualizado: tipo {asset.asset_type}, peso {fmt.pct(asset.target_weight)}.",
            title="pronto",
            markup=False,
        )
        self.dismiss()


def _switchable_types(asset: Asset) -> tuple[AssetType, ...]:
    """Options of the type selector: variable income, or just what it already is."""
    if asset.asset_type in VARIABLE_INCOME_TYPES:
        return tuple(sorted(VARIABLE_INCOME_TYPES, key=lambda kind: kind.value))
    return (asset.asset_type,)


def _weight_text(weight: Decimal) -> str:
    """The weight as the field takes it back: a plain fraction, no percent sign."""
    return format(weight.normalize(), "f")


def _indexer_of(asset: Asset) -> str:
    if asset.is_prefixed:
        return "PREFIXADO"
    return asset.indexer.value if asset.indexer is not None else fmt.DASH


def _liquidity_of(asset: Asset) -> str:
    if asset.daily_liquidity is None:
        return fmt.DASH
    return "diaria" if asset.daily_liquidity else "no vencimento"


def _date_of(value: datetime | None) -> str:
    return f"{value:%Y-%m-%d}" if value is not None else fmt.DASH


def _note_for(assets: list[Asset]) -> str:
    if not assets:
        return f"[yellow]{_EMPTY}[/yellow]"
    total = sum((asset.target_weight for asset in assets), Decimal("0"))
    return f"[dim]{len(assets)} ativos. Soma dos pesos: {fmt.pct(total)} (o maximo e 100.00%).[/dim]"
