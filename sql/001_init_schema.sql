-- Initial Neon schema for LINE translation bot
CREATE TABLE IF NOT EXISTS group_members (
    group_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_prompted_at TIMESTAMPTZ,
    last_completed_at TIMESTAMPTZ,
    PRIMARY KEY (group_id, user_id)
);

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

CREATE TABLE IF NOT EXISTS messages (
    id BIGSERIAL PRIMARY KEY,
    group_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    sender_name TEXT,
    text TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_messages_group_ts
    ON messages (group_id, timestamp DESC);
