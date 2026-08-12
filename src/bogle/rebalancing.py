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

Every position must be priced — a missing quote would silently distort all the
weights, so both raise :class:`MissingPriceError` instead.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date
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


@dataclass(frozen=True, slots=True)
class TickerSuggestion:
    ticker: str
    asset_type: AssetType
    price: Decimal
    allocation: Decimal
    """Valor ideal calculado para o ticker (antes do arredondamento em cotas)."""
    quantity: Decimal | None
    """Cotas inteiras a comprar (renda variavel); ``None`` para renda fixa."""
    effective_cost: Decimal
    target_weight: Decimal
    weight_after: Decimal
    """Peso sobre o patrimonio futuro (carteira + aporte, sobra contando como caixa)."""


@dataclass(frozen=True, slots=True)
class AporteSuggestion:
    amount: Decimal
    items: list[TickerSuggestion]
    total_allocated: Decimal
    leftover: Decimal
    warnings: list[str]


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


def suggest_allocation(summary: PortfolioSummary, amount: Decimal) -> AporteSuggestion:
    """Split ``amount`` across the portfolio to shrink drift, without selling.

    Needs are measured against the future patrimony (``total_value + amount``):
    ``needed = max(0, future_total * target_weight - current_value)``. When the
    contribution covers every need, each ticker gets exactly its need and the
    rest stays in cash; otherwise the split is proportional to need. Whatever
    the whole-share floor leaves behind is re-offered to the neediest tickers.
    No receiver ever exceeds its target weight of the future patrimony.
    """
    if amount <= 0:
        raise ValidationError(f"--amount deve ser positivo, recebido {amount}.")
    positions = summary.positions
    if not positions:
        raise ValidationError("Nenhuma posicao ativa para sugerir aporte.")
    missing = [p.ticker for p in positions if p.market_value is None or p.price is None]
    if missing:
        raise MissingPriceError(missing)

    future_total = summary.total_value + amount
    lines: list[_Line] = []
    for p in positions:
        assert p.market_value is not None and p.price is not None  # guarded above
        needed = max(Decimal("0"), future_total * p.target_weight - p.market_value)
        lines.append(_Line(position=p, value=p.market_value, price=p.price, needed=needed))

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
        )
        for line in sorted(lines, key=lambda ln: (-ln.cost, ln.position.ticker))
    ]

    private_fi = sorted(
        line.position.ticker
        for line in lines
        if line.cost > 0 and line.position.asset_type in PRIVATE_FIXED_INCOME_TYPES
    )
    warnings = []
    if private_fi:
        warnings.append(
            f"Aporte em renda fixa privada ({', '.join(private_fi)}) cria um novo contrato "
            "com taxa e data proprias; registre a compra como um novo ativo."
        )

    return AporteSuggestion(
        amount=amount,
        items=items,
        total_allocated=total_allocated,
        leftover=amount - total_allocated,
        warnings=warnings,
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
