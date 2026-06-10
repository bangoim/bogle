from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import psycopg
from psycopg import errors as pg_errors
from psycopg.rows import DictRow

from bogle.domain.errors import (
    AssetNotFoundError,
    TransactionNotFoundError,
    ValidationError,
)
from bogle.domain.transactions import Transaction, TransactionType

_ZERO = Decimal("0")

_SELECT_COLUMNS = "id, ticker, transaction_type, transaction_date, shares, unit_price, total_investment, fees, total_cost, tax_withheld"


def _row_to_transaction(row: dict) -> Transaction:
    return Transaction(
        id=row["id"],
        ticker=row["ticker"],
        transaction_type=TransactionType(row["transaction_type"]),
        date=row["transaction_date"],
        shares=row["shares"],
        unit_price=row["unit_price"],
        total_investment=row["total_investment"],
        fees=row["fees"],
        total_cost=row["total_cost"],
        tax_withheld=row["tax_withheld"],
    )


class TransactionRepository:
    """Data access for the ``transactions`` table.

    One ``add_*`` method per ``TransactionType``; every method validates
    its inputs with friendly errors before touching the database and
    returns the row as persisted (read back via ``RETURNING``).
    """

    def __init__(self, conn: psycopg.Connection[DictRow]) -> None:
        self._conn = conn

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def list(self, ticker: str | None = None) -> list[Transaction]:
        """Return transactions in chronological order, optionally by ticker."""
        with self._conn.cursor() as cur:
            if ticker is not None:
                cur.execute(
                    f"""
                    SELECT {_SELECT_COLUMNS} FROM transactions
                    WHERE ticker = %s
                    ORDER BY transaction_date, id
                    """,
                    (ticker.upper(),),
                )
            else:
                cur.execute(f"SELECT {_SELECT_COLUMNS} FROM transactions ORDER BY transaction_date, id")
            rows = cur.fetchall()
        return [_row_to_transaction(r) for r in rows]

    # ------------------------------------------------------------------
    # Trades
    # ------------------------------------------------------------------

    def add_buy(
        self,
        ticker: str,
        date: datetime,
        shares: Decimal,
        unit_price: Decimal,
        fees: Decimal = _ZERO,
    ) -> Transaction:
        """Record a purchase. ``total_cost`` = shares * unit_price + fees."""
        self._validate_trade(shares, unit_price, fees, tax_withheld=_ZERO)
        total_investment = shares * unit_price
        return self._insert(
            ticker,
            TransactionType.BUY,
            date,
            shares=shares,
            unit_price=unit_price,
            total_investment=total_investment,
            fees=fees,
            total_cost=total_investment + fees,
            tax_withheld=_ZERO,
        )

    def add_sale(
        self,
        ticker: str,
        date: datetime,
        shares: Decimal,
        unit_price: Decimal,
        fees: Decimal = _ZERO,
        tax_withheld: Decimal = _ZERO,
    ) -> Transaction:
        """Record a sale (partial or total).

        ``shares`` is the quantity sold and ``total_investment`` the gross
        proceeds; the operation's own cost is just ``fees``. Realized PnL
        is computed by the holdings view (issue 3.2), not stored here.
        ``tax_withheld`` covers the 0.005% "dedo-duro" retained on sales.
        """
        self._validate_trade(shares, unit_price, fees, tax_withheld)
        return self._insert(
            ticker,
            TransactionType.SELL,
            date,
            shares=shares,
            unit_price=unit_price,
            total_investment=shares * unit_price,
            fees=fees,
            total_cost=fees,
            tax_withheld=tax_withheld,
        )

    # ------------------------------------------------------------------
    # Income events (gross amount in total_investment)
    # ------------------------------------------------------------------

    def add_dividend(
        self,
        ticker: str,
        date: datetime,
        amount: Decimal,
        tax_withheld: Decimal = _ZERO,
    ) -> Transaction:
        """Record a stock dividend (currently tax-exempt for individuals)."""
        return self._insert_income(ticker, TransactionType.DIVIDEND, date, amount, tax_withheld)

    def add_jcp(
        self,
        ticker: str,
        date: datetime,
        amount: Decimal,
        tax_withheld: Decimal,
    ) -> Transaction:
        """Record JCP (juros sobre capital proprio); 15% retained at source."""
        return self._insert_income(ticker, TransactionType.JCP, date, amount, tax_withheld)

    def add_rendimento(
        self,
        ticker: str,
        date: datetime,
        amount: Decimal,
    ) -> Transaction:
        """Record an FII monthly distribution (tax-exempt for individuals)."""
        return self._insert_income(ticker, TransactionType.RENDIMENTO, date, amount, _ZERO)

    def add_interest(
        self,
        ticker: str,
        date: datetime,
        amount: Decimal,
        tax_withheld: Decimal = _ZERO,
    ) -> Transaction:
        """Record fixed-income interest (coupons or redemption yield)."""
        return self._insert_income(ticker, TransactionType.INTEREST, date, amount, tax_withheld)

    # ------------------------------------------------------------------
    # Deletes
    # ------------------------------------------------------------------

    def delete(self, transaction_id: int) -> None:
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute("DELETE FROM transactions WHERE id = %s", (transaction_id,))
            if cur.rowcount == 0:
                raise TransactionNotFoundError(transaction_id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_trade(shares: Decimal, unit_price: Decimal, fees: Decimal, tax_withheld: Decimal) -> None:
        # is_finite() primeiro: comparar NaN com 0 levanta InvalidOperation.
        errors: list[str] = []
        if not shares.is_finite() or shares <= 0:
            errors.append(f"shares deve ser maior que zero, recebido {shares}.")
        if not unit_price.is_finite() or unit_price <= 0:
            errors.append(f"unit_price deve ser maior que zero, recebido {unit_price}.")
        if not fees.is_finite() or fees < 0:
            errors.append(f"fees nao pode ser negativo, recebido {fees}.")
        if not tax_withheld.is_finite() or tax_withheld < 0:
            errors.append(f"tax_withheld nao pode ser negativo, recebido {tax_withheld}.")
        if errors:
            raise ValidationError("\n".join(errors))

    def _insert_income(
        self,
        ticker: str,
        transaction_type: TransactionType,
        date: datetime,
        amount: Decimal,
        tax_withheld: Decimal,
    ) -> Transaction:
        errors: list[str] = []
        if not amount.is_finite() or amount <= 0:
            errors.append(f"amount deve ser maior que zero, recebido {amount}.")
        if not tax_withheld.is_finite() or tax_withheld < 0:
            errors.append(f"tax_withheld nao pode ser negativo, recebido {tax_withheld}.")
        if errors:
            raise ValidationError("\n".join(errors))
        return self._insert(
            ticker,
            transaction_type,
            date,
            shares=_ZERO,
            unit_price=_ZERO,
            total_investment=amount,
            fees=_ZERO,
            total_cost=_ZERO,
            tax_withheld=tax_withheld,
        )

    def _insert(
        self,
        ticker: str,
        transaction_type: TransactionType,
        date: datetime,
        *,
        shares: Decimal,
        unit_price: Decimal,
        total_investment: Decimal,
        fees: Decimal,
        total_cost: Decimal,
        tax_withheld: Decimal,
    ) -> Transaction:
        ticker = ticker.upper()
        try:
            with self._conn.transaction(), self._conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO transactions (
                        ticker, transaction_type, transaction_date, shares,
                        unit_price, total_investment, fees, total_cost,
                        tax_withheld
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING {_SELECT_COLUMNS}
                    """,
                    (
                        ticker,
                        transaction_type.value,
                        date,
                        shares,
                        unit_price,
                        total_investment,
                        fees,
                        total_cost,
                        tax_withheld,
                    ),
                )
                row = cur.fetchone()
        except pg_errors.ForeignKeyViolation:
            raise AssetNotFoundError(ticker) from None
        except pg_errors.CheckViolation as exc:
            raise ValidationError(
                f"Valores invalidos para transacao {transaction_type.value} (constraint {exc.diag.constraint_name})."
            ) from None
        except pg_errors.NumericValueOutOfRange:
            # Cobre tambem o overflow do produto shares * unit_price, que
            # nenhuma validacao por campo consegue prever.
            raise ValidationError(
                f"Valores excedem a precisao suportada pelo banco para transacao {transaction_type.value}."
            ) from None
        assert row is not None  # INSERT ... RETURNING sempre devolve uma linha
        return _row_to_transaction(row)
