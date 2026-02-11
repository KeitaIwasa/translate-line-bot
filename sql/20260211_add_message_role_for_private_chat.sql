ALTER TABLE messages
ADD COLUMN IF NOT EXISTS message_role VARCHAR(16) NOT NULL DEFAULT 'user';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'messages_message_role_check'
    ) THEN
        ALTER TABLE messages
        ADD CONSTRAINT messages_message_role_check
        CHECK (message_role IN ('user', 'assistant'));
    END IF;
END $$;
