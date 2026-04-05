ALTER TABLE group_subscriptions
  ADD COLUMN IF NOT EXISTS pending_billing_owner_user_id TEXT,
  ADD COLUMN IF NOT EXISTS pending_billing_owner_subscription_id TEXT,
  ADD COLUMN IF NOT EXISTS pending_billing_owner_expires_at TIMESTAMPTZ;
