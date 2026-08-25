from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional


class RecordNotFoundError(LookupError):
    def __init__(self, resource: str, record_id: int) -> None:
        self.resource = resource
        self.record_id = record_id
        super().__init__(f"{resource} {record_id} not found")


class InvalidParentError(ValueError):
    pass


class BranchOperationError(ValueError):
    pass


class ProtectedBranchError(BranchOperationError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def row_to_message(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        "parent_id": row["parent_id"],
        "role": row["role"],
        "content": row["content"],
        "prompt_tokens": row["prompt_tokens"],
        "response_tokens": row["response_tokens"],
        "created_at": row["created_at"],
    }


def row_to_branch(row: sqlite3.Row, path_message_ids: list[int] | None = None) -> dict:
    return {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        "name": row["name"],
        "forked_from_message_id": row["forked_from_message_id"],
        "root_message_id": row["root_message_id"],
        "head_message_id": row["head_message_id"],
        "is_main": bool(row["is_main"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "path_message_ids": path_message_ids or [],
    }


def get_conversation(connection: sqlite3.Connection, conversation_id: int) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
    ).fetchone()
    if row is None:
        raise RecordNotFoundError("Conversation", conversation_id)
    return row


def get_message(connection: sqlite3.Connection, message_id: int) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM messages WHERE id = ?", (message_id,)
    ).fetchone()
    if row is None:
        raise RecordNotFoundError("Message", message_id)
    return row


def get_branch(connection: sqlite3.Connection, branch_id: int) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM branches WHERE id = ?", (branch_id,)
    ).fetchone()
    if row is None:
        raise RecordNotFoundError("Branch", branch_id)
    return row


def validate_parent(
    connection: sqlite3.Connection,
    conversation_id: int,
    parent_id: Optional[int],
) -> Optional[sqlite3.Row]:
    if parent_id is None:
        return None
    parent = get_message(connection, parent_id)
    if parent["conversation_id"] != conversation_id:
        raise InvalidParentError("parent_id belongs to another conversation")
    return parent


