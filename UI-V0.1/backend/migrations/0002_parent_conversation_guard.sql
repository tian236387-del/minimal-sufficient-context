CREATE TRIGGER IF NOT EXISTS messages_parent_conversation_insert
BEFORE INSERT ON messages
WHEN NEW.parent_id IS NOT NULL
AND NOT EXISTS (
    SELECT 1
    FROM messages parent
    WHERE parent.id = NEW.parent_id
      AND parent.conversation_id = NEW.conversation_id
)
BEGIN
    SELECT RAISE(ABORT, 'parent message must belong to the same conversation');
END;

CREATE TRIGGER IF NOT EXISTS messages_parent_conversation_update
BEFORE UPDATE OF parent_id, conversation_id ON messages
WHEN NEW.parent_id IS NOT NULL
AND NOT EXISTS (
    SELECT 1
    FROM messages parent
    WHERE parent.id = NEW.parent_id
      AND parent.conversation_id = NEW.conversation_id
)
BEGIN
    SELECT RAISE(ABORT, 'parent message must belong to the same conversation');
END;

