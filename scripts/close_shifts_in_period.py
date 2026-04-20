from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import date
from typing import Any

from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models import LocationPoint, User, WorkShift
from app.payroll import close_shift, rebuild_closed_shift_snapshots, refresh_payroll_metrics_cache

logger = logging.getLogger(__name__)


def _parse_iso_date(value: str) -> date:
    raw = str(value or '').strip()
    if not raw:
        raise SystemExit('Нужно передать дату в формате YYYY-MM-DD.')
    return date.fromisoformat(raw)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Закрывает открытые смены за период через штатную payroll-логику приложения.'
    )
    parser.add_argument('--date-from', required=True, help='Начальная дата периода в формате YYYY-MM-DD.')
    parser.add_argument('--date-to', required=True, help='Конечная дата периода в формате YYYY-MM-DD.')
    parser.add_argument(
        '--location',
        action='append',
        default=None,
        help='Название точки. Можно указать несколько раз. Если не задано, берутся все точки.',
    )
    parser.add_argument(
        '--shift-id',
        type=int,
        action='append',
        default=None,
        help='Закрыть только конкретные shift_id. Можно указать несколько раз.',
    )
    parser.add_argument(
        '--include-deleted',
        action='store_true',
        help='Обрабатывать и скрытые (deleted) смены. По умолчанию deleted пропускаются.',
    )
    parser.add_argument(
        '--rebuild-closed-shifts',
        action='store_true',
        help='После закрытия пересобрать snapshot закрытых смен за тот же период.',
    )
    parser.add_argument(
        '--refresh-metrics',
        action='store_true',
        help='Перед пересборкой snapshot заново обновить дневной кеш продаж из МоегоСклада за период.',
    )
    parser.add_argument(
        '--force-refresh',
        action='store_true',
        help='Игнорировать существующий кеш и перезапросить данные из МоегоСклада (работает с --refresh-metrics).',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Ничего не менять, только показать, какие смены будут закрыты.',
    )
    return parser


async def _run() -> None:
    args = _build_parser().parse_args()
    date_from = _parse_iso_date(args.date_from)
    date_to = _parse_iso_date(args.date_to)
    if date_from > date_to:
        raise SystemExit('--date-from не может быть позже --date-to.')

    async with AsyncSessionLocal() as db:
        query = (
            select(WorkShift)
            .where(
                WorkShift.shift_date >= date_from,
                WorkShift.shift_date <= date_to,
            )
            .order_by(WorkShift.shift_date.asc(), WorkShift.id.asc())
        )
        if not args.include_deleted:
            query = query.where(WorkShift.is_deleted.is_(False))
        if args.location:
            points = (
                await db.scalars(
                    select(LocationPoint).where(LocationPoint.name.in_(list(dict.fromkeys(args.location))))
                )
            ).all()
            point_ids = [point.id for point in points]
            if not point_ids:
                print(json.dumps({'matched': 0, 'closed': 0, 'details': [], 'warning': 'Точки не найдены.'}, ensure_ascii=False, indent=2))
                return
            query = query.where(WorkShift.location_point_id.in_(point_ids))
        if args.shift_id:
            query = query.where(WorkShift.id.in_(list(dict.fromkeys(args.shift_id))))

        shifts = (await db.scalars(query)).all()
        point_ids = {shift.location_point_id for shift in shifts}
        user_ids = {shift.employee_user_id for shift in shifts}
        points = {
            point.id: point
            for point in (await db.scalars(select(LocationPoint).where(LocationPoint.id.in_(point_ids)))).all()
        } if point_ids else {}
        users = {
            user.id: user
            for user in (await db.scalars(select(User).where(User.id.in_(user_ids)))).all()
        } if user_ids else {}

        target_shifts = [shift for shift in shifts if shift.status != 'closed']
        details: list[dict[str, Any]] = []
        for shift in target_shifts:
            point = points.get(shift.location_point_id)
            user = users.get(shift.employee_user_id)
            details.append({
                'shift_id': shift.id,
                'shift_date': shift.shift_date.isoformat(),
                'location': point.name if point else None,
                'employee_user_id': shift.employee_user_id,
                'employee_name': user.full_name if user else None,
                'status_before': shift.status,
                'is_deleted': bool(shift.is_deleted),
            })

        if args.dry_run:
            print(json.dumps({
                'date_from': date_from.isoformat(),
                'date_to': date_to.isoformat(),
                'matched': len(shifts),
                'to_close': len(target_shifts),
                'details': details,
            }, ensure_ascii=False, indent=2))
            return

        closed_count = 0
        close_results: list[dict[str, Any]] = []
        for shift in target_shifts:
            result = await close_shift(shift.id, db, actor_user=None, auto=True)
            close_results.append({
                'shift_id': shift.id,
                'shift_date': shift.shift_date.isoformat(),
                'status': result.get('shift', {}).get('status'),
                'message': result.get('message'),
            })
            closed_count += 1

        payload: dict[str, Any] = {
            'date_from': date_from.isoformat(),
            'date_to': date_to.isoformat(),
            'matched': len(shifts),
            'closed': closed_count,
            'details': details,
            'close_results': close_results,
        }

        if args.refresh_metrics:
            payload['cache_refresh'] = await refresh_payroll_metrics_cache(
                date_from,
                date_to,
                db,
                location=args.location[0] if args.location and len(args.location) == 1 else None,
                force_refresh=bool(args.force_refresh),
            )
        if args.rebuild_closed_shifts:
            payload['closed_shift_snapshots'] = await rebuild_closed_shift_snapshots(
                date_from,
                date_to,
                db,
                location=args.location[0] if args.location and len(args.location) == 1 else None,
                force_refresh_metrics=bool(args.force_refresh and args.refresh_metrics),
            )

        print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run())
