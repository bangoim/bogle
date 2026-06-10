-- 003_transaction_types: support sales and income events in transactions.
--
-- Until now every row was implicitly a purchase. This migration adds the
-- transaction_type discriminator (existing rows become BUY via the
-- default) and tax_withheld for income tax retained at source (JCP 15%,
-- "dedo-duro" on sales, etc.). Semantics per type:
--
--   BUY       shares > 0, unit_price > 0, total_cost = investment + fees
--   SELL      shares > 0 (quantity sold), unit_price > 0, total_cost = fees
--   DIVIDEND  income: gross amount in total_investment, shares/price = 0
--   JCP       income, tax_withheld = 15% at source
--   RENDIMENTO  income (FII), exempt for individuals
--   INTEREST  income (fixed income coupons / redemption yield)

-- Pre-check: existing rows become BUY below, and constraint 3 requires
-- trades to have positive shares/price. The legacy API never validated
-- inputs, so fail loudly (with the offending ids) instead of letting
-- ADD CONSTRAINT abort with an opaque error.
DO $$
DECLARE
    bad_ids TEXT;
BEGIN
    SELECT string_agg(id::text, ', ') INTO bad_ids
    FROM transactions
    WHERE shares <= 0 OR unit_price <= 0;
    IF bad_ids IS NOT NULL THEN
        RAISE EXCEPTION
            'migracao 003 abortada: transactions com shares ou unit_price <= 0 (ids: %). Corrija ou remova essas linhas antes de migrar.',
            bad_ids;
    END IF;
END $$;

ALTER TABLE transactions
    ADD COLUMN transaction_type TEXT NOT NULL DEFAULT 'BUY',
    ADD COLUMN tax_withheld     NUMERIC(20, 4) NOT NULL DEFAULT 0;

-- 1) transaction_type must be one of the supported kinds.
ALTER TABLE transactions
    ADD CONSTRAINT transactions_type_valid CHECK (
        transaction_type IN ('BUY', 'SELL', 'DIVIDEND', 'JCP', 'RENDIMENTO', 'INTEREST')
    );

-- 2) Withheld tax is never negative.
ALTER TABLE transactions
    ADD CONSTRAINT transactions_tax_withheld_non_negative CHECK (
        tax_withheld >= 0
    );

-- 3) Trades (BUY/SELL) carry a positive quantity and price.
ALTER TABLE transactions
    ADD CONSTRAINT transactions_trade_requires_shares_and_price CHECK (
        transaction_type NOT IN ('BUY', 'SELL')
        OR (shares > 0 AND unit_price > 0)
    );

-- 4) Income events carry only the gross amount (in total_investment);
--    every other money field is pinned to zero.
ALTER TABLE transactions
    ADD CONSTRAINT transactions_income_shape CHECK (
        transaction_type IN ('BUY', 'SELL')
        OR (
            shares = 0 AND unit_price = 0 AND total_investment > 0
            AND fees = 0 AND total_cost = 0
        )
    );

-- Income rows have shares = 0, which would make the holdings view crash
-- with division by zero for a ticker that only has income transactions.
-- NULLIF is the minimal stopgap: avg_cost_per_share becomes NULL there.
-- The full semantic rework of the view (BUY - SELL, active-position
-- filter) is issue 3.2 / migration 004.
CREATE OR REPLACE VIEW holdings AS
SELECT
    t.ticker,
    a.target_weight,
    SUM(t.shares)                                  AS total_shares,
    SUM(t.total_cost)                              AS total_cost,
    SUM(t.total_cost) / NULLIF(SUM(t.shares), 0)   AS avg_cost_per_share
FROM transactions t
JOIN assets a ON t.ticker = a.ticker
GROUP BY t.ticker, a.target_weight;
