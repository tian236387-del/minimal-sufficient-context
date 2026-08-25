CREATE TABLE branches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    name TEXT NOT NULL COLLATE NOCASE,
    forked_from_message_id INTEGER,
    root_message_id INTEGER,
    head_message_id INTEGER,
    is_main INTEGER NOT NULL DEFAULT 0 CHECK(is_main IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
    FOREIGN KEY(forked_from_message_id) REFERENCES messages(id) ON DELETE SET NULL,
    FOREIGN KEY(root_message_id) REFERENCES messages(id) ON DELETE SET NULL,
    FOREIGN KEY(head_message_id) REFERENCES messages(id) ON DELETE SET NULL,
    UNIQUE(conversation_id, name)
);

ALTER TABLE conversations
    ADD COLUMN active_message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL;

ALTER TABLE conversations
    ADD COLUMN active_branch_id INTEGER REFERENCES branches(id) ON DELETE SET NULL;

ALTER TABLE conversations
    ADD COLUMN token_budget INTEGER NOT NULL DEFAULT 8192
    CHECK(token_budget BETWEEN 256 AND 262144);

CREATE INDEX idx_branches_conversation ON branches(conversation_id);
CREATE INDEX idx_branches_head ON branches(head_message_id);
CREATE UNIQUE INDEX idx_branches_one_main
    ON branches(conversation_id)
    WHERE is_main = 1;

CREATE TRIGGER branches_message_conversation_insert
BEFORE INSERT ON branches
WHEN (
    NEW.forked_from_message_id IS NOT NULL
    AND NOT EXISTS (
        SELECT 1 FROM messages
        WHERE id = NEW.forked_from_message_id
          AND conversation_id = NEW.conversation_id
    )
) OR (
    NEW.root_message_id IS NOT NULL
    AND NOT EXISTS (
        SELECT 1 FROM messages
        WHERE id = NEW.root_message_id
          AND conversation_id = NEW.conversation_id
    )
) OR (
    NEW.head_message_id IS NOT NULL
    AND NOT EXISTS (
        SELECT 1 FROM messages
        WHERE id = NEW.head_message_id
          AND conversation_id = NEW.conversation_id
    )
)
BEGIN
    SELECT RAISE(ABORT, 'branch messages must belong to the same conversation');
END;

CREATE TRIGGER branches_message_conversation_update
BEFORE UPDATE OF conversation_id, forked_from_message_id, root_message_id, head_message_id
ON branches
WHEN (
    NEW.forked_from_message_id IS NOT NULL
    AND NOT EXISTS (
        SELECT 1 FROM messages
        WHERE id = NEW.forked_from_message_id
          AND conversation_id = NEW.conversation_id
    )
) OR (
    NEW.root_message_id IS NOT NULL
    AND NOT EXISTS (
        SELECT 1 FROM messages
        WHERE id = NEW.root_message_id
          AND conversation_id = NEW.conversation_id
    )
) OR (
    NEW.head_message_id IS NOT NULL
    AND NOT EXISTS (
        SELECT 1 FROM messages
        WHERE id = NEW.head_message_id
          AND conversation_id = NEW.conversation_id
    )
)
BEGIN
    SELECT RAISE(ABORT, 'branch messages must belong to the same conversation');
END;

CREATE TRIGGER conversations_active_state_guard
BEFORE UPDATE OF active_message_id, active_branch_id ON conversations
WHEN (
    NEW.active_message_id IS NOT NULL
    AND NOT EXISTS (
        SELECT 1 FROM messages
        WHERE id = NEW.active_message_id
          AND conversation_id = NEW.id
    )
) OR (
    NEW.active_branch_id IS NOT NULL
    AND NOT EXISTS (
        SELECT 1 FROM branches
        WHERE id = NEW.active_branch_id
          AND conversation_id = NEW.id
    )
)
BEGIN
    SELECT RAISE(ABORT, 'active state must belong to the same conversation');
END;

