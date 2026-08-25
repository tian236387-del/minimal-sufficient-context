from __future__ import annotations

import argparse
from pathlib import Path

from .config import Settings
from .database import Database


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a consistent SQLite backup")
    parser.add_argument(
        "--destination",
        type=Path,
        help="Optional backup file path; defaults to MSC_BACKUP_PATH",
    )
    args = parser.parse_args()

    settings = Settings.from_env()
    database = Database(
        settings.database_path,
        settings.migrations_path,
        settings.backup_path,
        settings.backup_before_migrate,
    )
    destination = database.backup_database(args.destination)
    print(destination)


if __name__ == "__main__":
    main()

