-- 010_spending_schema: accounts, transactions and bills from Meu Pluggy (issue #114).
--
-- A schema of its own, apart from the portfolio in ``public``: nothing here is
-- read by the portfolio code, and the analyses of #115 onward read only this.
--
-- Every row keeps Pluggy's id (``pluggy_id``) and the JSON it came from
-- (``raw``), but the primary keys are local. Pluggy's ids change when the
-- consent is renewed (a new item, new account and transaction ids), and a
-- transaction id may change inside the same item: the local id is what stays,
-- and the sync remaps the Pluggy ids onto it (src/bogle/spending/sync.py).
--
-- ``local_date`` is the day the statement shows: on the card, the bill's
-- ``billPostDate``; otherwise ``date`` (UTC in the API; midnight UTC, with or
-- without milliseconds, is a bare date, anything else a Sao Paulo instant).
-- Amounts follow Pluggy's sign: on a credit card a positive amount is
-- a purchase.

CREATE SCHEMA spending;

-- One row per connection in PLUGGY_ITEM_IDS, as last read from GET /items.
CREATE TABLE spending.items (
    id                 TEXT PRIMARY KEY,
    connector_id       INTEGER,
    connector_name     TEXT,
    status             TEXT NOT NULL,
    execution_status   TEXT,
    error_code         TEXT,
    error_message      TEXT,
    pluggy_created_at  TIMESTAMPTZ,
    last_updated_at    TIMESTAMPTZ,
    consent_expires_at TIMESTAMPTZ,
    checked_at         TIMESTAMPTZ NOT NULL
);

-- Pluggy's category tree; upserted, never deleted (transactions point at it).
CREATE TABLE spending.categories (
    id                     TEXT PRIMARY KEY,
    description            TEXT NOT NULL,
    description_translated TEXT,
    parent_id              TEXT,
    parent_description     TEXT
);

CREATE TABLE spending.accounts (
    id                     BIGSERIAL PRIMARY KEY,
    pluggy_id              TEXT NOT NULL UNIQUE,
    -- The ids this account had before a renewal of the consent.
    replaced_pluggy_ids    TEXT[] NOT NULL DEFAULT '{}',
    item_id                TEXT REFERENCES spending.items (id) ON DELETE SET NULL,
    type                   TEXT NOT NULL CHECK (type IN ('BANK', 'CREDIT')),
    subtype                TEXT NOT NULL,
    number                 TEXT,
    name                   TEXT NOT NULL,
    marketing_name         TEXT,
    owner                  TEXT,
    tax_number             TEXT,
    currency_code          TEXT NOT NULL,
    balance                NUMERIC(18, 2),
    transfer_number        TEXT,
    credit_brand           TEXT,
    credit_level           TEXT,
    credit_limit           NUMERIC(18, 2),
    available_credit_limit NUMERIC(18, 2),
    minimum_payment        NUMERIC(18, 2),
    balance_close_date     DATE,
    balance_due_date       DATE,
    -- Freshness and coverage: the last sync that applied, the newest past
    -- transaction, and the oldest one (#117 counts months from it).
    last_synced_at         TIMESTAMPTZ,
    first_transaction_date DATE,
    last_transaction_date  DATE,
    raw                    JSONB NOT NULL
);

CREATE TABLE spending.bills (
    id                     BIGSERIAL PRIMARY KEY,
    pluggy_id              TEXT NOT NULL UNIQUE,
    account_id             BIGINT NOT NULL REFERENCES spending.accounts (id),
    due_date               DATE NOT NULL,
    closing_date           DATE,
    total_amount           NUMERIC(18, 2) NOT NULL,
    currency_code          TEXT NOT NULL,
    minimum_payment_amount NUMERIC(18, 2),
    allows_installments    BOOLEAN,
    finance_charges        JSONB NOT NULL DEFAULT '[]',
    payments               JSONB NOT NULL DEFAULT '[]',
    raw                    JSONB NOT NULL,
    first_seen_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX bills_account_due_date ON spending.bills (account_id, due_date);

CREATE TABLE spending.transactions (
    id                         BIGSERIAL PRIMARY KEY,
    pluggy_id                  TEXT NOT NULL UNIQUE,
    account_id                 BIGINT NOT NULL REFERENCES spending.accounts (id),
    provider_id                TEXT,
    provider_code              TEXT,
    date                       TIMESTAMPTZ NOT NULL,
    local_date                 DATE NOT NULL,
    status                     TEXT NOT NULL CHECK (status IN ('PENDING', 'POSTED')),
    type                       TEXT NOT NULL CHECK (type IN ('DEBIT', 'CREDIT')),
    amount                     NUMERIC(18, 2) NOT NULL,
    currency_code              TEXT NOT NULL,
    amount_in_account_currency NUMERIC(18, 2),
    balance                    NUMERIC(18, 2),
    description                TEXT NOT NULL,
    description_raw            TEXT,
    category_id                TEXT,
    category                   TEXT,
    operation_type             TEXT,
    merchant_name              TEXT,
    merchant_business_name     TEXT,
    merchant_cnpj              TEXT,
    merchant_cnae              TEXT,
    merchant_category          TEXT,
    payment_data               JSONB,
    -- creditCardMetadata: the bill (Pluggy's id), when the bank posted the line
    -- to it, the month it is expected on (YYYY-MM), and the installments.
    bill_id                    TEXT,
    bill_post_date             DATE,
    bill_forecast_month        TEXT,
    payment_type               TEXT,
    installment_number         SMALLINT,
    total_installments         SMALLINT,
    installments_total_amount  NUMERIC(18, 2),
    purchase_date              DATE,
    payee_mcc                  TEXT,
    card_number                TEXT,
    pluggy_created_at          TIMESTAMPTZ,
    pluggy_updated_at          TIMESTAMPTZ,
    raw                        JSONB NOT NULL,
    first_seen_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX transactions_account_local_date ON spending.transactions (account_id, local_date);
CREATE INDEX transactions_account_provider_id ON spending.transactions (account_id, provider_id)
    WHERE provider_id IS NOT NULL;
CREATE INDEX transactions_bill_id ON spending.transactions (bill_id) WHERE bill_id IS NOT NULL;

-- A transaction that may repeat an older one: same day, amount and description,
-- but neither the same Pluggy id, nor providerId, nor the exact natural key
-- (instant, amount, description, installment) that the sync remaps by. While PENDING, ``transaction_id`` stays out
-- of the analyses; confirming merges it into ``duplicate_of_id``, rejecting
-- keeps both for good.
CREATE TABLE spending.duplicate_suggestions (
    id              BIGSERIAL PRIMARY KEY,
    transaction_id  BIGINT NOT NULL UNIQUE REFERENCES spending.transactions (id) ON DELETE CASCADE,
    duplicate_of_id BIGINT NOT NULL REFERENCES spending.transactions (id) ON DELETE CASCADE,
    status          TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'REJECTED')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ,
    CHECK (transaction_id <> duplicate_of_id)
);

CREATE UNIQUE INDEX duplicate_suggestions_open_candidate ON spending.duplicate_suggestions (duplicate_of_id)
    WHERE status = 'PENDING';
