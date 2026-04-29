from __future__ import annotations

from decimal import Decimal


class BogleError(Exception):
    """Base class for every domain-level error raised by bogle.

    The CLI layer catches this and converts it into a friendly message.
    Anything else propagates as a real bug.
    """


class ValidationError(BogleError):
    """Input validation failure (bad CLI argument, missing field, etc.)."""


class AssetNotFoundError(BogleError):
    def __init__(self, ticker: str) -> None:
        self.ticker = ticker
        super().__init__(f"Ativo '{ticker}' nao encontrado.")


class AssetAlreadyExistsError(BogleError):
    def __init__(self, ticker: str) -> None:
        self.ticker = ticker
        super().__init__(f"Ativo '{ticker}' ja existe.")


class WeightSumExceededError(BogleError):
    def __init__(self, total: Decimal) -> None:
        self.total = total
        super().__init__(
            "Soma de target_weight ultrapassaria 1.0 "
            f"(resultaria em {total:.4f}). Operacao revertida."
        )


class AssetHasTransactionsError(BogleError):
    def __init__(self, ticker: str) -> None:
        self.ticker = ticker
        super().__init__(
            f"Ativo '{ticker}' possui transacoes vinculadas e nao pode ser removido."
        )
