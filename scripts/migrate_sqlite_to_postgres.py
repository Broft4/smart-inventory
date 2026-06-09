#!/usr/bin/env python3
"""Перенос данных Smart Inventory / UCHETKA из SQLite в PostgreSQL.

Пример:
    PYTHONPATH=/opt/smart-inventory \
    python scripts/migrate_sqlite_to_postgres.py \
      --sqlite /opt/smart-inventory/inventory.db \
      --postgres postgresql+asyncpg://uchetka:PASS@127.0.0.1:5432/uchetka

Скрипт рассчитан на пустую PostgreSQL-БД. Если нужно пересоздать схему,
добавьте --drop-existing --yes.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, func, select, text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.database import Base  # noqa: E402
from app.logic import bootstrap_schema_and_admin  # noqa: E402
from app.payroll import bootstrap_payroll_schema  # noqa: E402


def _sqlite_path(value: str) -> str:
    raw = str(value or '').strip()
    if raw.startswith('sqlite+aiosqlite:///'):
        raw = raw[len('sqlite+aiosqlite:///'):]
    elif raw.startswith('sqlite:///'):
        raw = raw[len('sqlite:///'):]
    if raw.startswith('./'):
        raw = str(PROJECT_ROOT / raw[2:])
    return os.path.abspath(raw)


def _is_postgres_url(value: str) -> bool:
    return str(value or '').startswith(('postgresql://', 'postgresql+asyncpg://'))


def _parse_date(value: Any) -> date | None:
    if value is None or value == '':
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value).strip()
    if not raw:
        return None
    return date.fromisoformat(raw[:10])


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or value == '':
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime.combine(value, datetime.min.time())
    else:
        raw = str(value).strip()
        if not raw:
            return None
        if raw.endswith('Z'):
            raw = raw[:-1] + '+00:00'
        if 'T' not in raw and ' ' in raw:
            raw = raw.replace(' ', 'T', 1)
        dt = datetime.fromisoformat(raw)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _parse_bool(value: Any) -> bool | None:
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    raw = str(value).strip().lower()
    if raw in {'1', 'true', 't', 'yes', 'y', 'да'}:
        return True
    if raw in {'0', 'false', 'f', 'no', 'n', 'нет'}:
        return False
    return bool(raw)


def _convert_value(column, value: Any) -> Any:
    if value is None:
        return None
    column_type = column.type
    if isinstance(column_type, DateTime):
        return _parse_datetime(value)
    if isinstance(column_type, Date):
        return _parse_date(value)
    if isinstance(column_type, Boolean):
        return _parse_bool(value)
    if isinstance(column_type, Integer) and not isinstance(value, bool):
        if value == '':
            return None
        return int(value)
    if isinstance(column_type, Float):
        if value == '':
            return None
        return float(value)
    return value


def _read_rows(conn: sqlite3.Connection, table_name: str) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(f'SELECT * FROM "{table_name}"').fetchall()


def _source_tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    }


async def _target_table_count(async_conn, table) -> int:
    result = await async_conn.execute(select(func.count()).select_from(table))
    return int(result.scalar_one() or 0)


async def _create_schema(async_conn) -> None:
    await async_conn.run_sync(bootstrap_schema_and_admin)
    await async_conn.run_sync(bootstrap_payroll_schema)


async def _reset_postgres_sequences(async_conn) -> None:
    for table in Base.metadata.sorted_tables:
        if len(table.primary_key.columns) != 1:
            continue
        pk = next(iter(table.primary_key.columns))
        if not isinstance(pk.type, Integer):
            continue
        max_result = await async_conn.execute(select(func.max(pk)).select_from(table))
        max_id = max_result.scalar_one_or_none()
        if not max_id:
            continue
        seq_result = await async_conn.execute(
            text('SELECT pg_get_serial_sequence(:table_name, :column_name)'),
            {'table_name': table.name, 'column_name': pk.name},
        )
        sequence_name = seq_result.scalar_one_or_none()
        if sequence_name:
            await async_conn.execute(
                text('SELECT setval(:sequence_name, :max_id, true)'),
                {'sequence_name': sequence_name, 'max_id': int(max_id)},
            )


async def migrate(sqlite_path: str, postgres_url: str, *, drop_existing: bool = False, yes: bool = False, batch_size: int = 500) -> None:
    if not os.path.exists(sqlite_path):
        raise SystemExit(f'SQLite-БД не найдена: {sqlite_path}')
    if not _is_postgres_url(postgres_url):
        raise SystemExit('Для --postgres нужно указать PostgreSQL URL, например postgresql+asyncpg://user:pass@host:5432/db')
    if drop_existing and not yes:
        raise SystemExit('Опция --drop-existing требует подтверждение --yes, чтобы случайно не удалить PostgreSQL-данные.')

    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row
    source_tables = _source_tables(sqlite_conn)

    engine = create_async_engine(postgres_url, echo=False, pool_pre_ping=True)
    try:
        async with engine.begin() as target_conn:
            if drop_existing:
                print('Удаляю существующую схему PostgreSQL...')
                await target_conn.run_sync(Base.metadata.drop_all)

            print('Создаю/обновляю схему PostgreSQL...')
            await _create_schema(target_conn)

            non_empty: list[str] = []
            for table in Base.metadata.sorted_tables:
                if table.name in source_tables and await _target_table_count(target_conn, table) > 0:
                    non_empty.append(table.name)
            if non_empty and not drop_existing:
                raise SystemExit(
                    'Целевая PostgreSQL-БД не пустая. Таблицы с данными: '
                    + ', '.join(non_empty[:20])
                    + (', ...' if len(non_empty) > 20 else '')
                    + '. Создай пустую БД или запусти с --drop-existing --yes.'
                )

            total_inserted = 0
            for table in Base.metadata.sorted_tables:
                if table.name not in source_tables:
                    print(f'Пропускаю {table.name}: нет в SQLite.')
                    continue

                source_rows = _read_rows(sqlite_conn, table.name)
                if not source_rows:
                    print(f'{table.name}: 0 строк.')
                    continue

                table_columns = {column.name: column for column in table.columns}
                source_column_names = set(source_rows[0].keys())
                copy_column_names = [name for name in table_columns if name in source_column_names]
                if not copy_column_names:
                    print(f'Пропускаю {table.name}: нет совпадающих колонок.')
                    continue

                converted_rows: list[dict[str, Any]] = []
                for row in source_rows:
                    converted_rows.append({
                        name: _convert_value(table_columns[name], row[name])
                        for name in copy_column_names
                    })

                for offset in range(0, len(converted_rows), batch_size):
                    await target_conn.execute(table.insert(), converted_rows[offset:offset + batch_size])

                total_inserted += len(converted_rows)
                print(f'{table.name}: перенесено {len(converted_rows)} строк.')

            print('Обновляю PostgreSQL sequences...')
            await _reset_postgres_sequences(target_conn)
            print(f'Готово. Всего перенесено строк: {total_inserted}.')
    finally:
        sqlite_conn.close()
        await engine.dispose()


def main() -> None:
    default_sqlite = os.getenv('SQLITE_DATABASE_URL') or os.getenv('SQLITE_DB_PATH') or str(PROJECT_ROOT / 'inventory.db')
    default_postgres = os.getenv('POSTGRES_DATABASE_URL') or ''

    parser = argparse.ArgumentParser(description='Перенос SQLite inventory.db в PostgreSQL для Smart Inventory / UCHETKA.')
    parser.add_argument('--sqlite', default=default_sqlite, help='Путь к inventory.db или sqlite+aiosqlite URL.')
    parser.add_argument('--postgres', default=default_postgres, help='PostgreSQL URL: postgresql+asyncpg://user:pass@host:5432/db')
    parser.add_argument('--drop-existing', action='store_true', help='Удалить существующие таблицы PostgreSQL перед переносом.')
    parser.add_argument('--yes', action='store_true', help='Подтвердить опасные действия, например --drop-existing.')
    parser.add_argument('--batch-size', type=int, default=500, help='Размер пачки INSERT. По умолчанию 500.')
    args = parser.parse_args()

    if not args.postgres:
        raise SystemExit('Укажи --postgres или переменную POSTGRES_DATABASE_URL.')

    sqlite_path = _sqlite_path(args.sqlite)
    asyncio.run(migrate(
        sqlite_path,
        args.postgres,
        drop_existing=args.drop_existing,
        yes=args.yes,
        batch_size=max(1, int(args.batch_size or 500)),
    ))


if __name__ == '__main__':
    main()
