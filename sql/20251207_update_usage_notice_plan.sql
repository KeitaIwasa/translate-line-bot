-- Safe migration to plan-aware notice flag (NULL / 'free' / 'pro')

ALTER TABLE group_usage_counters
  ADD COLUMN IF NOT EXISTS limit_notice_plan TEXT;

DO $$
BEGIN
  -- If legacy boolean column exists, migrate its true values to 'free' and drop it
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'group_usage_counters'
      AND column_name = 'limit_notice_sent'
  ) THEN
    EXECUTE 'UPDATE group_usage_counters SET limit_notice_plan = ''free'' WHERE limit_notice_sent = TRUE';
    EXECUTE 'ALTER TABLE group_usage_counters DROP COLUMN limit_notice_sent';
  END IF;
END$$;

-- Add CHECK constraint if not present
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE table_name = 'group_usage_counters'
      AND constraint_name = 'chk_usage_notice_plan'
  ) THEN
    EXECUTE 'ALTER TABLE group_usage_counters ADD CONSTRAINT chk_usage_notice_plan CHECK (limit_notice_plan IN (''free'', ''pro'') OR limit_notice_plan IS NULL)';
  END IF;
END$$;
