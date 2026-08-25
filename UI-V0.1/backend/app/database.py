from __future__ import annotations

import hashlib
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


MIGRATION_FILENAME = re.compile(r"^(?P<version>\d{4})_(?P<name>.+)\.sql$")


class DatabaseError(RuntimeError):
    pass


class MigrationError(DatabaseError):
    pass


class BackupError(DatabaseError):
    pass


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    path: Path
    checksum: str


class Database:
    def __init__(
        self,
        path: Path,
        migrations_path: Path,
        backup_path: Path,
        backup_before_migrate: bool = True,
    ) -> None:
        self.path = Path(path)
        self.migrations_path = Path(migrations_path)
        self.backup_path = Path(backup_path)
        self.backup_before_migrate = backup_before_migrate

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA synchronous = NORMAL")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            connection.close()
            raise DatabaseError("SQLite foreign key enforcement could not be enabled")
        return connection

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def migrate(self) -> list[int]:
        database_existed = self.path.exists() and self.path.stat().st_size > 0

        with self.connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL").fetchone()

        with self.transaction() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )

        migrations = self._load_migrations()
        with self.connection() as connection:
            applied_rows = connection.execute(
                "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
            ).fetchall()
            application_tables_exist = self._application_tables_exist(connection)

        migration_by_version = {migration.version: migration for migration in migrations}
        for row in applied_rows:
            migration = migration_by_version.get(row["version"])
            if migration is None:
                raise MigrationError(
                    f"Applied migration {row['version']:04d} has no matching file"
                )
            if row["checksum"] != migration.checksum:
                raise MigrationError(
                    f"Migration {row['version']:04d}_{row['name']} was modified after apply"
                )

        applied_versions = {row["version"] for row in applied_rows}
        pending = [
            migration
            for migration in migrations
            if migration.version not in applied_versions
        ]

        if (
            pending
            and database_existed
            and application_tables_exist
            and self.backup_before_migrate
        ):
            self.backup_database(prefix=f"pre-migration-v{pending[0].version:04d}")

        applied_now = []
        for migration in pending:
            self._apply_migration(migration)
            applied_now.append(migration.version)

        self.verify_integrity()
        return applied_now

    def current_version(self) -> int:
        with self.connection() as connection:
            table_exists = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'schema_migrations'
                """
            ).fetchone()
            if not table_exists:
                return 0
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
            ).fetchone()
            return int(row["version"])

    def verify_integrity(self) -> None:
        with self.connection() as connection:
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                details = ", ".join(
                    f"{row[0]} rowid={row[1]} parent={row[2]}" for row in violations[:5]
                )
                raise MigrationError(f"Foreign key violations detected: {details}")

            result = connection.execute("PRAGMA quick_check").fetchone()[0]
            if result != "ok":
                raise MigrationError(f"SQLite quick_check failed: {result}")

    def backup_database(
        self,
        destination: Path | None = None,
        prefix: str = "msc-chat",
    ) -> Path:
        if not self.path.exists():
            raise BackupError(f"Database does not exist: {self.path}")

        if destination is None:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
            destination = self.backup_path / f"{prefix}-{timestamp}.sqlite3"
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)

        if destination.resolve() == self.path.resolve():
            raise BackupError("Backup destination must differ from the source database")
        if destination.exists():
            raise BackupError(f"Backup destination already exists: {destination}")

        source = None
        target = None
        try:
            source = self.connect()
            target = sqlite3.connect(destination)
            source.backup(target)
            check = target.execute("PRAGMA quick_check").fetchone()[0]
            if check != "ok":
                raise BackupError(f"Backup quick_check failed: {check}")
            target.commit()
        except Exception as error:
            if target is not None:
                target.close()
                target = None
            if source is not None:
                source.close()
                source = None
            if destination.exists():
                destination.unlink()
            if isinstance(error, BackupError):
                raise
            raise BackupError(f"Database backup failed: {error}") from error
        finally:
            if target is not None:
                target.close()
            if source is not None:
                source.close()

        return destination

    def _load_migrations(self) -> list[Migration]:
        if not self.migrations_path.is_dir():
            raise MigrationError(
                f"Migrations directory does not exist: {self.migrations_path}"
            )

        migrations = []
        seen_versions = set()
        for path in sorted(self.migrations_path.glob("*.sql")):
            match = MIGRATION_FILENAME.match(path.name)
            if not match:
                raise MigrationError(f"Invalid migration filename: {path.name}")
            version = int(match.group("version"))
            if version in seen_versions:
                raise MigrationError(f"Duplicate migration version: {version:04d}")
            seen_versions.add(version)
            migrations.append(
                Migration(
                    version=version,
                    name=match.group("name"),
                    path=path,
                    checksum=hashlib.sha256(path.read_bytes()).hexdigest(),
                )
            )
        return migrations

    def _apply_migration(self, migration: Migration) -> None:
        sql = migration.path.read_text(encoding="utf-8")
        with self.transaction() as connection:
            for statement in self._split_sql_statements(sql):
                connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations (version, name, checksum, applied_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    migration.version,
                    migration.name,
                    migration.checksum,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    @staticmethod
    def _split_sql_statements(sql: str) -> list[str]:
        statements = []
        buffer = ""
        for line in sql.splitlines(keepends=True):
            buffer += line
            if sqlite3.complete_statement(buffer):
                statement = buffer.strip()
                if statement:
                    statements.append(statement)
                buffer = ""
        if buffer.strip():
            raise MigrationError("Migration contains an incomplete SQL statement")
        return statements

    @staticmethod
    def _application_tables_exist(connection: sqlite3.Connection) -> bool:
        rows = connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name IN ('conversations', 'messages')
            """
        ).fetchall()
        return bool(rows)
