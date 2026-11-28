-- 012_category_groups: category groups, the user's category and rules (issue #116).
--
-- Pluggy's tree has 130 categories; the analyses read them in groups. A group
-- has a nature (``fixo`` or ``variavel``); ``Outros`` is the reserve group
-- (``is_fallback``): it may be renamed, never removed, and holds every
-- category nobody mapped.
--
-- ``category_group_map`` maps a Pluggy category to a group. No foreign key to
-- ``spending.categories``: the categories arrive with the first sync, and the
-- default below is written before it. A category's group is its own mapping,
-- else the nearest ancestor's (``parent_id`` of ``spending.categories``; the
-- prefix of the id would not do: the insurance children have nine digits, and
-- ``02030001``'s parent is ``02000000``), else the reserve group. One that
-- reaches the reserve group without any mapping is *unmapped*, and is warned
-- about until the user maps it (view ``spending.category_group_of``).
--
-- The category of a transaction: ``category_id`` is Pluggy's, rewritten by
-- every sync; ``rule_category_id`` is the one a rule (``rule_id``) gave it;
-- ``manual_category_id`` is the user's, which no sync touches.
-- ``effective_category_id`` is the one that counts: manual, else rule, else
-- Pluggy's.
--
-- A rule's conditions are combined by AND, and it needs a document or a
-- description (src/bogle/spending/rules.py). Applying a rule to the stored
-- transactions is recorded (``rule_applications``), with the value each
-- changed line had, so the last application can be undone exactly.

