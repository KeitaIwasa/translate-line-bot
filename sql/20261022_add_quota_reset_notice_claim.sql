-- 無料枠の月次リセット案内を、周期につき1回に抑える。

ALTER TABLE group_usage_counters
  ADD COLUMN IF NOT EXISTS quota_reset_notice_sent BOOLEAN NOT NULL DEFAULT FALSE;
