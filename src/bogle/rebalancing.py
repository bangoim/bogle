"""Buy-vs-Hold classification, Boglehead style (issue #22).

No-sell policy: positions that outgrew their target are HELD — never sold — and
fresh contributions go to whatever lagged behind. Classification is a pure
function over the positions computed by :mod:`bogle.position`, reusing its drift
convention (``drift = current_weight - target_weight``, negative = below
target): a ticker is BUY when ``drift < -threshold``, HOLD otherwise.

Every position must be priced — a missing quote would silently distort all the
weights, so it raises :class:`MissingPriceError` instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from bogle.domain.errors import MissingPriceError
from bogle.position import Position

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
