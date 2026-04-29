-- 002_extend_assets_for_asset_types: support multiple asset types.
--
-- Adds the metadata columns needed to describe BDR/FII/ETF, Tesouro Direto
-- and private fixed-income instruments (CDB/RDB/LCI/LCA/CAIXINHA). The
-- CHECK constraints below enforce coherence between fields so that an
-- inconsistent row (e.g. a STOCK with a maturity date, a non-prefixed CDB
-- without an indexer) cannot be persisted.

ALTER TABLE assets
    ADD COLUMN asset_type      TEXT NOT NULL,
    ADD COLUMN issuer          TEXT,
    ADD COLUMN indexer         TEXT,
    ADD COLUMN rate            NUMERIC(10, 6),
    ADD COLUMN is_prefixed     BOOLEAN,
    ADD COLUMN daily_liquidity BOOLEAN,
    ADD COLUMN purchase_date   TIMESTAMPTZ,
    ADD COLUMN maturity_date   TIMESTAMPTZ;

-- 1) asset_type must be one of the supported categories.
ALTER TABLE assets
    ADD CONSTRAINT assets_asset_type_valid CHECK (
        asset_type IN (
            'STOCK', 'BDR', 'FII', 'ETF',
            'TESOURO',
            'CDB', 'RDB', 'LCI', 'LCA', 'CAIXINHA'
        )
    );

-- 2) indexer (when set) must be one of the supported tags.
ALTER TABLE assets
    ADD CONSTRAINT assets_indexer_valid CHECK (
        indexer IS NULL
        OR indexer IN ('CDI', 'CDI+', 'IPCA+', 'SELIC', 'PREFIXADO')
    );

-- 3) Variable-income instruments must not carry any fixed-income metadata.
ALTER TABLE assets
    ADD CONSTRAINT assets_variable_income_clean CHECK (
        asset_type NOT IN ('STOCK', 'BDR', 'FII', 'ETF')
        OR (
            issuer IS NULL
            AND indexer IS NULL
            AND rate IS NULL
            AND is_prefixed IS NULL
            AND daily_liquidity IS NULL
            AND purchase_date IS NULL
            AND maturity_date IS NULL
        )
    );

-- 4) Prefixed instruments do not have an indexer.
ALTER TABLE assets
    ADD CONSTRAINT assets_prefixed_no_indexer CHECK (
        is_prefixed IS NULL OR is_prefixed = false OR indexer IS NULL
    );

-- 5) Instruments without daily liquidity must declare a maturity date.
ALTER TABLE assets
    ADD CONSTRAINT assets_no_liquidity_requires_maturity CHECK (
        daily_liquidity IS NULL
        OR daily_liquidity = true
        OR maturity_date IS NOT NULL
    );

-- 6) Defensive: private fixed income (banco emissor) must declare the issuer.
ALTER TABLE assets
    ADD CONSTRAINT assets_private_fixed_income_requires_issuer CHECK (
        asset_type NOT IN ('CDB', 'RDB', 'LCI', 'LCA', 'CAIXINHA')
        OR issuer IS NOT NULL
    );

-- 7) Defensive: non-prefixed fixed income must declare an indexer.
ALTER TABLE assets
    ADD CONSTRAINT assets_postfixed_requires_indexer CHECK (
        asset_type NOT IN ('TESOURO', 'CDB', 'RDB', 'LCI', 'LCA', 'CAIXINHA')
        OR is_prefixed IS NULL
        OR is_prefixed = true
        OR indexer IS NOT NULL
    );
