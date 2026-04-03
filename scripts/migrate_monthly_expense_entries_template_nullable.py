#!/usr/bin/env python3
"""
One-off SQLite migration for smart_inventory.

Goal:
    Make monthly_expense_entries.template_id nullable, so old expense templates
    can be deleted without losing monthly expense history.

Usage:
    python scripts/migrate_monthly_expense_entries_template_nullable.py
    python scripts/migrate_monthly_expense_entries_template_nullable.py --db inventory.db
    python scripts/migrate_monthly_expense_entries_template_nullable.py --no-backup

This script:
    1. Optionally creates a timestamped backup of the SQLite file.
    2. Detects whether template_id is already nullable.
    3. Rebuilds monthly_expense_entries with template_id nullable.
    4. Copies all existing rows.
    5. Restores indexes.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Make monthly_expense_entries.template_id nullable in SQLite."
    )
    parser.add_argument(
        "--db",
        default="inventory.db",
        help="Path to SQLite database file. Default: inventory.db",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip automatic .db backup before migration.",
    )
    return parser.parse_args()


def fetch_table_sql(conn: sqlite3.Connection, table_name: str) -> str | None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row[0] if row and row[0] else None


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return bool(row)


def make_backup(db_path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.with_name(f"{db_path.stem}_backup_before_template_nullable_{timestamp}{db_path.suffix}")
    shutil.copy2(db_path, backup_path)
    return backup_path


def migrate(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()

    cur.execute("PRAGMA foreign_keys=OFF;")
    cur.execute("BEGIN TRANSACTION;")

    cur.execute("""
        CREATE TABLE monthly_expense_entries_new (
            id INTEGER NOT NULL PRIMARY KEY,
            template_id INTEGER,
            location_point_id INTEGER NOT NULL,
            month_start DATE NOT NULL,
            amount FLOAT NOT NULL,
            is_paid BOOLEAN NOT NULL,
            assigned_employee_user_id INTEGER,
            apply_to_employee_salary BOOLEAN NOT NULL,
            updated_by_user_id INTEGER,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            custom_name VARCHAR(255),
            comment TEXT,
            created_by_user_id INTEGER,
            FOREIGN KEY(template_id) REFERENCES expense_templates (id) ON DELETE CASCADE,
            FOREIGN KEY(location_point_id) REFERENCES location_points (id) ON DELETE CASCADE,
            FOREIGN KEY(assigned_employee_user_id) REFERENCES users (id) ON DELETE SET NULL,
            FOREIGN KEY(updated_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
            FOREIGN KEY(created_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
            CONSTRAINT uq_monthly_expense_template_month UNIQUE (template_id, month_start)
        );
    """)

    cur.execute("""
        INSERT INTO monthly_expense_entries_new (
            id,
            template_id,
            location_point_id,
            month_start,
            amount,
            is_paid,
            assigned_employee_user_id,
            apply_to_employee_salary,
            updated_by_user_id,
            created_at,
            updated_at,
            custom_name,
            comment,
            created_by_user_id
        )
        SELECT
            id,
            template_id,
            location_point_id,
            month_start,
            amount,
            is_paid,
            assigned_employee_user_id,
            apply_to_employee_salary,
            updated_by_user_id,
            created_at,
            updated_at,
            custom_name,
            comment,
            created_by_user_id
        FROM monthly_expense_entries;
    """)

    cur.execute("DROP TABLE monthly_expense_entries;")
    cur.execute("ALTER TABLE monthly_expense_entries_new RENAME TO monthly_expense_entries;")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS ix_monthly_expense_entries_template_id "
        "ON monthly_expense_entries(template_id);"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS ix_monthly_expense_entries_location_point_id "
        "ON monthly_expense_entries(location_point_id);"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS ix_monthly_expense_entries_month_start "
        "ON monthly_expense_entries(month_start);"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS ix_monthly_expense_entries_assigned_employee_user_id "
        "ON monthly_expense_entries(assigned_employee_user_id);"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS ix_monthly_expense_entries_updated_by_user_id "
        "ON monthly_expense_entries(updated_by_user_id);"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS ix_monthly_expense_entries_created_by_user_id "
        "ON monthly_expense_entries(created_by_user_id);"
    )

    cur.execute("COMMIT;")
    cur.execute("PRAGMA foreign_keys=ON;")

    fk_issues = cur.execute("PRAGMA foreign_key_check;").fetchall()
    if fk_issues:
        raise RuntimeError(f"Foreign key check failed after migration: {fk_issues}")


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)

    if not db_path.exists():
        print(f"ERROR: Database file not found: {db_path}")
        return 1

    if not db_path.is_file():
        print(f"ERROR: Not a file: {db_path}")
        return 1

    if not args.no_backup:
        backup_path = make_backup(db_path)
        print(f"Backup created: {backup_path}")

    conn = sqlite3.connect(db_path)
    try:
        if not table_exists(conn, "monthly_expense_entries"):
            print("ERROR: Table monthly_expense_entries not found.")
            return 1

        table_sql = fetch_table_sql(conn, "monthly_expense_entries")
        if not table_sql:
            print("ERROR: Could not read current CREATE TABLE statement.")
            return 1

        normalized = " ".join(table_sql.upper().split())
        if "TEMPLATE_ID INTEGER NOT NULL" not in normalized:
            print("No migration needed: template_id is already nullable or schema differs from the old version.")
            print("Current table SQL:")
            print(table_sql)
            return 0

        migrate(conn)

        new_sql = fetch_table_sql(conn, "monthly_expense_entries")
        print("Migration completed successfully.")
        print("New table SQL:")
        print(new_sql)
        return 0

    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"ERROR: Migration failed: {exc}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
