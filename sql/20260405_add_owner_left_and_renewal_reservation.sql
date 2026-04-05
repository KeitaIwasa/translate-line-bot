ALTER TABLE group_members
  ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS left_at TIMESTAMPTZ;

UPDATE group_members
SET active = TRUE
WHERE active IS NULL;

ALTER TABLE group_subscriptions
  ADD COLUMN IF NOT EXISTS billing_owner_lost_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS renewal_owner_user_id TEXT,
  ADD COLUMN IF NOT EXISTS renewal_stripe_customer_id TEXT,
  ADD COLUMN IF NOT EXISTS renewal_subscription_schedule_id TEXT,
  ADD COLUMN IF NOT EXISTS renewal_setup_session_id TEXT,
  ADD COLUMN IF NOT EXISTS renewal_effective_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS renewal_price_id TEXT,
  ADD COLUMN IF NOT EXISTS renewal_plan TEXT,
  ADD COLUMN IF NOT EXISTS renewal_billing_interval TEXT;