def list_conversations(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute(
        """
        SELECT c.*,
               (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id)
               AS message_count,
               (SELECT COUNT(*) FROM branches b WHERE b.conversation_id = c.id)
               AS branch_count,
               (SELECT b.name FROM branches b WHERE b.id = c.active_branch_id)
               AS active_branch_name
        FROM conversations c
        ORDER BY c.id DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def list_messages(connection: sqlite3.Connection, conversation_id: int) -> list[dict]:
    rows = connection.execute(
        """
        SELECT *
        FROM messages
        WHERE conversation_id = ?
        ORDER BY id ASC
        """,
        (conversation_id,),
    ).fetchall()
    return [row_to_message(row) for row in rows]


def ancestor_ids(connection: sqlite3.Connection, leaf_id: Optional[int]) -> list[int]:
    if leaf_id is None:
        return []
    path = []
    seen = set()
    current_id = leaf_id
    while current_id is not None:
        if current_id in seen:
            raise BranchOperationError("Cycle detected in message tree")
        seen.add(current_id)
        row = get_message(connection, current_id)
        path.append(row["id"])
        current_id = row["parent_id"]
    path.reverse()
    return path


def branch_path_ids(connection: sqlite3.Connection, branch: sqlite3.Row) -> list[int]:
    leaf_id = branch["head_message_id"] or branch["forked_from_message_id"]
    return ancestor_ids(connection, leaf_id)


def list_branches(connection: sqlite3.Connection, conversation_id: int) -> list[dict]:
    rows = connection.execute(
        """
        SELECT * FROM branches
        WHERE conversation_id = ?
        ORDER BY is_main DESC, id ASC
        """,
        (conversation_id,),
    ).fetchall()
    return [row_to_branch(row, branch_path_ids(connection, row)) for row in rows]


def create_conversation(
    connection: sqlite3.Connection,
    title: str,
    system_prompt: str,
    model: str,
    token_budget: int = 8192,
) -> sqlite3.Row:
    cursor = connection.execute(
        """
        INSERT INTO conversations (
            title, system_prompt, model, token_budget, created_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (title, system_prompt, model, token_budget, utc_now()),
    )
    conversation_id = cursor.lastrowid
    timestamp = utc_now()
    branch_cursor = connection.execute(
        """
        INSERT INTO branches (
            conversation_id, name, is_main, created_at, updated_at
        )
        VALUES (?, 'Main', 1, ?, ?)
        """,
        (conversation_id, timestamp, timestamp),
    )
    connection.execute(
        "UPDATE conversations SET active_branch_id = ? WHERE id = ?",
        (branch_cursor.lastrowid, conversation_id),
    )
    return get_conversation(connection, conversation_id)


def update_conversation_model(
    connection: sqlite3.Connection,
    conversation_id: int,
    model: str,
) -> None:
    connection.execute(
        "UPDATE conversations SET model = ? WHERE id = ?",
        (model, conversation_id),
    )


def update_conversation_settings(
    connection: sqlite3.Connection,
    conversation_id: int,
    *,
    title: Optional[str] = None,
    token_budget: Optional[int] = None,
) -> sqlite3.Row:
    get_conversation(connection, conversation_id)
    if title is not None:
        connection.execute(
            "UPDATE conversations SET title = ? WHERE id = ?",
            (title, conversation_id),
        )
    if token_budget is not None:
        connection.execute(
            "UPDATE conversations SET token_budget = ? WHERE id = ?",
            (token_budget, conversation_id),
        )
    return get_conversation(connection, conversation_id)


def set_active_state(
    connection: sqlite3.Connection,
    conversation_id: int,
    branch_id: int,
    message_id: Optional[int],
) -> sqlite3.Row:
    get_conversation(connection, conversation_id)
    branch = get_branch(connection, branch_id)
    if branch["conversation_id"] != conversation_id:
        raise BranchOperationError("Branch belongs to another conversation")
    if message_id is not None:
        message = get_message(connection, message_id)
        if message["conversation_id"] != conversation_id:
            raise BranchOperationError("Message belongs to another conversation")
        if message_id not in branch_path_ids(connection, branch):
            raise BranchOperationError("Message is not visible on the selected branch")
    connection.execute(
        """
        UPDATE conversations
        SET active_branch_id = ?, active_message_id = ?
        WHERE id = ?
        """,
        (branch_id, message_id, conversation_id),
    )
    return get_conversation(connection, conversation_id)


def _next_branch_name(connection: sqlite3.Connection, conversation_id: int) -> str:
    number = 2
    while True:
        name = f"Branch {number}"
        exists = connection.execute(
            """
            SELECT 1 FROM branches
            WHERE conversation_id = ? AND name = ? COLLATE NOCASE
            """,
            (conversation_id, name),
        ).fetchone()
        if exists is None:
            return name
        number += 1


def _validate_branch_name(
    connection: sqlite3.Connection,
    conversation_id: int,
    name: str,
    *,
    exclude_branch_id: Optional[int] = None,
) -> str:
    branch_name = name.strip()
    if not branch_name:
        raise BranchOperationError("Branch name cannot be empty")
    row = connection.execute(
        """
        SELECT id FROM branches
        WHERE conversation_id = ?
          AND name = ? COLLATE NOCASE
          AND (? IS NULL OR id != ?)
        """,
        (conversation_id, branch_name, exclude_branch_id, exclude_branch_id),
    ).fetchone()
    if row is not None:
        raise BranchOperationError(f'Branch name "{branch_name}" already exists')
    return branch_name


def create_branch(
    connection: sqlite3.Connection,
    conversation_id: int,
    forked_from_message_id: Optional[int],
    name: Optional[str] = None,
) -> sqlite3.Row:
    get_conversation(connection, conversation_id)
    if forked_from_message_id is not None:
        message = get_message(connection, forked_from_message_id)
        if message["conversation_id"] != conversation_id:
            raise BranchOperationError("Fork anchor belongs to another conversation")
    branch_name = _validate_branch_name(
        connection,
        conversation_id,
        name or _next_branch_name(connection, conversation_id),
    )
    timestamp = utc_now()
    cursor = connection.execute(
        """
        INSERT INTO branches (
            conversation_id, name, forked_from_message_id,
            is_main, created_at, updated_at
        )
        VALUES (?, ?, ?, 0, ?, ?)
        """,
        (
            conversation_id,
            branch_name,
            forked_from_message_id,
            timestamp,
            timestamp,
        ),
    )
    branch = get_branch(connection, cursor.lastrowid)
    connection.execute(
        """
        UPDATE conversations
        SET active_branch_id = ?, active_message_id = ?
        WHERE id = ?
        """,
        (branch["id"], forked_from_message_id, conversation_id),
    )
    return branch


def create_merge_branch(
    connection: sqlite3.Connection,
    conversation_id: int,
    target_branch: sqlite3.Row,
    base_message_id: Optional[int],
    head_message_id: Optional[int],
    name: str,
) -> sqlite3.Row:
    conversation = get_conversation(connection, conversation_id)
    if target_branch["conversation_id"] != conversation["id"]:
        raise BranchOperationError("Target branch belongs to another conversation")
    if base_message_id is not None:
        validate_parent(connection, conversation_id, base_message_id)
    if head_message_id is not None:
        validate_parent(connection, conversation_id, head_message_id)

    target_path = ancestor_ids(connection, head_message_id)
    if base_message_id is not None and base_message_id not in target_path:
        raise BranchOperationError("Merge base is not on the target branch")

    branch_name = _validate_branch_name(connection, conversation_id, name)
    timestamp = utc_now()
    cursor = connection.execute(
        """
        INSERT INTO branches (
            conversation_id, name, forked_from_message_id,
            root_message_id, head_message_id, is_main, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, 0, ?, ?)
        """,
        (
            conversation_id,
            branch_name,
            base_message_id,
            None,
            head_message_id,
            timestamp,
            timestamp,
        ),
    )
    return get_branch(connection, cursor.lastrowid)


def rename_branch(
    connection: sqlite3.Connection,
    branch_id: int,
    name: str,
) -> sqlite3.Row:
    branch = get_branch(connection, branch_id)
    branch_name = _validate_branch_name(
        connection,
        branch["conversation_id"],
        name,
        exclude_branch_id=branch_id,
    )
    connection.execute(
        "UPDATE branches SET name = ?, updated_at = ? WHERE id = ?",
        (branch_name, utc_now(), branch_id),
    )
    return get_branch(connection, branch["id"])


def expected_branch_parent(branch: sqlite3.Row) -> Optional[int]:
    return branch["head_message_id"] or branch["forked_from_message_id"]


def find_branch_for_parent(
    connection: sqlite3.Connection,
    conversation: sqlite3.Row,
    parent_id: Optional[int],
) -> Optional[sqlite3.Row]:
    rows = connection.execute(
        """
        SELECT * FROM branches
        WHERE conversation_id = ?
        ORDER BY CASE WHEN id = ? THEN 0 ELSE 1 END, is_main DESC, id ASC
        """,
        (conversation["id"], conversation["active_branch_id"]),
    ).fetchall()
    return next(
        (row for row in rows if expected_branch_parent(row) == parent_id),
        None,
    )


def validate_branch_parent(
    connection: sqlite3.Connection,
    branch: sqlite3.Row,
    parent_id: Optional[int],
) -> None:
    expected_parent = expected_branch_parent(branch)
    if parent_id != expected_parent:
        raise InvalidParentError(
            f"Branch expects parent_id {expected_parent}, received {parent_id}"
        )
    validate_parent(connection, branch["conversation_id"], parent_id)


def advance_branch(
    connection: sqlite3.Connection,
    branch_id: int,
    user_message_id: int,
    assistant_message_id: int,
) -> sqlite3.Row:
    branch = get_branch(connection, branch_id)
    root_message_id = branch["root_message_id"] or user_message_id
    connection.execute(
        """
        UPDATE branches
        SET root_message_id = ?, head_message_id = ?, updated_at = ?
        WHERE id = ?
        """,
        (root_message_id, assistant_message_id, utc_now(), branch_id),
    )
    connection.execute(
        """
        UPDATE conversations
        SET active_branch_id = ?, active_message_id = ?
        WHERE id = ?
        """,
        (branch_id, assistant_message_id, branch["conversation_id"]),
    )
    return get_branch(connection, branch_id)


def delete_branch_tree(connection: sqlite3.Connection, branch_id: int) -> dict:
    branch = get_branch(connection, branch_id)
    if branch["is_main"]:
        raise ProtectedBranchError("The Main branch cannot be deleted")

    root_message_id = branch["root_message_id"]
    subtree_ids = []
    if root_message_id is not None:
        subtree_ids = [
            row["id"]
            for row in connection.execute(
                """
                WITH RECURSIVE subtree(id) AS (
                    SELECT id FROM messages WHERE id = ?
                    UNION ALL
                    SELECT messages.id
                    FROM messages
                    JOIN subtree ON messages.parent_id = subtree.id
                )
                SELECT id FROM subtree
                """,
                (root_message_id,),
            ).fetchall()
        ]

    affected_branch_ids = {branch_id}
    if subtree_ids:
        subtree_set = set(subtree_ids)
        branch_rows = connection.execute(
            "SELECT * FROM branches WHERE conversation_id = ?",
            (branch["conversation_id"],),
        ).fetchall()
        for row in branch_rows:
            if any(
                message_id in subtree_set
                for message_id in (
                    row["forked_from_message_id"],
                    row["root_message_id"],
                    row["head_message_id"],
                )
                if message_id is not None
            ):
                affected_branch_ids.add(row["id"])

    main_branch = connection.execute(
        """
        SELECT * FROM branches
        WHERE conversation_id = ? AND is_main = 1
        """,
        (branch["conversation_id"],),
    ).fetchone()
    if main_branch["id"] in affected_branch_ids:
        raise ProtectedBranchError(
            "This branch shares messages with Main and cannot be deleted safely"
        )
    connection.execute(
        """
        UPDATE conversations
        SET active_branch_id = ?, active_message_id = ?
        WHERE id = ?
        """,
        (
            main_branch["id"],
            main_branch["head_message_id"],
            branch["conversation_id"],
        ),
    )

    placeholders = ",".join("?" for _ in affected_branch_ids)
    connection.execute(
        f"DELETE FROM branches WHERE id IN ({placeholders})",
        tuple(sorted(affected_branch_ids)),
    )
    if root_message_id is not None:
        connection.execute("DELETE FROM messages WHERE id = ?", (root_message_id,))

    return {
        "deleted_branch_ids": sorted(affected_branch_ids),
        "deleted_message_count": len(subtree_ids),
        "active_branch_id": main_branch["id"],
        "active_message_id": main_branch["head_message_id"],
    }


def bootstrap_branches(connection: sqlite3.Connection) -> None:
    conversations = connection.execute(
        "SELECT * FROM conversations ORDER BY id"
    ).fetchall()
    for conversation in conversations:
        existing = connection.execute(
            "SELECT 1 FROM branches WHERE conversation_id = ? LIMIT 1",
            (conversation["id"],),
        ).fetchone()
        if existing is not None:
            continue

        leaves = connection.execute(
            """
            SELECT message.id
            FROM messages message
            LEFT JOIN messages child ON child.parent_id = message.id
            WHERE message.conversation_id = ?
            GROUP BY message.id
            HAVING COUNT(child.id) = 0
            ORDER BY message.id DESC
            """,
            (conversation["id"],),
        ).fetchall()
        leaf_ids = [row["id"] for row in leaves]
        active_leaf_id = conversation["active_message_id"]
        if active_leaf_id not in leaf_ids:
            active_leaf_id = leaf_ids[0] if leaf_ids else None
        active_path = ancestor_ids(connection, active_leaf_id)
        timestamp = utc_now()
        main_cursor = connection.execute(
            """
            INSERT INTO branches (
                conversation_id, name, root_message_id, head_message_id,
                is_main, created_at, updated_at
            )
            VALUES (?, 'Main', ?, ?, 1, ?, ?)
            """,
            (
                conversation["id"],
                active_path[0] if active_path else None,
                active_leaf_id,
                timestamp,
                timestamp,
            ),
        )
        main_branch_id = main_cursor.lastrowid

        imported_index = 1
        for leaf_id in leaf_ids:
            if leaf_id == active_leaf_id:
                continue
            path = ancestor_ids(connection, leaf_id)
            common_length = 0
            for active_id, candidate_id in zip(active_path, path):
                if active_id != candidate_id:
                    break
                common_length += 1
            forked_from = path[common_length - 1] if common_length else None
            root_message_id = path[common_length] if common_length < len(path) else None
            connection.execute(
                """
                INSERT INTO branches (
                    conversation_id, name, forked_from_message_id,
                    root_message_id, head_message_id, is_main,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    conversation["id"],
                    f"Imported branch {imported_index}",
                    forked_from,
                    root_message_id,
                    leaf_id,
                    timestamp,
                    timestamp,
                ),
            )
            imported_index += 1

        connection.execute(
            """
            UPDATE conversations
            SET active_branch_id = ?, active_message_id = ?
            WHERE id = ?
            """,
            (main_branch_id, active_leaf_id, conversation["id"]),
        )


def insert_message(
    connection: sqlite3.Connection,
    conversation_id: int,
    parent_id: Optional[int],
    role: str,
    content: str,
    prompt_tokens: Optional[int] = None,
    response_tokens: Optional[int] = None,
) -> sqlite3.Row:
    cursor = connection.execute(
        """
        INSERT INTO messages (
            conversation_id, parent_id, role, content,
            prompt_tokens, response_tokens, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            conversation_id,
            parent_id,
            role,
            content,
            prompt_tokens,
            response_tokens,
            utc_now(),
        ),
    )
    return get_message(connection, cursor.lastrowid)


def delete_conversation(connection: sqlite3.Connection, conversation_id: int) -> None:
    get_conversation(connection, conversation_id)
    connection.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
