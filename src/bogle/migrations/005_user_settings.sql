-- 005_user_settings: user preferences as JSONB key/value rows.
--
-- One row per setting; absent row means "use the default" (defaults live in
-- code, src/bogle/settings.py, alongside per-key type validation). JSONB keeps
-- the schema stable as new settings appear — no migration per key.

CREATE TABLE user_settings (
    key        TEXT        PRIMARY KEY,
    value      JSONB       NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
