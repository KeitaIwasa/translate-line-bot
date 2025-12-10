-- add group_name to group_settings for storing LINE group display name
ALTER TABLE group_settings
    ADD COLUMN IF NOT EXISTS group_name TEXT;

-- backfill updated_at when group_name changes will be handled in application layer
