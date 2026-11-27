-- 008_price_history_tesouro_source: Tesouro Direto prices in price_history (issue #87).
--
-- The daily PU of each Tesouro title of the portfolio (PU Venda Manha, from the
-- Tesouro Transparente CSV) is kept with the closes, under the title name as
-- ``symbol``. It is a writer of its own: add-only, and no provider corrects it.
-- Only the CHECK changes.

ALTER TABLE price_history
    DROP CONSTRAINT price_history_source_check;

ALTER TABLE price_history
    ADD CONSTRAINT price_history_source_check
    CHECK (source IN ('yfinance', 'brapi', 'tesouro'));
