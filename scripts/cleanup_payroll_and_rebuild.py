from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

from sqlalchemy import select, tuple_

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import AsyncSessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    LocationPoint,
    ShiftPayrollCategorySnapshot,
    ShiftPayrollSnapshot,
    WorkShift,
)
from app.payroll import get_moscow_today, rebuild_closed_shift_snapshots  # noqa: E402


@dataclass(slots=True)
class CleanupResult:
    deleted_shift_ids: list[int]
    deleted_snapshot_ids: list[int]
    deleted_category_snapshot_count: int
    duplicate_groups: list[dict[str, object]]

    @property
    def deleted_shift_count(self) -> int:
        return len(self.deleted_shift_ids)

    @property
    def deleted_snapshot_count(self) -> int:
        return len(self.deleted_snapshot_ids)


def _parse_iso_date(value: str | None) -> date | None:
    raw = str(value or '').strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise SystemExit(f'Некорректная дата: {raw}. Ожидается YYYY-MM-DD.') from exc


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            'Чистит удалённые и дублирующиеся смены из payroll-части БД '
            'и пересчитывает snapshots закрытых смен за период.'
        )
    )
    parser.add_argument('--days', type=int, default=31, help='Сколько последних дней обработать, если не переданы --date-from/--date-to.')
    parser.add_argument('--date-from', type=str, default=None, help='Начальная дата периода в формате YYYY-MM-DD.')
    parser.add_argument('--date-to', type=str, default=None, help='Конечная дата периода в формате YYYY-MM-DD. По умолчанию — сегодня по Москве.')
    parser.add_argument('--location', type=str, default=None, help='Название точки. Если не указано, обрабатываются все точки.')
    parser.add_argument('--skip-rebuild', action='store_true', help='Только почистить БД, без пересчёта snapshots.')
    parser.add_argument('--force-refresh-metrics', action='store_true', help='Перед пересчётом принудительно освежить метрики продаж/возвратов из МойСклад.')
    parser.add_argument('--dry-run', action='store_true', help='Ничего не менять в БД, только показать что будет очищено и пересчитано.')
    return parser


def _resolve_period(args: argparse.Namespace) -> tuple[date, date]:
    date_from = _parse_iso_date(args.date_from)
    date_to = _parse_iso_date(args.date_to)

    if date_from and date_to:
        if date_from > date_to:
            raise SystemExit('--date-from не может быть позже --date-to.')
        return date_from, date_to

    if date_from and not date_to:
        return date_from, get_moscow_today()

    if date_to and not date_from:
        raise SystemExit('Если передан --date-to, нужно передать и --date-from.')

    days = max(int(args.days or 1), 1)
    date_to = get_moscow_today()
    date_from = date_to - timedelta(days=days - 1)
    return date_from, date_to


async def _delete_shift_with_snapshots(db, shift: WorkShift, *, dry_run: bool) -> tuple[int | None, int | None, int]:
    snapshot = await db.scalar(
        select(ShiftPayrollSnapshot)
        .where(ShiftPayrollSnapshot.shift_id == shift.id)
        .limit(1)
    )
    category_count = 0
    snapshot_id = None
    if snapshot is not None:
        snapshot_id = snapshot.id
        category_rows = (
            await db.scalars(
                select(ShiftPayrollCategorySnapshot.id)
                .where(ShiftPayrollCategorySnapshot.snapshot_id == snapshot.id)
            )
        ).all()
        category_count = len(category_rows)
        if not dry_run:
            for row_id in category_rows:
                category_snapshot = await db.get(ShiftPayrollCategorySnapshot, row_id)
                if category_snapshot is not None:
                    await db.delete(category_snapshot)
            await db.delete(snapshot)
    if not dry_run:
        await db.delete(shift)
    return shift.id, snapshot_id, category_count


