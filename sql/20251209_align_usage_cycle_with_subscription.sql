-- Align usage counters with Stripe billing cycles
-- 1) store current_period_start from Stripe
-- 2) switch usage counter key from calendar month to period start date (YYYY-MM-DD)

BEGIN;

-- group_subscriptions: keep track of cycle start
ALTER TABLE group_subscriptions
  ADD COLUMN IF NOT EXISTS current_period_start TIMESTAMPTZ;

-- group_usage_counters: rename month_key -> period_key and widen length
ALTER TABLE group_usage_counters
  RENAME COLUMN month_key TO period_key;

ALTER TABLE group_usage_counters
  ALTER COLUMN period_key TYPE VARCHAR(10);

-- backfill existing keys (calendar month -> first day)
UPDATE group_usage_counters
SET period_key = period_key || '-01'
WHERE length(period_key) = 7;

COMMIT;
