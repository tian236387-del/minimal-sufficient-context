ALTER TABLE merge_operations
    ADD COLUMN previous_active_branch_id INTEGER
    REFERENCES branches(id) ON DELETE SET NULL;

ALTER TABLE merge_operations
    ADD COLUMN previous_active_message_id INTEGER
    REFERENCES messages(id) ON DELETE SET NULL;

