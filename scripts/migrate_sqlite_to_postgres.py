from __future__ import annotations

import argparse
import asyncio
import sqlite3
from datetime import date, datetime
from pathlib import Path
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.database import Base  # noqa: E402
import app.models  # noqa: F401,E402

TABLES = [
    'users',
    'selection_cycles',
    'reports',
    'category_assignments',
    'check_results',
]
DATE_COLUMNS = {'birth_date', 'started_at', 'report_date'}
DATETIME_COLUMNS = {'created_at', 'updated_at', 'date_created', 'assigned_at'}
BOOL_COLUMNS = {'is_active'}


def convert_value(column: str, value):
    if value is None:
        return None
    if column in BOOL_COLUMNS:
        return bool(value)
    if column in DATE_COLUMNS and isinstance(value, str):
        return date.fromisoformat(value)
    if column in DATETIME_COLUMNS and isinstance(value, str):
        return datetime.fromisoformat(value)
    return value


def read_sqlite_rows(sqlite_path: Path, table: str) -> list[dict]:
    with sqlite3.connect(sqlite_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(f'SELECT * FROM {table}').fetchall()
    return [
        {key: convert_value(key, row[key]) for key in row.keys()}
        for row in rows
    ]


async def migrate(sqlite_path: Path, target_url: str, truncate_target: bool) -> None:
    engine = create_async_engine(target_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if truncate_target:
            await conn.execute(text('TRUNCATE TABLE check_results, category_assignments, reports, selection_cycles, users RESTART IDENTITY CASCADE'))

        for table in TABLES:
            rows = read_sqlite_rows(sqlite_path, table)
            if not rows:
                print(f'{table}: 0 rows')
                continue
            columns = list(rows[0].keys())
            query = text(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(':' + column for column in columns)})"
            )
            await conn.execute(query, rows)
            print(f'{table}: {len(rows)} rows')

    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description='Перенос данных из SQLite в PostgreSQL для smart_inventory.')
    parser.add_argument('--sqlite', default=str(PROJECT_ROOT / 'inventory.db'), help='Путь к исходной SQLite базе.')
    parser.add_argument('--target', required=True, help='Строка подключения PostgreSQL в формате postgresql+asyncpg://...')
    parser.add_argument('--truncate-target', action='store_true', help='Очистить целевую БД перед переносом.')
    args = parser.parse_args()

    asyncio.run(migrate(Path(args.sqlite), args.target, args.truncate_target))


if __name__ == '__main__':
    main()
