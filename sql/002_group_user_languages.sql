-- Migration: introduce group_user_languages and metadata columns
ALTER TABLE group_members
    DROP COLUMN IF EXISTS preferred_lang;

ALTER TABLE group_members
    DROP COLUMN IF EXISTS updated_at;

ALTER TABLE group_members
    ADD COLUMN IF NOT EXISTS joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

ALTER TABLE group_members
    ADD COLUMN IF NOT EXISTS last_prompted_at TIMESTAMPTZ;

ALTER TABLE group_members
    ADD COLUMN IF NOT EXISTS last_completed_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_group_members_user
    ON group_members (user_id);

CREATE TABLE IF NOT EXISTS group_user_languages (
    group_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    lang_code VARCHAR(16) NOT NULL,
    lang_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (group_id, user_id, lang_code)
);

CREATE INDEX IF NOT EXISTS idx_group_user_languages_user
    ON group_user_languages (user_id);
