-- 課金停止を手動停止（理由 NULL）と区別する。
ALTER TABLE group_settings
    DROP CONSTRAINT IF EXISTS chk_translation_paused_reason;

ALTER TABLE group_settings
    ADD CONSTRAINT chk_translation_paused_reason
    CHECK (translation_paused_reason IN ('quota', 'billing') OR translation_paused_reason IS NULL);
