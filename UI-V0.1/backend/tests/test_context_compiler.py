from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.context_compiler import (
    ContextBudgetError,
    compile_context,
    compile_messages,
    context_diff,
)
from app.database import Database
from app.repository import create_conversation, insert_message


BACKEND_DIR = Path(__file__).resolve().parents[1]


class ContextCompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        temporary_path = Path(self.temporary_directory.name)
        self.database = Database(
            temporary_path / "context.db",
            BACKEND_DIR / "migrations",
            temporary_path / "backups",
            backup_before_migrate=False,
        )
        self.assertEqual(self.database.migrate(), [1, 2, 3, 4, 5, 6, 7])

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def build_fork(self):
        with self.database.transaction() as connection:
            conversation = create_conversation(
                connection,
                "Context test",
                "system prompt",
                "fake-model",
            )
            root = insert_message(connection, conversation["id"], None, "user", "root")
            shared_reply = insert_message(
                connection, conversation["id"], root["id"], "assistant", "shared"
            )
            branch_a = insert_message(
                connection, conversation["id"], shared_reply["id"], "user", "A"
            )
            branch_a_reply = insert_message(
                connection, conversation["id"], branch_a["id"], "assistant", "A reply"
            )
            branch_b = insert_message(
                connection, conversation["id"], shared_reply["id"], "user", "B"
            )
            branch_b_reply = insert_message(
                connection, conversation["id"], branch_b["id"], "assistant", "B reply"
            )
        return (
            conversation,
            root,
            shared_reply,
            branch_a,
            branch_a_reply,
            branch_b,
            branch_b_reply,
        )

    def test_compiler_includes_only_active_ancestor_path(self) -> None:
        conversation, root, shared, branch_a, branch_a_reply, _, _ = self.build_fork()
        with self.database.connection() as connection:
            conversation = connection.execute(
                "SELECT * FROM conversations WHERE id = ?", (conversation["id"],)
            ).fetchone()
            compiled, active_path = compile_messages(
                connection, conversation, branch_a_reply["id"], "continue A"
            )

        self.assertEqual(
            [item["id"] for item in active_path],
            [root["id"], shared["id"], branch_a["id"], branch_a_reply["id"]],
        )
        compiled_content = [item["content"] for item in compiled]
        self.assertEqual(compiled_content[-1], "continue A")
        self.assertNotIn("B", compiled_content)

    def test_linear_branch_diff_exposes_sibling_contamination(self) -> None:
        conversation, _, _, branch_a, _, _, branch_b_reply = self.build_fork()
        with self.database.connection() as connection:
            conversation = connection.execute(
                "SELECT * FROM conversations WHERE id = ?", (conversation["id"],)
            ).fetchone()
            linear = compile_context(
                connection, conversation, branch_b_reply["id"], "question", strategy="linear"
            )
            branch = compile_context(
                connection, conversation, branch_b_reply["id"], "question", strategy="branch"
            )
        diff = context_diff(linear, branch)
        self.assertIn(branch_a["id"], diff["linear_only_message_ids"])
        self.assertNotIn(branch_a["id"], branch.included_message_ids)

    def test_budget_keeps_contiguous_recent_suffix(self) -> None:
        with self.database.transaction() as connection:
            conversation = create_conversation(
                connection, "Budget", "system", "fake-model", token_budget=256
            )
            parent_id = None
            rows = []
            for index in range(8):
                row = insert_message(
                    connection,
                    conversation["id"],
                    parent_id,
                    "user" if index % 2 == 0 else "assistant",
                    f"message-{index}-" + ("x" * 120),
                )
                rows.append(row)
                parent_id = row["id"]

        with self.database.connection() as connection:
            conversation = connection.execute(
                "SELECT * FROM conversations WHERE id = ?", (conversation["id"],)
            ).fetchone()
            compiled = compile_context(
                connection, conversation, parent_id, "continue", strategy="branch"
            )
        self.assertLessEqual(compiled.estimated_tokens, 256)
        self.assertTrue(compiled.truncated_message_ids)
        expected_suffix = [row["id"] for row in rows][-len(compiled.included_message_ids) :]
        self.assertEqual(compiled.included_message_ids, expected_suffix)

    def test_budget_rejects_system_and_user_that_cannot_fit(self) -> None:
        with self.database.transaction() as connection:
            conversation = create_conversation(
                connection, "Budget", "system", "fake-model", token_budget=256
            )
        with self.database.connection() as connection:
            conversation = connection.execute(
                "SELECT * FROM conversations WHERE id = ?", (conversation["id"],)
            ).fetchone()
            with self.assertRaises(ContextBudgetError):
                compile_context(connection, conversation, None, "x" * 2000)

    def test_database_rejects_parent_from_another_conversation(self) -> None:
        with self.database.transaction() as connection:
            first = create_conversation(connection, "First", "system", "fake-model")
            second = create_conversation(connection, "Second", "system", "fake-model")
            parent = insert_message(connection, first["id"], None, "user", "first")

        with self.assertRaises(sqlite3.IntegrityError):
            with self.database.transaction() as connection:
                insert_message(
                    connection, second["id"], parent["id"], "user", "invalid"
                )
