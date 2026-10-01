"""Rebalancing engine, Boglehead style (issues #22 and #23).

No-sell policy: positions that outgrew their target are HELD — never sold — and
fresh contributions go to whatever lagged behind. Both entry points are pure
functions over the positions computed by :mod:`bogle.position`, reusing its
drift convention (``drift = current_weight - target_weight``, negative = below
target):

- :func:`classify_positions` — a ticker is BUY when ``drift < -threshold``,
  HOLD otherwise.
- :func:`suggest_allocation` — splits a fixed contribution so every ticker
  approaches its target weight of the *future* patrimony (portfolio + aporte),
  never pushing a receiver past its target. Variable income buys whole shares
  (round down); fixed income (Tesouro included) takes exact values.

Every *open* position must be priced — a missing quote would silently distort all
the weights, so both raise :class:`MissingPriceError` instead. A target that has
no position behind it yet is worth zero however it is priced, so it only sits the
contribution out (with a warning saying so) instead of taking the whole
suggestion down with it.

**Manual prices.** ``suggest_allocation`` accepts a price per ticker, for the user
about to place a *limit* order: the quote says what the paper costs now, and the
order says what they are willing to pay. It applies to the purchase only — how
many whole shares the allocated money buys, and what they cost — while the
portfolio stays marked to market, so the weights, the drift and therefore *how
much* each ticker receives are the same as without it. Marking the position at a
made-up price would quietly change the recommendation itself: a limit below the
market would make the ticker look more underweight and pull money towards it.
"""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from enum import StrEnum

from bogle.domain.assets import PRIVATE_FIXED_INCOME_TYPES, VARIABLE_INCOME_TYPES, AssetType
from bogle.domain.errors import MissingPriceError, ValidationError
from bogle.position import PortfolioSummary, Position

DEFAULT_THRESHOLD = Decimal("0.05")  # 5 pontos percentuais


class Recommendation(StrEnum):
    BUY = "BUY"
    HOLD = "HOLD"


@dataclass(frozen=True, slots=True)
class TickerRecommendation:
    ticker: str
    current_weight: Decimal
    target_weight: Decimal
    drift: Decimal
    recommendation: Recommendation
    reason: str


