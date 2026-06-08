-- Track why translation is disabled so quota pauses can resume automatically.
ALTER TABLE group_settings
    ADD COLUMN IF NOT EXISTS translation_paused_reason TEXT;

ALTER TABLE group_settings
    DROP CONSTRAINT IF EXISTS chk_translation_paused_reason;

ALTER TABLE group_settings
    ADD CONSTRAINT chk_translation_paused_reason
    CHECK (translation_paused_reason IN ('quota') OR translation_paused_reason IS NULL);