async def _cleanup_deleted_and_duplicate_shifts(
    db,
    *,
    date_from: date,
    date_to: date,
    location: str | None,
    dry_run: bool,
) -> CleanupResult:
    query = (
        select(WorkShift)
        .where(
            WorkShift.shift_date >= date_from,
            WorkShift.shift_date <= date_to,
        )
        .order_by(WorkShift.shift_date.asc(), WorkShift.employee_user_id.asc(), WorkShift.id.asc())
    )

    point_ids_filter: set[int] | None = None
    normalized_location: str | None = None
    if location:
        normalized_location = location.strip().title()
        point = await db.scalar(
            select(LocationPoint).where(LocationPoint.name == normalized_location).limit(1)
        )
        if point is None:
            return CleanupResult([], [], 0, [])
        point_ids_filter = {point.id}
        query = query.where(WorkShift.location_point_id == point.id)

    shifts = (await db.scalars(query)).all()
    by_key: dict[tuple[int, date, int], list[WorkShift]] = defaultdict(list)
    for shift in shifts:
        if point_ids_filter is not None and shift.location_point_id not in point_ids_filter:
            continue
        by_key[(shift.location_point_id, shift.shift_date, shift.employee_user_id)].append(shift)

    deleted_shift_ids: list[int] = []
    deleted_snapshot_ids: list[int] = []
    deleted_category_snapshot_count = 0
    duplicate_groups: list[dict[str, object]] = []

    # Сначала удаляем все is_deleted в периоде — они уже не должны участвовать ни в календаре, ни в бухгалтерии.
    for shift in shifts:
        if not shift.is_deleted:
            continue
        shift_id, snapshot_id, category_count = await _delete_shift_with_snapshots(db, shift, dry_run=dry_run)
        if shift_id is not None:
            deleted_shift_ids.append(shift_id)
        if snapshot_id is not None:
            deleted_snapshot_ids.append(snapshot_id)
        deleted_category_snapshot_count += category_count

    # Затем чистим дубли по (точка, дата, сотрудник), оставляя одну наиболее "правильную" запись.
    for (location_point_id, shift_date, employee_user_id), group in by_key.items():
        active_group = [row for row in group if not row.is_deleted]
        if len(active_group) <= 1:
            continue

        def sort_key(row: WorkShift) -> tuple[int, int, object, object, int]:
            return (
                0 if row.status == 'closed' else 1,
                0 if row.closed_at is not None else 1,
                -(row.closed_at.timestamp()) if row.closed_at else 0,
                -(row.updated_at.timestamp()) if row.updated_at else 0,
                -row.id,
            )

        ordered = sorted(active_group, key=sort_key)
        keep = ordered[0]
        to_delete = ordered[1:]
        if not to_delete:
            continue

        point_name = None
        point = await db.get(LocationPoint, location_point_id)
        if point is not None:
            point_name = point.name
        duplicate_groups.append({
            'location': point_name,
            'location_point_id': location_point_id,
            'shift_date': shift_date.isoformat(),
            'employee_user_id': employee_user_id,
            'kept_shift_id': keep.id,
            'deleted_shift_ids': [row.id for row in to_delete],
        })

        for shift in to_delete:
            shift_id, snapshot_id, category_count = await _delete_shift_with_snapshots(db, shift, dry_run=dry_run)
            if shift_id is not None:
                deleted_shift_ids.append(shift_id)
            if snapshot_id is not None:
                deleted_snapshot_ids.append(snapshot_id)
            deleted_category_snapshot_count += category_count

    if not dry_run:
        await db.commit()

    return CleanupResult(
        deleted_shift_ids=deleted_shift_ids,
        deleted_snapshot_ids=deleted_snapshot_ids,
        deleted_category_snapshot_count=deleted_category_snapshot_count,
        duplicate_groups=duplicate_groups,
    )


async def _run() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    date_from, date_to = _resolve_period(args)

    async with AsyncSessionLocal() as db:
        cleanup = await _cleanup_deleted_and_duplicate_shifts(
            db,
            date_from=date_from,
            date_to=date_to,
            location=args.location,
            dry_run=bool(args.dry_run),
        )

    rebuild_result: dict[str, object] | None = None
    if not args.skip_rebuild:
        if args.dry_run:
            rebuild_result = {
                'date_from': date_from.isoformat(),
                'date_to': date_to.isoformat(),
                'location': args.location.strip().title() if args.location else None,
                'dry_run': True,
                'message': 'Пересчёт не запускался, потому что включён --dry-run.',
            }
        else:
            async with AsyncSessionLocal() as db:
                rebuild_result = await rebuild_closed_shift_snapshots(
                    date_from,
                    date_to,
                    db,
                    location=args.location,
                    force_refresh_metrics=bool(args.force_refresh_metrics),
                )

    result = {
        'period': {
            'date_from': date_from.isoformat(),
            'date_to': date_to.isoformat(),
            'location': args.location.strip().title() if args.location else None,
        },
        'cleanup': {
            'deleted_shift_count': cleanup.deleted_shift_count,
            'deleted_shift_ids': cleanup.deleted_shift_ids,
            'deleted_snapshot_count': cleanup.deleted_snapshot_count,
            'deleted_snapshot_ids': cleanup.deleted_snapshot_ids,
            'deleted_category_snapshot_count': cleanup.deleted_category_snapshot_count,
            'duplicate_groups': cleanup.duplicate_groups,
        },
        'rebuild': rebuild_result,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    asyncio.run(_run())