def _pct(value: Decimal) -> str:
    """0.6375 -> '63.8%'; 0.7 -> '70%' (one decimal half-up, trailing zero dropped)."""
    scaled = (value * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if scaled == scaled.to_integral_value():
        return f"{scaled:.0f}%"
    return f"{scaled}%"


def _pp(value: Decimal) -> str:
    scaled = (abs(value) * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if scaled == scaled.to_integral_value():
        return f"{scaled:.0f}"
    return f"{scaled}"


def _reason(drift: Decimal, current: Decimal, target: Decimal, threshold: Decimal) -> str:
    if drift < -threshold:
        return f"Peso atual {_pct(current)} esta {_pp(drift)} p.p. abaixo do target de {_pct(target)}."
    if drift < 0:
        return (
            f"Peso atual {_pct(current)} esta {_pp(drift)} p.p. abaixo do target de {_pct(target)}, "
            f"dentro da tolerancia de {_pp(threshold)} p.p."
        )
    if drift == 0:
        return f"Peso atual {_pct(current)} esta no target."
    return f"Peso atual {_pct(current)} esta {_pp(drift)} p.p. acima do target de {_pct(target)}; politica no-sell."


def classify_positions(positions: list[Position], threshold: Decimal = DEFAULT_THRESHOLD) -> list[TickerRecommendation]:
    """Classify every position as BUY or HOLD (input order preserved)."""
    missing = [p.ticker for p in positions if p.current_weight is None or p.drift is None]
    if missing:
        raise MissingPriceError(missing)

    recommendations = []
    for p in positions:
        assert p.current_weight is not None and p.drift is not None  # guarded above
        buy = p.drift < -threshold
        recommendations.append(
            TickerRecommendation(
                ticker=p.ticker,
                current_weight=p.current_weight,
                target_weight=p.target_weight,
                drift=p.drift,
                recommendation=Recommendation.BUY if buy else Recommendation.HOLD,
                reason=_reason(p.drift, p.current_weight, p.target_weight, threshold),
            )
        )
    return recommendations


# ---------------------------------------------------------------------------
# Aporte suggestion (issue #23)
# ---------------------------------------------------------------------------

_CENT = Decimal("0.01")

B3_FEE_RATE = Decimal("0.0003")
"""Tarifa da B3 sobre a compra de renda variavel no pregao regular, pessoa fisica.

0,0050% de negociacao + 0,0224% de CCP + 0,0026% de transferencia de ativos (TTA),
na tabela "Tarifacao de Produtos de Renda Variavel" v3.0 (vigente desde
15/08/2025). Vale igual para acao, BDR, FII e ETF — de acoes, internacional ou de
renda fixa. Nos leiloes de abertura e fechamento a negociacao sobe para 0,0070%, e
a TTA e recalculada todo ano: a estimativa fica alguns centavos abaixo da nota
nesses casos. Renda fixa (CDB, LCI, Tesouro...) nao paga tarifa de negociacao.
"""

FEE_BASIS = (
    f"Taxa B3 estimada em {B3_FEE_RATE * 100:.2f}% da renda variavel, no pregao regular "
    "(em leilao sai um pouco mais); corretagem nao incluida."
)
"""De onde sai a taxa dos totais, para os dois frontends dizerem a mesma coisa."""


@dataclass(frozen=True, slots=True)
class TickerSuggestion:
    ticker: str
    asset_type: AssetType
    price: Decimal
    """Preco usado na compra: a cotacao, ou o que o usuario informou."""
    allocation: Decimal
    """Valor ideal calculado para o ticker (antes do arredondamento em cotas)."""
    quantity: Decimal | None
    """Cotas inteiras a comprar (renda variavel); ``None`` para renda fixa."""
    effective_cost: Decimal
    target_weight: Decimal
    weight_after: Decimal
    """Peso sobre o patrimonio futuro (carteira + aporte, sobra contando como caixa)."""
    current_weight: Decimal | None = None
    """Peso de hoje, antes do aporte. Sem ele a tela mostra onde o ticker vai
    parar sem dizer de onde ele saiu — e e a distancia entre os dois que explica
    por que um recebeu dinheiro e o outro nao. ``None`` numa carteira vazia, onde
    um peso sobre patrimonio zero nao existe."""
    quoted_price: Decimal | None = None
    """A cotacao do provedor, mesmo quando ``price`` e um preco informado — os
    frontends mostram uma ao lado da outra em vez de esconder a de mercado."""
    is_manual_price: bool = False
    """``price`` veio do usuario (ordem limitada), nao do provedor."""
    price_source: str | None = None
    """``"brapi"`` / ``"yfinance"`` / ``"tesouro"`` / ``"calculado"``, como na Posicao."""
    as_of: datetime | None = None
    """Timestamp da cotacao. Sem ele a tela mostra um preco sem dizer de quando
    ele e — e um preco de cinco minutos atras nao e o preco de agora."""

    @property
    def drift_after(self) -> Decimal:
        """O que ainda falta para o target depois do aporte (negativo = abaixo).

        Mesma convencao do drift da Posicao (``peso - target``), medida sobre o
        patrimonio futuro: e o numero que diz se este aporte resolveu o desvio do
        ticker ou apenas o diminuiu.
        """
        return self.weight_after - self.target_weight


@dataclass(frozen=True, slots=True)
class UnquotedTarget:
    """A target the provider could not quote, left out of this contribution.

    Only a target with no position behind it gets here — an open position without
    a price aborts the suggestion — so it is worth zero before and after. What a
    frontend has to show is the gap left open, and the way to close it: a price
    informed for the ticker brings it back into the split.
    """

    ticker: str
    asset_type: AssetType
    target_weight: Decimal
    current_weight: Decimal | None
    """Zero, or ``None`` numa carteira vazia (como em :class:`TickerSuggestion`)."""

    @property
    def weight_after(self) -> Decimal:
        return Decimal("0")

    @property
    def drift_after(self) -> Decimal:
        """O target inteiro, em aberto: e o tamanho do que este aporte deixou de fora."""
        return -self.target_weight


@dataclass(frozen=True, slots=True)
class AporteSuggestion:
    amount: Decimal
    items: list[TickerSuggestion]
    total_allocated: Decimal
    estimated_fees: Decimal
    """Tarifa da B3 estimada sobre o que a renda variavel compra (:data:`B3_FEE_RATE`)."""
    leftover: Decimal
    """O que sobra em caixa depois das compras *e* da taxa. Negativo quando a taxa
    nao cabe no que o arredondamento em cotas deixou: e o quanto falta."""
    warnings: list[str]
    unquoted: list[UnquotedTarget] = field(default_factory=list)
    """Os targets do aviso "Sem cotacao", como dado: sem eles o frontend so tem o
    texto para mostrar, e nenhum lugar onde oferecer o preco que os traz de volta."""

    @property
    def total_with_fees(self) -> Decimal:
        """O que as compras custam de verdade: o alocado mais a taxa da B3."""
        return self.total_allocated + self.estimated_fees


@dataclass(slots=True)
class _Line:
    position: Position
    value: Decimal
    price: Decimal
    needed: Decimal
    cost: Decimal = Decimal("0")
    quantity: Decimal | None = None

    @property
    def remaining_need(self) -> Decimal:
        return self.needed - self.cost

    @property
    def is_variable_income(self) -> bool:
        return self.position.asset_type in VARIABLE_INCOME_TYPES


def _whole_shares(line: _Line, budget: Decimal) -> Decimal:
    """How many whole shares fit in ``budget`` without overshooting the need."""
    affordable = (budget / line.price).to_integral_value(rounding=ROUND_DOWN)
    within_need = (line.remaining_need / line.price).to_integral_value(rounding=ROUND_DOWN)
    return min(affordable, within_need)


def _manual_prices(positions: list[Position], prices: Mapping[str, Decimal] | None) -> dict[str, Decimal]:
    """Check the prices the user informed and index them by ticker.

    Refused instead of ignored: a typo in a ticker would otherwise leave the user
    reading a suggestion at the market price while believing it was at theirs.
    """
    if not prices:
        return {}
    by_ticker = {p.ticker: p for p in positions}
    resolved: dict[str, Decimal] = {}
    for name, price in prices.items():
        ticker = name.upper()
        position = by_ticker.get(ticker)
        if position is None:
            raise ValidationError(f"Preco informado para um ticker fora da carteira: {ticker}.")
        if position.asset_type not in VARIABLE_INCOME_TYPES:
            # Renda fixa entra por valor exato: o preco nao converte nada, e
            # aceitar um so mudaria o numero mostrado na coluna.
            raise ValidationError(
                f"Preco informado so vale para renda variavel, que compra cotas inteiras; "
                f"{ticker} e {position.asset_type.value}."
            )
        if price <= 0:
            raise ValidationError(f"Preco informado para {ticker} deve ser positivo, recebido {price}.")
        resolved[ticker] = price
    return resolved


def suggest_allocation(
    summary: PortfolioSummary, amount: Decimal, *, prices: Mapping[str, Decimal] | None = None
) -> AporteSuggestion:
    """Split ``amount`` across the portfolio to shrink drift, without selling.

    Needs are measured against the future patrimony (``total_value + amount``):
    ``needed = max(0, future_total * target_weight - current_value)``. When the
    contribution covers every need, each ticker gets exactly its need and the
    rest stays in cash; otherwise the split is proportional to need. Whatever
    the whole-share floor leaves behind is re-offered to the neediest tickers.
    No receiver ever exceeds its target weight of the future patrimony.

    A ticker worth nothing is not a special case, it is the neediest one: pass
    the summary from :func:`~bogle.position.get_allocation_summary` and an asset
    that is still only a target weight competes for the money like every other
    (which is how the first purchase of a ticker gets suggested at all).

    ``prices`` overrides the quote of a variable-income ticker with the price the
    user intends to pay (a limit order). See the module docstring: it changes how
    many shares the money buys and what they cost, never the split itself. It also
    doubles as the way in for a target the provider cannot quote: without a price
    there is no way to say how many shares the money buys, so the ticker sits this
    contribution out (and the suggestion says so) unless one is informed.

    The B3 fee on the variable-income purchases (:data:`B3_FEE_RATE`) is estimated
    on top of the split, not taken out of it: ``leftover`` is what remains after
    the purchases *and* the fee, and goes negative when the fee does not fit.
    """
    if amount <= 0:
        raise ValidationError(f"--amount deve ser positivo, recebido {amount}.")
    positions = summary.positions
    if not positions:
        raise ValidationError("Nenhuma posicao ativa para sugerir aporte.")
    # Uma posicao aberta sem cotacao distorce todos os pesos, entao aborta. Um
    # target que ainda nao virou posicao vale zero de qualquer jeito: sem preco
    # ele so perde a vez neste aporte, e a carteira segue somando certo.
    missing = [p.ticker for p in positions if p.quantity > 0 and (p.market_value is None or p.price is None)]
    if missing:
        raise MissingPriceError(missing)
    manual = _manual_prices(positions, prices)

    future_total = summary.total_value + amount
    lines: list[_Line] = []
    unquoted: list[Position] = []
    for p in positions:
        price = manual.get(p.ticker, p.price)
        if price is None:
            unquoted.append(p)
            continue
        value = p.market_value if p.market_value is not None else Decimal("0")
        # A necessidade vem do valor de mercado, nao do preco informado: e o que
        # mantem a recomendacao independente do preco que o usuario quer pagar.
        needed = max(Decimal("0"), future_total * p.target_weight - value)
        lines.append(_Line(position=p, value=value, price=price, needed=needed))

    total_needed = sum((line.needed for line in lines), Decimal("0"))
    scale = min(Decimal("1"), amount / total_needed) if total_needed > 0 else Decimal("0")
    allocations = {line.position.ticker: line.needed * scale for line in lines}

    for line in lines:
        allocation = allocations[line.position.ticker]
        if line.is_variable_income:
            line.quantity = (allocation / line.price).to_integral_value(rounding=ROUND_DOWN)
            line.cost = line.quantity * line.price
        else:
            line.cost = allocation.quantize(_CENT, rounding=ROUND_DOWN)

    # Sobra do floor volta para quem mais precisa (sem nunca passar do target).
    residual = amount - sum((line.cost for line in lines), Decimal("0"))
    for line in sorted(lines, key=lambda ln: ln.remaining_need, reverse=True):
        if residual < _CENT:
            break
        if line.is_variable_income:
            extra = _whole_shares(line, residual)
            if extra > 0:
                assert line.quantity is not None
                line.quantity += extra
                line.cost += extra * line.price
                residual -= extra * line.price
        else:
            extra = min(residual, line.remaining_need).quantize(_CENT, rounding=ROUND_DOWN)
            if extra > 0:
                line.cost += extra
                residual -= extra

    total_allocated = sum((line.cost for line in lines), Decimal("0"))
    # So estimada, por cima da divisao: ela nao tira cota de ninguem, e a sobra e
    # que diz se o dinheiro cobre as compras com a taxa.
    variable_income = sum((line.cost for line in lines if line.is_variable_income), Decimal("0"))
    estimated_fees = (variable_income * B3_FEE_RATE).quantize(_CENT, rounding=ROUND_HALF_UP)
    items = [
        TickerSuggestion(
            ticker=line.position.ticker,
            asset_type=line.position.asset_type,
            price=line.price,
            allocation=allocations[line.position.ticker].quantize(_CENT, rounding=ROUND_DOWN),
            quantity=line.quantity,
            effective_cost=line.cost,
            target_weight=line.position.target_weight,
            weight_after=(line.value + line.cost) / future_total,
            current_weight=line.position.current_weight,
            quoted_price=line.position.price,
            is_manual_price=line.position.ticker in manual,
            price_source=line.position.price_source,
            as_of=line.position.as_of,
        )
        for line in sorted(lines, key=lambda ln: (-ln.cost, ln.position.ticker))
    ]

    private_fi = sorted(
        line.position.ticker
        for line in lines
        if line.cost > 0 and line.position.asset_type in PRIVATE_FIXED_INCOME_TYPES
    )
    warnings = []
    left_out = [
        UnquotedTarget(
            ticker=p.ticker,
            asset_type=p.asset_type,
            target_weight=p.target_weight,
            current_weight=p.current_weight,
        )
        for p in sorted(unquoted, key=lambda pos: pos.ticker)
    ]
    if left_out:
        # Calar isso deixaria o usuario lendo uma sugestao que ignorou um dos seus
        # targets — e um aporte "completo" que nao e. O *como* informar o preco
        # fica com cada frontend: aqui ele seria a flag de um e a tecla do outro.
        warnings.append(
            f"Sem cotacao para {', '.join(t.ticker for t in left_out)}: o target continua valendo, mas o "
            "ticker ficou fora deste aporte. Confira o ticker no cadastro do ativo, ou informe o preco que "
            "pretende pagar."
        )
    if manual:
        # A sugestao passa a valer condicionada: a ordem pode nao executar, e
        # nesse preco e que as cotas e o custo fecham.
        warnings.append(
            f"Preco informado por voce em {', '.join(sorted(manual))}: as cotas e o custo efetivo "
            "assumem que a ordem executa nesse preco, e os pesos seguem na cotacao de mercado."
        )
    if private_fi:
        warnings.append(
            f"Aporte em renda fixa privada ({', '.join(private_fi)}) cria um novo contrato "
            "com taxa e data proprias; registre a compra como um novo ativo."
        )

    return AporteSuggestion(
        amount=amount,
        items=items,
        total_allocated=total_allocated,
        estimated_fees=estimated_fees,
        leftover=amount - total_allocated - estimated_fees,
        warnings=warnings,
        unquoted=left_out,
    )


# ---------------------------------------------------------------------------
# Evaluation cycle (issue #24)
# ---------------------------------------------------------------------------


def next_evaluation_date(last_evaluation: date, period_months: int) -> date:
    """The date the rebalance cycle completes: ``last_evaluation`` plus the
    period, clamping the day to the target month's length (Aug 31 + 6m -> Feb 28/29)."""
    month_index = last_evaluation.month - 1 + period_months
    year = last_evaluation.year + month_index // 12
    month = month_index % 12 + 1
    day = min(last_evaluation.day, monthrange(year, month)[1])
    return date(year, month, day)


def overdue_notice(last_evaluation: date | None, period_months: int, *, today: date) -> str | None:
    """The "cycle completed" reminder, or ``None`` when nothing is due yet.

    Shared by the frontends (issue #73): the CLI prints it to stderr before a
    command, the TUI raises it as a toast on the Home screen. Never evaluated
    means nothing to remind about — ``bogle suggest`` records the first one.
    """
    if last_evaluation is None:
        return None
    next_eval = next_evaluation_date(last_evaluation, period_months)
    if today < next_eval:
        return None
    return (
        f"ciclo de rebalanceamento de {period_months} meses vencido desde {next_eval.isoformat()}. "
        "Rode 'bogle suggest' para avaliar a carteira."
    )
