from __future__ import annotations

from decimal import Decimal

from bogle import format as fmt


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


class InsufficientSharesError(BogleError):
    """A sale asking for more shares than the position has.

    Raised above the repository, never by it: the ledger writes what it is told
    and the ``holdings`` view answers an oversold ticker by hiding the position
    (issue #9). See :mod:`bogle.sales` for why the refusal lives one layer up.

    The quantities go through :mod:`bogle.format`, so they are masked with every
    other amount while the privacy mode is on — a message is not a way around it.
    """

    def __init__(self, ticker: str, held: Decimal, requested: Decimal) -> None:
        self.ticker = ticker
        self.held = held
        self.requested = requested
        super().__init__(
            f"Nao ha posicao aberta em '{ticker}' para vender."
            if held <= 0
            else f"Posicao de '{ticker}' tem {fmt.exact(held)} cotas, e a venda pede {fmt.exact(requested)}."
        )


class TransactionNotFoundError(BogleError):
    def __init__(self, transaction_id: int) -> None:
        self.transaction_id = transaction_id
        super().__init__(f"Transacao {transaction_id} nao encontrada.")


class MissingPriceError(BogleError):
    """A rebalancing computation needs every position priced; degrading
    silently (like the position view does) would distort all the weights."""

    def __init__(self, tickers: list[str]) -> None:
        self.tickers = tickers
        super().__init__(
            f"Sem preco atual para: {', '.join(tickers)}. "
            "Rebalanceamento exige todas as posicoes precificadas; tente novamente mais tarde."
        )


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
