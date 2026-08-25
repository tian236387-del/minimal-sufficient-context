DROP TRIGGER summaries_orphan_after_source_delete;

CREATE TRIGGER summaries_orphan_before_source_delete
BEFORE DELETE ON summary_sources
BEGIN
    UPDATE summaries
    SET status = 'orphaned', updated_at = datetime('now')
    WHERE id = OLD.summary_id;
END;

CREATE TRIGGER merge_operation_branch_update_guard
BEFORE UPDATE OF conversation_id, target_branch_id, source_branch_id, result_branch_id
ON merge_operations
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
    SELECT RAISE(ABORT, 'merge branch update crosses conversation boundary');
END;
