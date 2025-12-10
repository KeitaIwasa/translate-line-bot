-- Stripe 課金用テーブル追加

CREATE TABLE IF NOT EXISTS group_subscriptions (
    group_id TEXT PRIMARY KEY,
    stripe_customer_id TEXT,
    stripe_subscription_id TEXT,
    status TEXT NOT NULL DEFAULT 'trialing',
    current_period_end TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (status IN (
        'active', 'canceled', 'trialing', 'unpaid',
        'incomplete', 'incomplete_expired', 'past_due'
    ))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_group_subscriptions_subscription_id
    ON group_subscriptions (stripe_subscription_id);

CREATE TABLE IF NOT EXISTS group_usage_counters (
    group_id TEXT NOT NULL,
    month_key VARCHAR(7) NOT NULL,
    translation_count INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (group_id, month_key)
);
