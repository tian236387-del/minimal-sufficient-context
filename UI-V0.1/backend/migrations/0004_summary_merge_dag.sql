CREATE TABLE summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    branch_id INTEGER,
    anchor_message_id INTEGER,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'branch'
        CHECK(kind IN ('branch', 'merge_snapshot')),
    version INTEGER NOT NULL DEFAULT 1 CHECK(version > 0),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'orphaned', 'archived')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
    FOREIGN KEY(branch_id) REFERENCES branches(id) ON DELETE SET NULL,
    FOREIGN KEY(anchor_message_id) REFERENCES messages(id) ON DELETE SET NULL
);

CREATE TABLE summary_sources (
    summary_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    source_branch_id INTEGER,
    source_order INTEGER NOT NULL CHECK(source_order >= 0),
    PRIMARY KEY(summary_id, message_id),
    FOREIGN KEY(summary_id) REFERENCES summaries(id) ON DELETE CASCADE,
    FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE CASCADE,
    FOREIGN KEY(source_branch_id) REFERENCES branches(id) ON DELETE SET NULL
);

CREATE TABLE summary_claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    summary_id INTEGER NOT NULL,
    claim_key TEXT NOT NULL,
    claim_value TEXT NOT NULL,
    claim_text TEXT NOT NULL,
    source_message_id INTEGER NOT NULL,
    claim_order INTEGER NOT NULL DEFAULT 0 CHECK(claim_order >= 0),
    created_at TEXT NOT NULL,
    FOREIGN KEY(summary_id) REFERENCES summaries(id) ON DELETE CASCADE,
    FOREIGN KEY(source_message_id) REFERENCES messages(id) ON DELETE CASCADE
);

CREATE INDEX idx_summaries_conversation
    ON summaries(conversation_id, created_at DESC);
CREATE INDEX idx_summaries_branch
    ON summaries(branch_id, version DESC);
CREATE INDEX idx_summary_sources_message
    ON summary_sources(message_id);
CREATE INDEX idx_summary_claims_summary
    ON summary_claims(summary_id, claim_order);

CREATE TRIGGER summaries_conversation_guard
BEFORE INSERT ON summaries
WHEN (
    NEW.branch_id IS NOT NULL
    AND NOT EXISTS (
        SELECT 1 FROM branches
        WHERE id = NEW.branch_id
          AND conversation_id = NEW.conversation_id
    )
) OR (
    NEW.anchor_message_id IS NOT NULL
    AND NOT EXISTS (
        SELECT 1 FROM messages
        WHERE id = NEW.anchor_message_id
          AND conversation_id = NEW.conversation_id
    )
)
BEGIN
    SELECT RAISE(ABORT, 'summary references another conversation');
END;

CREATE TRIGGER summary_sources_conversation_guard
BEFORE INSERT ON summary_sources
WHEN NOT EXISTS (
    SELECT 1
    FROM summaries summary
    JOIN messages message ON message.id = NEW.message_id
    WHERE summary.id = NEW.summary_id
      AND summary.conversation_id = message.conversation_id
      AND (
          NEW.source_branch_id IS NULL
          OR EXISTS (
              SELECT 1 FROM branches branch
              WHERE branch.id = NEW.source_branch_id
                AND branch.conversation_id = summary.conversation_id
          )
      )
)
BEGIN
    SELECT RAISE(ABORT, 'summary source must belong to the same conversation');
END;

CREATE TRIGGER summary_claims_conversation_guard
BEFORE INSERT ON summary_claims
WHEN NOT EXISTS (
    SELECT 1
    FROM summaries summary
    JOIN messages message ON message.id = NEW.source_message_id
    WHERE summary.id = NEW.summary_id
      AND summary.conversation_id = message.conversation_id
)
BEGIN
    SELECT RAISE(ABORT, 'summary claim source must belong to the same conversation');
END;

CREATE TRIGGER summaries_orphan_after_source_delete
AFTER DELETE ON summary_sources
WHEN NOT EXISTS (
    SELECT 1 FROM summary_sources WHERE summary_id = OLD.summary_id
)
BEGIN
    UPDATE summaries
    SET status = 'orphaned', updated_at = datetime('now')
    WHERE id = OLD.summary_id;
