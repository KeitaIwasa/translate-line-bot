-- group_settings: translation on/off per group (for pause/resume feature)
CREATE TABLE IF NOT EXISTS group_settings (
  group_id TEXT PRIMARY KEY,
  translation_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
