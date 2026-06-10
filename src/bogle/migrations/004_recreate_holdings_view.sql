-- 004_recreate_holdings_view: BUY - SELL semantics + active-position filter.
--
-- The 001/003 view summed every transaction as if it were a purchase,
-- so a SELL *increased* the displayed position. This version nets BUY
-- against SELL, exposes asset_type and the net invested capital, and
-- hides zeroed (or never-opened) positions via HAVING.
--
-- Also renames transactions.purchase_date to transaction_date: the
-- historical name became wrong once 003 introduced sales and income.

DROP VIEW IF EXISTS holdings;

ALTER TABLE transactions
    RENAME COLUMN purchase_date TO transaction_date;

-- RENAME COLUMN does not rename the catalogued NOT NULL constraint
-- (PostgreSQL >= 18 names them in pg_constraint). Conditional so the
-- migration also applies on older versions, where it does not exist.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'transactions_purchase_date_not_null'
          AND conrelid = 'transactions'::regclass
    ) THEN
        ALTER TABLE transactions
            RENAME CONSTRAINT transactions_purchase_date_not_null
            TO transactions_transaction_date_not_null;
    END IF;
END $$;

-- total_invested = BUY costs (fees included) minus gross SELL proceeds:
-- the net capital still at risk. It goes negative once sales returned
-- more cash than was ever invested — expected, not an error.
--
-- Realized PnL is NOT computed here: it needs average-cost/FIFO state
-- at the moment of each sale (issues 4.3/8.5). Income types contribute
-- zero to both aggregates by construction (shares = 0, total_cost = 0,
-- enforced by transactions_income_shape).
CREATE VIEW holdings AS
SELECT
    a.ticker,
    a.target_weight,
    a.asset_type,
    SUM(CASE WHEN t.transaction_type = 'BUY'  THEN t.shares ELSE 0 END)
      - SUM(CASE WHEN t.transaction_type = 'SELL' THEN t.shares ELSE 0 END) AS total_shares,
    SUM(CASE WHEN t.transaction_type = 'BUY'  THEN t.total_cost ELSE 0 END)
      - SUM(CASE WHEN t.transaction_type = 'SELL' THEN t.total_investment ELSE 0 END) AS total_invested
FROM assets a
LEFT JOIN transactions t ON t.ticker = a.ticker
GROUP BY a.ticker, a.target_weight, a.asset_type
HAVING SUM(CASE WHEN t.transaction_type = 'BUY'  THEN t.shares ELSE 0 END)
     - SUM(CASE WHEN t.transaction_type = 'SELL' THEN t.shares ELSE 0 END) > 0;
