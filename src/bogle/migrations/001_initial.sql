-- 001_initial: minimal portfolio schema.
--
-- assets       — user-defined target allocation per ticker.
-- transactions — record of purchases (sales/dividends arrive in 003).
-- holdings     — simple aggregation view (filter for active positions arrives in 004).

CREATE TABLE assets (
    ticker        TEXT PRIMARY KEY,
    target_weight NUMERIC(5, 4) NOT NULL CHECK (target_weight > 0 AND target_weight <= 1)
);

CREATE TABLE transactions (
    id               BIGSERIAL      PRIMARY KEY,
    ticker           TEXT           NOT NULL REFERENCES assets(ticker),
    purchase_date    TIMESTAMPTZ    NOT NULL,
    shares           NUMERIC(20, 8) NOT NULL,
    unit_price       NUMERIC(20, 4) NOT NULL,
    total_investment NUMERIC(20, 4) NOT NULL,
    fees             NUMERIC(20, 4) NOT NULL DEFAULT 0,
    total_cost       NUMERIC(20, 4) NOT NULL
);

CREATE VIEW holdings AS
SELECT
    t.ticker,
    a.target_weight,
    SUM(t.shares)                      AS total_shares,
    SUM(t.total_cost)                  AS total_cost,
    SUM(t.total_cost) / SUM(t.shares)  AS avg_cost_per_share
FROM transactions t
JOIN assets a ON t.ticker = a.ticker
GROUP BY t.ticker, a.target_weight;
