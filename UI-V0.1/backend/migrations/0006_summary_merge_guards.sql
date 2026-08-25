CREATE TRIGGER summaries_conversation_update_guard
BEFORE UPDATE OF conversation_id, branch_id, anchor_message_id ON summaries
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
    SELECT RAISE(ABORT, 'summary update references another conversation');
END;

CREATE TRIGGER summary_sources_conversation_update_guard
BEFORE UPDATE OF summary_id, message_id, source_branch_id ON summary_sources
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
    SELECT RAISE(ABORT, 'summary source update crosses conversation boundary');
END;

CREATE TRIGGER summary_claims_conversation_update_guard
BEFORE UPDATE OF summary_id, source_message_id ON summary_claims
WHEN NOT EXISTS (
    SELECT 1
    FROM summaries summary
    JOIN messages message ON message.id = NEW.source_message_id
    WHERE summary.id = NEW.summary_id
      AND summary.conversation_id = message.conversation_id
)
BEGIN
    SELECT RAISE(ABORT, 'summary claim update crosses conversation boundary');
END;

CREATE TRIGGER merge_operation_state_guard_insert
BEFORE INSERT ON merge_operations
WHEN (
    NEW.base_message_id IS NOT NULL
    AND NOT EXISTS (
        SELECT 1 FROM messages
        WHERE id = NEW.base_message_id
          AND conversation_id = NEW.conversation_id
    )
) OR (
    NEW.snapshot_summary_id IS NOT NULL
    AND NOT EXISTS (
        SELECT 1 FROM summaries
        WHERE id = NEW.snapshot_summary_id
          AND conversation_id = NEW.conversation_id
    )
) OR (
    NEW.previous_active_branch_id IS NOT NULL
    AND NOT EXISTS (
        SELECT 1 FROM branches
        WHERE id = NEW.previous_active_branch_id
          AND conversation_id = NEW.conversation_id
    )
) OR (
    NEW.previous_active_message_id IS NOT NULL
    AND NOT EXISTS (
        SELECT 1 FROM messages
        WHERE id = NEW.previous_active_message_id
          AND conversation_id = NEW.conversation_id
    )
)
BEGIN
    SELECT RAISE(ABORT, 'merge state must belong to the same conversation');
END;

CREATE TRIGGER merge_operation_state_guard_update
BEFORE UPDATE OF conversation_id, base_message_id, snapshot_summary_id,
    previous_active_branch_id, previous_active_message_id ON merge_operations
WHEN (
    NEW.base_message_id IS NOT NULL
    AND NOT EXISTS (
        SELECT 1 FROM messages
        WHERE id = NEW.base_message_id
          AND conversation_id = NEW.conversation_id
    )
) OR (
    NEW.snapshot_summary_id IS NOT NULL
    AND NOT EXISTS (
        SELECT 1 FROM summaries
        WHERE id = NEW.snapshot_summary_id
          AND conversation_id = NEW.conversation_id
    )
) OR (
    NEW.previous_active_branch_id IS NOT NULL
    AND NOT EXISTS (
        SELECT 1 FROM branches
        WHERE id = NEW.previous_active_branch_id
          AND conversation_id = NEW.conversation_id
    )
) OR (
    NEW.previous_active_message_id IS NOT NULL
    AND NOT EXISTS (
        SELECT 1 FROM messages
        WHERE id = NEW.previous_active_message_id
          AND conversation_id = NEW.conversation_id
    )
)
BEGIN
    SELECT RAISE(ABORT, 'merge update crosses conversation boundary');
END;

CREATE TRIGGER merge_operation_summaries_conversation_guard
BEFORE INSERT ON merge_operation_summaries
WHEN NOT EXISTS (
    SELECT 1
    FROM merge_operations merge
    JOIN summaries summary ON summary.id = NEW.summary_id
    WHERE merge.id = NEW.merge_id
      AND merge.conversation_id = summary.conversation_id
)
BEGIN
    SELECT RAISE(ABORT, 'merge summary must belong to the same conversation');
END;

CREATE TRIGGER merge_operation_summaries_update_guard
BEFORE UPDATE OF merge_id, summary_id ON merge_operation_summaries
WHEN NOT EXISTS (
    SELECT 1
    FROM merge_operations merge
    JOIN summaries summary ON summary.id = NEW.summary_id
    WHERE merge.id = NEW.merge_id
      AND merge.conversation_id = summary.conversation_id
)
BEGIN
    SELECT RAISE(ABORT, 'merge summary update crosses conversation boundary');
END;

