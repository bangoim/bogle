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
        super().__init__(f"Soma de target_weight ultrapassaria 1.0 (resultaria em {total:.4f}). Operacao revertida.")


class AssetHasTransactionsError(BogleError):
    def __init__(self, ticker: str) -> None:
        self.ticker = ticker
        super().__init__(f"Ativo '{ticker}' possui transacoes vinculadas e nao pode ser removido.")


class TransactionNotFoundError(BogleError):
    def __init__(self, transaction_id: int) -> None:
        self.transaction_id = transaction_id
        super().__init__(f"Transacao {transaction_id} nao encontrada.")


class UnknownSettingError(BogleError):
    def __init__(self, key: str, known_keys: list[str]) -> None:
        self.key = key
        super().__init__(f"Configuracao '{key}' nao reconhecida. Chaves suportadas: {', '.join(known_keys)}.")


class MarketDataError(BogleError):
    """Base for failures fetching market data from an external provider.

    Carries the provider name and, when available, the provider's own error
    code/message so the CLI can show something actionable without a stack trace.
    """

    def __init__(self, message: str, *, provider: str = "", code: str = "") -> None:
        self.provider = provider
        self.code = code
        super().__init__(message)


class QuoteNotFoundError(MarketDataError):
    def __init__(self, symbol: str, *, provider: str = "") -> None:
        self.symbol = symbol
        super().__init__(f"Cotacao nao encontrada para '{symbol}'.", provider=provider)


class RateLimitError(MarketDataError):
    """Provider returned HTTP 429 after the retries were exhausted."""

    def __init__(self, provider: str) -> None:
        super().__init__(
            f"Limite de requisicoes excedido em {provider}. Tente novamente mais tarde.",
            provider=provider,
        )


class NetworkError(MarketDataError):
    """Network-level failure (timeout, DNS, connection) talking to a provider."""

    def __init__(self, provider: str, detail: str = "") -> None:
        message = f"Falha de rede ao acessar {provider}."
        if detail:
            message += f" ({detail})"
        super().__init__(message, provider=provider)
