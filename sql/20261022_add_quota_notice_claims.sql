-- 無料枠の事前案内と、停止中メンションの再案内を期間内で1回に抑える。

ALTER TABLE group_usage_counters
  ADD COLUMN IF NOT EXISTS approaching_notice_sent BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE group_usage_counters
  ADD COLUMN IF NOT EXISTS quota_mention_reminder_on DATE;
