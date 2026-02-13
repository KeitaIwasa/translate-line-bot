ALTER TABLE group_members
  ADD COLUMN display_name TEXT,
  ADD COLUMN display_name_updated_at TIMESTAMPTZ;

UPDATE group_members
SET
  display_name = COALESCE(NULLIF(user_id, ''), '__unknown__'),
  display_name_updated_at = COALESCE(display_name_updated_at, NOW())
WHERE display_name IS NULL OR display_name_updated_at IS NULL;
