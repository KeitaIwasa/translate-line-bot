CREATE TABLE IF NOT EXISTS contact_rate_limits (
    ip_hash TEXT NOT NULL,
    window_start TIMESTAMPTZ NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ip_hash, window_start)
);

CREATE INDEX IF NOT EXISTS idx_contact_rate_limits_updated_at
    ON contact_rate_limits (updated_at);
