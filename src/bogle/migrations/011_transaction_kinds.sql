-- 011_transaction_kinds: what each spending transaction is (issue #115).
--
-- ``auto_kind`` and ``auto_reason`` are the rules' verdict
-- (src/bogle/spending/classify.py), rewritten by every sync. ``manual_kind`` is
-- the user's, and no sync touches it. ``kind`` is the one that counts: the
-- user's when there is one, else the rules'.
--
-- ``paired_transaction_id`` is the other side of a pair the rules matched: the
-- checking-account debit and the card's bill payment, or the two ends of a
-- transfer between the user's own accounts.

ALTER TABLE spending.transactions
    ADD COLUMN auto_kind TEXT CHECK (auto_kind IN (
        'gasto', 'receita', 'transferencia_propria', 'pagamento_fatura', 'investimento', 'abatimento', 'ignorada'
    )),
    ADD COLUMN auto_reason TEXT,
    ADD COLUMN paired_transaction_id BIGINT REFERENCES spending.transactions (id) ON DELETE SET NULL,
    ADD COLUMN manual_kind TEXT CHECK (manual_kind IN (
        'gasto', 'receita', 'transferencia_propria', 'pagamento_fatura', 'investimento', 'abatimento', 'ignorada'
    )),
    ADD COLUMN manual_kind_at TIMESTAMPTZ,
    ADD COLUMN kind TEXT GENERATED ALWAYS AS (COALESCE(manual_kind, auto_kind)) STORED;

CREATE INDEX transactions_kind_local_date ON spending.transactions (kind, local_date);

-- What every spending analysis reads (#117 onward): spending and refunds only,
-- never a transfer between own accounts, a bill payment, an investment, income
-- or an ignored line, and never the newer side of a PENDING duplicate
-- suggestion. ``value`` is positive for money spent and negative for a refund,
-- whatever the account: Pluggy's card amounts are positive for a purchase, the
-- checking account's are negative for a debit. It is in reais: a purchase abroad
-- has ``amount`` in its own currency and the reais in ``amount_in_account_currency``.
CREATE VIEW spending.expenses AS
SELECT
    t.id AS transaction_id,
    t.account_id,
    a.type AS account_type,
    t.local_date,
    t.status,
    t.kind,
    t.category_id,
    t.category,
    t.description,
    CASE
        WHEN a.type = 'CREDIT' THEN COALESCE(t.amount_in_account_currency, t.amount)
        ELSE -COALESCE(t.amount_in_account_currency, t.amount)
    END AS value
FROM spending.transactions t
JOIN spending.accounts a ON a.id = t.account_id
WHERE t.kind IN ('gasto', 'abatimento')
  AND NOT EXISTS (
      SELECT 1
      FROM spending.duplicate_suggestions d
      WHERE d.transaction_id = t.id AND d.status = 'PENDING'
  );
