ALTER TABLE group_members
  ALTER COLUMN display_name DROP NOT NULL,
  ALTER COLUMN display_name_updated_at DROP NOT NULL,
  ALTER COLUMN display_name_updated_at DROP DEFAULT;

UPDATE group_members
SET
  display_name = NULL,
  display_name_updated_at = NULL
WHERE display_name = user_id;
