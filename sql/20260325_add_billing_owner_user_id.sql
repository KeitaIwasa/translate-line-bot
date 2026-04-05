ALTER TABLE group_subscriptions
  ADD COLUMN IF NOT EXISTS billing_owner_user_id TEXT;