END;

CREATE TABLE merge_operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    target_branch_id INTEGER,
    source_branch_id INTEGER,
    result_branch_id INTEGER,
    base_message_id INTEGER,
    snapshot_summary_id INTEGER,
    target_branch_name TEXT NOT NULL,
    source_branch_name TEXT NOT NULL,
    result_branch_name TEXT,
    version INTEGER NOT NULL CHECK(version > 0),
    preview_token TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'completed'
        CHECK(status IN ('completed', 'rolled_back')),
    resolution_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    rolled_back_at TEXT,
    rollback_reason TEXT,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
    FOREIGN KEY(target_branch_id) REFERENCES branches(id) ON DELETE SET NULL,
    FOREIGN KEY(source_branch_id) REFERENCES branches(id) ON DELETE SET NULL,
    FOREIGN KEY(result_branch_id) REFERENCES branches(id) ON DELETE SET NULL,
    FOREIGN KEY(base_message_id) REFERENCES messages(id) ON DELETE SET NULL,
    FOREIGN KEY(snapshot_summary_id) REFERENCES summaries(id) ON DELETE SET NULL
);

CREATE TABLE merge_operation_summaries (
    merge_id INTEGER NOT NULL,
    summary_id INTEGER NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('target', 'source', 'derived')),
    source_order INTEGER NOT NULL DEFAULT 0 CHECK(source_order >= 0),
    PRIMARY KEY(merge_id, summary_id, side),
    FOREIGN KEY(merge_id) REFERENCES merge_operations(id) ON DELETE CASCADE,
    FOREIGN KEY(summary_id) REFERENCES summaries(id) ON DELETE CASCADE
);

CREATE TABLE merge_conflicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    merge_id INTEGER NOT NULL,
    conflict_key TEXT NOT NULL,
    subject TEXT NOT NULL,
    target_values_json TEXT NOT NULL,
    source_values_json TEXT NOT NULL,
    target_source_ids_json TEXT NOT NULL,
    source_source_ids_json TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'medium'
        CHECK(severity IN ('low', 'medium', 'high')),
    status TEXT NOT NULL DEFAULT 'resolved'
        CHECK(status IN ('resolved', 'ignored')),
    resolution_value TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    UNIQUE(merge_id, conflict_key),
    FOREIGN KEY(merge_id) REFERENCES merge_operations(id) ON DELETE CASCADE
);

CREATE TABLE merge_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    merge_id INTEGER NOT NULL,
    event_type TEXT NOT NULL CHECK(event_type IN ('created', 'rollback')),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(merge_id) REFERENCES merge_operations(id) ON DELETE CASCADE
);

CREATE INDEX idx_merge_operations_conversation
    ON merge_operations(conversation_id, created_at DESC);
CREATE INDEX idx_merge_operations_result_branch
    ON merge_operations(result_branch_id, status);
CREATE INDEX idx_merge_operation_summaries_summary
    ON merge_operation_summaries(summary_id);
CREATE INDEX idx_merge_conflicts_merge
    ON merge_conflicts(merge_id, conflict_key);
CREATE INDEX idx_merge_events_merge
    ON merge_events(merge_id, created_at);

CREATE TRIGGER merge_operation_conversation_guard
BEFORE INSERT ON merge_operations
WHEN (
    NEW.target_branch_id IS NOT NULL
    AND NOT EXISTS (
        SELECT 1 FROM branches
        WHERE id = NEW.target_branch_id
          AND conversation_id = NEW.conversation_id
    )
) OR (
    NEW.source_branch_id IS NOT NULL
    AND NOT EXISTS (
        SELECT 1 FROM branches
        WHERE id = NEW.source_branch_id
          AND conversation_id = NEW.conversation_id
    )
) OR (
    NEW.result_branch_id IS NOT NULL
    AND NOT EXISTS (
        SELECT 1 FROM branches
        WHERE id = NEW.result_branch_id
          AND conversation_id = NEW.conversation_id
    )
)
BEGIN
    SELECT RAISE(ABORT, 'merge branches must belong to the same conversation');
END;

