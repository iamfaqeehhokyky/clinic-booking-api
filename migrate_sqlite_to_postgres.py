"""Copy the clinic booking application's SQLite records into an empty PostgreSQL DB.

Run from the project root after setting DATABASE_URL to the Render EXTERNAL URL.
Example (PowerShell):
  $env:DATABASE_URL = "postgresql://..." 
  python migrate_sqlite_to_postgres.py "C:\\path\\to\\clinic.sqlite3"
"""

import argparse
import sqlite3
from pathlib import Path

from sqlalchemy import text

# Importing app creates the SQLAlchemy tables using DATABASE_URL.
from app import app
from database import db

TABLE_NAMES = ("slots", "bookings", "idempotency", "rate_attempts")


def main():
    parser = argparse.ArgumentParser(description="Migrate clinic SQLite data to PostgreSQL")
    parser.add_argument("sqlite_file", help="Path to the existing SQLite database file")
    args = parser.parse_args()

    sqlite_path = Path(args.sqlite_file).expanduser().resolve()
    if not sqlite_path.is_file():
        raise SystemExit(f"SQLite database file not found: {sqlite_path}")

    with app.app_context():
        if db.engine.dialect.name != "postgresql":
            raise SystemExit(
                "DATABASE_URL is not pointing to PostgreSQL. Set it to the Render "
                "EXTERNAL database URL before running this script."
            )

        with sqlite3.connect(str(sqlite_path)) as source:
            source.row_factory = sqlite3.Row
            source_tables = {
                row[0]
                for row in source.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }

            missing = [name for name in TABLE_NAMES if name not in source_tables]
            if missing:
                raise SystemExit(
                    "The SQLite database is missing expected table(s): "
                    + ", ".join(missing)
                    + ". Check that you selected the correct SQLite file."
                )

            # Read the source before opening the destination transaction.
            source_rows = {
                name: [dict(row) for row in source.execute(f'SELECT * FROM "{name}"')]
                for name in TABLE_NAMES
            }

        with db.engine.begin() as destination:
            # Avoid accidentally duplicating data or mixing it with live records.
            nonempty = []
            for name in TABLE_NAMES:
                count = destination.execute(
                    text(f'SELECT COUNT(*) FROM "{name}"')
                ).scalar_one()
                if count:
                    nonempty.append(f"{name} ({count})")
            if nonempty:
                raise SystemExit(
                    "Destination database is not empty: " + ", ".join(nonempty)
                    + ". Stop here; do not rerun into a populated database."
                )

            copied = {}
            for name in TABLE_NAMES:
                table = db.metadata.tables[name]
                expected_columns = {column.name for column in table.columns}
                rows = source_rows[name]

                for row in rows:
                    actual_columns = set(row)
                    if actual_columns != expected_columns:
                        raise SystemExit(
                            f"Column mismatch in '{name}'. SQLite has "
                            f"{sorted(actual_columns)}; SQLAlchemy expects "
                            f"{sorted(expected_columns)}. No data was committed."
                        )

                if rows:
                    destination.execute(table.insert(), rows)
                copied[name] = len(rows)

            # Explicitly inserted IDs do not advance PostgreSQL's serial sequence.
            destination.execute(text("""
                SELECT setval(
                    pg_get_serial_sequence('rate_attempts', 'id'),
                    COALESCE((SELECT MAX(id) FROM rate_attempts), 1),
                    EXISTS (SELECT 1 FROM rate_attempts)
                )
            """))

    print("Migration completed successfully.")
    for name, count in copied.items():
        print(f"  {name}: {count} row(s)")
    print("Keep your SQLite file as a backup until you verify the deployed app.")


if __name__ == "__main__":
    main()
