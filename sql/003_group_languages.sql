-- Migration: introduce group-level language settings
CREATE TABLE IF NOT EXISTS group_languages (
    group_id TEXT NOT NULL,
    lang_code VARCHAR(16) NOT NULL,
    lang_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (group_id, lang_code)
);

-- Seed from existing per-user settings if present
INSERT INTO group_languages (group_id, lang_code, lang_name)
SELECT DISTINCT gul.group_id, gul.lang_code, gul.lang_name
FROM group_user_languages gul
ON CONFLICT (group_id, lang_code) DO NOTHING;
