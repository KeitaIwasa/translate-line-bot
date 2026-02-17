-- Reorganize subscription plans: Free / Standard / Pro + legacy Pro

BEGIN;

ALTER TABLE group_subscriptions
  ADD COLUMN IF NOT EXISTS stripe_price_id TEXT,
  ADD COLUMN IF NOT EXISTS entitlement_plan TEXT,
  ADD COLUMN IF NOT EXISTS billing_interval TEXT,
  ADD COLUMN IF NOT EXISTS is_grandfathered BOOLEAN,
  ADD COLUMN IF NOT EXISTS quota_anchor_day SMALLINT,
  ADD COLUMN IF NOT EXISTS scheduled_target_price_id TEXT,
  ADD COLUMN IF NOT EXISTS scheduled_effective_at TIMESTAMPTZ;

ALTER TABLE group_subscriptions
  ALTER COLUMN entitlement_plan SET DEFAULT 'free',
  ALTER COLUMN billing_interval SET DEFAULT 'month',
  ALTER COLUMN is_grandfathered SET DEFAULT FALSE;

UPDATE group_subscriptions
SET
  billing_interval = COALESCE(NULLIF(billing_interval, ''), 'month'),
  is_grandfathered = COALESCE(is_grandfathered, FALSE)
WHERE billing_interval IS NULL
   OR is_grandfathered IS NULL;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE table_name = 'group_subscriptions'
      AND constraint_name = 'chk_group_subscriptions_entitlement_plan'
  ) THEN
    ALTER TABLE group_subscriptions DROP CONSTRAINT chk_group_subscriptions_entitlement_plan;
  END IF;

  ALTER TABLE group_subscriptions
    ADD CONSTRAINT chk_group_subscriptions_entitlement_plan
    CHECK (entitlement_plan IN ('free', 'standard', 'pro'));
END$$;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE table_name = 'group_subscriptions'
      AND constraint_name = 'chk_group_subscriptions_billing_interval'
  ) THEN
    ALTER TABLE group_subscriptions DROP CONSTRAINT chk_group_subscriptions_billing_interval;
  END IF;

  ALTER TABLE group_subscriptions
    ADD CONSTRAINT chk_group_subscriptions_billing_interval
    CHECK (billing_interval IN ('month', 'year', 'legacy_month'));
END$$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE table_name = 'group_subscriptions'
      AND constraint_name = 'chk_group_subscriptions_quota_anchor_day'
  ) THEN
    ALTER TABLE group_subscriptions
      ADD CONSTRAINT chk_group_subscriptions_quota_anchor_day
      CHECK (quota_anchor_day IS NULL OR (quota_anchor_day BETWEEN 1 AND 31));
  END IF;
END$$;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE table_name = 'group_usage_counters'
      AND constraint_name = 'chk_usage_notice_plan'
  ) THEN
    ALTER TABLE group_usage_counters DROP CONSTRAINT chk_usage_notice_plan;
  END IF;

  ALTER TABLE group_usage_counters
    ADD CONSTRAINT chk_usage_notice_plan
    CHECK (limit_notice_plan IN ('free', 'standard', 'pro') OR limit_notice_plan IS NULL);
END$$;

ALTER TABLE messages
  ADD COLUMN IF NOT EXISTS encrypted_body TEXT,
  ADD COLUMN IF NOT EXISTS encryption_version TEXT,
  ADD COLUMN IF NOT EXISTS is_encrypted BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_group_subscriptions_price_id
  ON group_subscriptions (stripe_price_id);

CREATE INDEX IF NOT EXISTS idx_group_subscriptions_scheduled_effective_at
  ON group_subscriptions (scheduled_effective_at)
  WHERE scheduled_effective_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_messages_encrypted_timestamp
  ON messages (timestamp)
  WHERE is_encrypted = TRUE;

COMMIT;