CREATE TABLE spending.category_groups (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL CHECK (btrim(name) <> ''),
    nature      TEXT NOT NULL CHECK (nature IN ('fixo', 'variavel')),
    is_fallback BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE UNIQUE INDEX category_groups_name ON spending.category_groups (lower(name));
CREATE UNIQUE INDEX category_groups_one_fallback ON spending.category_groups (is_fallback) WHERE is_fallback;

CREATE TABLE spending.category_group_map (
    category_id TEXT PRIMARY KEY,
    group_id    BIGINT NOT NULL REFERENCES spending.category_groups (id)
);

CREATE INDEX category_group_map_group ON spending.category_group_map (group_id);

INSERT INTO spending.category_groups (name, nature, is_fallback) VALUES
    ('Moradia', 'fixo', FALSE),
    ('Serviços', 'fixo', FALSE),
    ('Financiamentos', 'fixo', FALSE),
    ('Alimentação', 'variavel', FALSE),
    ('Transporte', 'variavel', FALSE),
    ('Saúde', 'variavel', FALSE),
    ('Lazer', 'variavel', FALSE),
    ('Compras', 'variavel', FALSE),
    ('Impostos e taxas', 'variavel', FALSE),
    ('Outros', 'variavel', TRUE);

-- The default, by the ids of Pluggy's tree (GET /categories, 130 categories);
-- the children follow their parent but for the exceptions listed.
INSERT INTO spending.category_group_map (category_id, group_id)
SELECT v.category_id, g.id
FROM (VALUES
    ('01000000', 'Outros'),            -- Income
    ('02000000', 'Financiamentos'),    -- Loans and financing
    ('02010000', 'Impostos e taxas'),  -- Late payment and overdraft costs
    ('02020000', 'Impostos e taxas'),  -- Interests charged
    ('03000000', 'Outros'),            -- Investments
    ('04000000', 'Outros'),            -- Same person transfer
    ('05000000', 'Outros'),            -- Transfers
    ('06000000', 'Outros'),            -- Legal obligations
    ('07000000', 'Serviços'),          -- Services
    ('07040000', 'Lazer'),             -- Tickets
    ('08000000', 'Compras'),           -- Shopping
    ('08090000', 'Outros'),            -- Cashback
    ('09000000', 'Serviços'),          -- Digital services (subscriptions)
    ('09010000', 'Lazer'),             -- Gaming
    ('10000000', 'Alimentação'),       -- Groceries
    ('11000000', 'Alimentação'),       -- Food and drinks
    ('12000000', 'Lazer'),             -- Travel
    ('13000000', 'Outros'),            -- Donations
    ('14000000', 'Lazer'),             -- Gambling
    ('15000000', 'Impostos e taxas'),  -- Taxes
    ('16000000', 'Impostos e taxas'),  -- Bank fees
    ('17000000', 'Moradia'),           -- Housing
    ('17030000', 'Compras'),           -- Houseware
    ('18000000', 'Saúde'),             -- Healthcare
    ('19000000', 'Transporte'),        -- Transportation
    ('20000000', 'Serviços'),          -- Insurance
    ('200200000', 'Moradia'),          -- Home insurance
    ('21000000', 'Lazer'),             -- Leisure
    ('99999999', 'Outros')             -- Other
) AS v (category_id, group_name)
JOIN spending.category_groups g ON g.name = v.group_name;

-- Every category known (stored, or mapped before it arrived) and its group.
-- ``mapped_from`` is the category whose mapping decided it (itself or an
-- ancestor); NULL when none did: the category is unmapped and fell back.
CREATE VIEW spending.category_group_of AS
WITH RECURSIVE known AS (
    SELECT id AS category_id FROM spending.categories
    UNION
    SELECT category_id FROM spending.category_group_map
),
chain AS (
    SELECT category_id, category_id AS ancestor_id, 0 AS depth FROM known
    UNION ALL
    SELECT chain.category_id, c.parent_id, chain.depth + 1
    FROM chain
    JOIN spending.categories c ON c.id = chain.ancestor_id
    WHERE c.parent_id IS NOT NULL AND chain.depth < 16
),
nearest AS (
    SELECT DISTINCT ON (chain.category_id) chain.category_id, m.group_id, chain.ancestor_id AS mapped_from
    FROM chain
    JOIN spending.category_group_map m ON m.category_id = chain.ancestor_id
    ORDER BY chain.category_id, chain.depth
)
SELECT
    known.category_id,
    COALESCE(nearest.group_id, (SELECT id FROM spending.category_groups WHERE is_fallback)) AS group_id,
    nearest.mapped_from
FROM known
LEFT JOIN nearest ON nearest.category_id = known.category_id;

CREATE TABLE spending.category_rules (
    id                BIGSERIAL PRIMARY KEY,
    category_id       TEXT NOT NULL REFERENCES spending.categories (id),
    -- Digits only: the merchant's CNPJ or the other party's CPF/CNPJ (paymentData).
    document          TEXT CHECK (document ~ '^[0-9]+$'),
    description       TEXT CHECK (btrim(description) <> ''),
    description_match TEXT CHECK (description_match IN ('contem', 'comeca', 'igual')),
    -- Over the absolute value of the line, in reais; both ends included.
    min_value         NUMERIC(18, 2) CHECK (min_value >= 0),
    max_value         NUMERIC(18, 2) CHECK (max_value >= 0),
    account_id        BIGINT REFERENCES spending.accounts (id),
    created_at        TIMESTAMPTZ NOT NULL,
    CHECK (document IS NOT NULL OR description IS NOT NULL),
    CHECK ((description IS NULL) = (description_match IS NULL)),
    CHECK (min_value IS NULL OR max_value IS NULL OR min_value <= max_value)
);

ALTER TABLE spending.transactions
    ADD COLUMN manual_category_id TEXT REFERENCES spending.categories (id),
    ADD COLUMN manual_category_at TIMESTAMPTZ,
    ADD COLUMN rule_id BIGINT REFERENCES spending.category_rules (id),
    ADD COLUMN rule_category_id TEXT REFERENCES spending.categories (id),
    ADD COLUMN effective_category_id TEXT
        GENERATED ALWAYS AS (COALESCE(manual_category_id, rule_category_id, category_id)) STORED,
    ADD CONSTRAINT transactions_rule_category CHECK ((rule_id IS NULL) = (rule_category_id IS NULL));

CREATE INDEX transactions_rule ON spending.transactions (rule_id) WHERE rule_id IS NOT NULL;

CREATE TABLE spending.rule_applications (
    id         BIGSERIAL PRIMARY KEY,
    rule_id    BIGINT NOT NULL REFERENCES spending.category_rules (id) ON DELETE CASCADE,
    applied_at TIMESTAMPTZ NOT NULL
);

-- Each line an application changed, and what it had before.
CREATE TABLE spending.rule_application_changes (
    application_id       BIGINT NOT NULL REFERENCES spending.rule_applications (id) ON DELETE CASCADE,
    transaction_id       BIGINT NOT NULL REFERENCES spending.transactions (id) ON DELETE CASCADE,
    previous_rule_id     BIGINT,
    previous_category_id TEXT,
    PRIMARY KEY (application_id, transaction_id)
);

-- As in 011, now with the category that counts and its group. ``category_id``
-- and ``category`` are the effective ones (the user's, a rule's or Pluggy's,
-- ``category_source`` says which); ``pluggy_category_id`` is Pluggy's own.
DROP VIEW spending.expenses;

CREATE VIEW spending.expenses AS
SELECT
    t.id AS transaction_id,
    t.account_id,
    a.type AS account_type,
    t.local_date,
    t.status,
    t.kind,
    t.effective_category_id AS category_id,
    CASE
        WHEN t.manual_category_id IS NOT NULL THEN COALESCE(c.description_translated, c.description, t.manual_category_id)
        WHEN t.rule_category_id IS NOT NULL THEN COALESCE(c.description_translated, c.description, t.rule_category_id)
        ELSE COALESCE(c.description_translated, c.description, t.category)
    END AS category,
    CASE
        WHEN t.manual_category_id IS NOT NULL THEN 'manual'
        WHEN t.rule_category_id IS NOT NULL THEN 'regra'
        ELSE 'pluggy'
    END AS category_source,
    t.category_id AS pluggy_category_id,
    g.id AS group_id,
    g.name AS group_name,
    g.nature AS group_nature,
    t.description,
    CASE
        WHEN a.type = 'CREDIT' THEN COALESCE(t.amount_in_account_currency, t.amount)
        ELSE -COALESCE(t.amount_in_account_currency, t.amount)
    END AS value
FROM spending.transactions t
JOIN spending.accounts a ON a.id = t.account_id
LEFT JOIN spending.categories c ON c.id = t.effective_category_id
LEFT JOIN spending.category_group_of r ON r.category_id = t.effective_category_id
LEFT JOIN spending.category_groups g ON g.id = COALESCE(
    r.group_id, (SELECT id FROM spending.category_groups WHERE is_fallback)
)
WHERE t.kind IN ('gasto', 'abatimento')
  AND NOT EXISTS (
      SELECT 1
      FROM spending.duplicate_suggestions d
      WHERE d.transaction_id = t.id AND d.status = 'PENDING'
  );
