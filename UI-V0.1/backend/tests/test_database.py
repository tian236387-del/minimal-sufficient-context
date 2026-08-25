from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.database import Database
from app.repository import (
    bootstrap_branches,
    create_conversation,
    delete_conversation,
    insert_message,
    list_branches,
)


BACKEND_DIR = Path(__file__).resolve().parents[1]


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.temporary_path = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def make_database(self, backup_before_migrate: bool = False) -> Database:
        return Database(
            self.temporary_path / "database.db",
            BACKEND_DIR / "migrations",
            self.temporary_path / "backups",
            backup_before_migrate=backup_before_migrate,
        )

    def test_foreign_keys_are_enabled_and_delete_cascades(self) -> None:
        database = self.make_database()
        database.migrate()
        with database.transaction() as connection:
            conversation = create_conversation(
                connection, "Cascade", "system", "fake-model"
            )
            user = insert_message(
                connection, conversation["id"], None, "user", "hello"
            )
            insert_message(
                connection,
                conversation["id"],
                user["id"],
                "assistant",
                "reply",
            )

        with database.connection() as connection:
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)

        with database.transaction() as connection:
            delete_conversation(connection, conversation["id"])

        with database.connection() as connection:
            message_count = connection.execute(
                "SELECT COUNT(*) FROM messages"
            ).fetchone()[0]
        self.assertEqual(message_count, 0)

    def test_backup_is_consistent_and_preserves_data(self) -> None:
        database = self.make_database()
        database.migrate()
        with database.transaction() as connection:
            conversation = create_conversation(
                connection, "Backup", "system", "fake-model"
            )
            insert_message(
                connection, conversation["id"], None, "user", "persist me"
            )

        backup_path = database.backup_database()

        self.assertTrue(backup_path.exists())
        backup_connection = sqlite3.connect(backup_path)
        try:
            self.assertEqual(
                backup_connection.execute(
                    "SELECT COUNT(*) FROM conversations"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                backup_connection.execute("PRAGMA quick_check").fetchone()[0],
                "ok",
            )
        finally:
            backup_connection.close()

    def test_transaction_rolls_back_all_messages_when_second_insert_fails(self) -> None:
        database = self.make_database()
        database.migrate()
        with database.transaction() as connection:
            conversation = create_conversation(
                connection, "Atomic", "system", "fake-model"
            )

        with self.assertRaises(sqlite3.IntegrityError):
            with database.transaction() as connection:
                user = insert_message(
                    connection,
                    conversation["id"],
                    None,
                    "user",
                    "must roll back",
                )
                insert_message(
                    connection,
                    conversation["id"],
                    user["id"],
                    "invalid-role",
                    "constraint failure",
                )

        with database.connection() as connection:
            message_count = connection.execute(
                "SELECT COUNT(*) FROM messages WHERE conversation_id = ?",
                (conversation["id"],),
            ).fetchone()[0]
        self.assertEqual(message_count, 0)

    def test_legacy_database_is_backed_up_before_first_migration(self) -> None:
        database_path = self.temporary_path / "database.db"
        legacy_connection = sqlite3.connect(database_path)
        try:
            legacy_connection.executescript(
                (BACKEND_DIR / "migrations" / "0001_initial.sql").read_text(
                    encoding="utf-8"
                )
            )
            legacy_connection.execute(
                """
                INSERT INTO conversations (title, system_prompt, model, created_at)
                VALUES ('Legacy', 'system', 'fake-model', 'now')
                """
            )
            legacy_connection.commit()
        finally:
            legacy_connection.close()

        database = self.make_database(backup_before_migrate=True)
        self.assertEqual(database.migrate(), [1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(database.current_version(), 7)
        backups = list((self.temporary_path / "backups").glob("*.sqlite3"))
        self.assertEqual(len(backups), 1)

    def test_existing_message_tree_bootstraps_named_branches(self) -> None:
        database = self.make_database()
        database.migrate()
        with database.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT INTO conversations (title, system_prompt, model, created_at)
                VALUES ('Imported', 'system', 'fake-model', 'now')
                """
            )
            conversation_id = cursor.lastrowid
            root = insert_message(
                connection, conversation_id, None, "user", "shared root"
            )
            shared = insert_message(
                connection, conversation_id, root["id"], "assistant", "shared reply"
            )
            first_leaf = insert_message(
                connection, conversation_id, shared["id"], "user", "first leaf"
            )
            second_leaf = insert_message(
                connection, conversation_id, shared["id"], "user", "second leaf"
            )
            bootstrap_branches(connection)
            branches = list_branches(connection, conversation_id)

        self.assertEqual([branch["name"] for branch in branches], ["Main", "Imported branch 1"])
        self.assertEqual(branches[0]["head_message_id"], second_leaf["id"])
        self.assertEqual(branches[1]["head_message_id"], first_leaf["id"])
        self.assertEqual(branches[1]["forked_from_message_id"], shared["id"])
