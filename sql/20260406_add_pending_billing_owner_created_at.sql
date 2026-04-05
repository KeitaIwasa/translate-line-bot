ALTER TABLE group_subscriptions
  ADD COLUMN IF NOT EXISTS pending_billing_owner_created_at TIMESTAMPTZ;

UPDATE group_subscriptions
SET pending_billing_owner_created_at = updated_at
WHERE pending_billing_owner_user_id IS NOT NULL
  AND pending_billing_owner_subscription_id IS NOT NULL
  AND pending_billing_owner_expires_at IS NOT NULL
  AND pending_billing_owner_created_at IS NULL;
