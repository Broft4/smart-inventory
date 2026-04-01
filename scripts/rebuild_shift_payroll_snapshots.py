from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import delete, select

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
from app.payroll import _build_computed_shift  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Пересчитывает shift_payroll_snapshots после починки интеграции с МойСклад.',
    )
    parser.add_argument('--date-from', required=True, help='Начало периода в формате YYYY-MM-DD')
    parser.add_argument('--date-to', required=True, help='Конец периода в формате YYYY-MM-DD')
    parser.add_argument('--location', help='Название точки из location_points.name')
    parser.add_argument(
        '--all-matched',
        action='store_true',
        help='Пересчитать все найденные закрытые смены, а не только смены с нулевыми снапшотами.',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Ничего не менять в БД, только показать какие смены будут пересчитаны.',
    )
    return parser.parse_args()


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SystemExit(f'Некорректная дата: {value}. Ожидается YYYY-MM-DD') from exc


async def rebuild_snapshot_for_shift(shift: WorkShift, dry_run: bool) -> tuple[bool, str]:
    async with AsyncSessionLocal() as db:
        shift = await db.get(WorkShift, shift.id)
        if not shift or shift.is_deleted:
            return False, f'Смена {getattr(shift, "id", "?")} не найдена.'

        point = await db.get(LocationPoint, shift.location_point_id)
        if not point:
            return False, f'У смены {shift.id} не найдена точка.'

        snapshot = await db.scalar(
            select(ShiftPayrollSnapshot)
            .where(ShiftPayrollSnapshot.shift_id == shift.id)
            .limit(1)
        )
        if snapshot is None:
            return False, f'У смены {shift.id} нет существующего снапшота.'

        existing_closed_at = snapshot.closed_at
        existing_is_auto_closed = bool(snapshot.is_auto_closed)

        if dry_run:
            return True, (
                f'[DRY-RUN] shift_id={shift.id} date={shift.shift_date.isoformat()} '
                f'location={point.name} employee_user_id={shift.employee_user_id}'
            )

        await db.execute(
            delete(ShiftPayrollCategorySnapshot)
            .where(ShiftPayrollCategorySnapshot.snapshot_id == snapshot.id)
        )
        await db.delete(snapshot)
        await db.flush()

        computed = await _build_computed_shift(shift, db)

        new_snapshot = ShiftPayrollSnapshot(
            shift_id=shift.id,
            location_point_id=point.id,
            employee_user_id=shift.employee_user_id,
            shift_date=shift.shift_date,
            settings_version_id=computed.settings.id,
            split_count=computed.split_count,
            share_ratio=computed.share_ratio,
            exit_amount=computed.exit_amount,
            bonus_threshold=computed.bonus_threshold,
            bonus_amount=computed.bonus_amount,
            other_rate_percent=computed.other_rate_percent,
            non_tobacco_net_sales_for_bonus=computed.non_tobacco_net_sales_for_bonus,
            gross_sales_amount=computed.gross_sales_amount,
            return_amount=computed.return_amount,
            net_sales_amount=computed.net_sales_amount,
            cost_amount=computed.cost_amount,
            gross_profit_amount=computed.gross_profit_amount,
            category_earnings_total=computed.category_earnings_total,
            employee_expense_amount=0.0,
            gross_salary_amount=computed.gross_salary_amount,
            net_salary_amount=computed.gross_salary_amount,
            is_auto_closed=existing_is_auto_closed,
            closed_at=existing_closed_at or shift.closed_at or datetime.utcnow(),
        )
        db.add(new_snapshot)
        await db.flush()

        for row in computed.categories:
            db.add(ShiftPayrollCategorySnapshot(
                snapshot_id=new_snapshot.id,
                category_id=row['category_id'],
                category_name=row['category_name'],
                rate_percent=row['rate_percent'],
                sales_amount=row['sales_amount'],
                return_amount=row['return_amount'],
                net_sales_amount=row['net_sales_amount'],
                earning_amount=row['earning_amount'],
                is_other_category=row['is_other_category'],
            ))

        await db.commit()
        return True, (
            f'Пересчитана shift_id={shift.id} date={shift.shift_date.isoformat()} '
            f'location={point.name} gross={computed.gross_sales_amount:.2f} '
            f'returns={computed.return_amount:.2f} cost={computed.cost_amount:.2f}'
        )


async def main() -> int:
    args = parse_args()
    date_from = parse_date(args.date_from)
    date_to = parse_date(args.date_to)
    if date_from > date_to:
        raise SystemExit('date-from не может быть позже date-to')

    async with AsyncSessionLocal() as db:
        query = (
            select(WorkShift, ShiftPayrollSnapshot, LocationPoint)
            .join(ShiftPayrollSnapshot, ShiftPayrollSnapshot.shift_id == WorkShift.id)
            .join(LocationPoint, LocationPoint.id == WorkShift.location_point_id)
            .where(
                WorkShift.is_deleted.is_(False),
                WorkShift.status == 'closed',
                WorkShift.shift_date >= date_from,
                WorkShift.shift_date <= date_to,
            )
            .order_by(WorkShift.shift_date.asc(), WorkShift.id.asc())
        )
        if args.location:
            query = query.where(LocationPoint.name == args.location.strip().title())
        if not args.all_matched:
            query = query.where(
                ShiftPayrollSnapshot.gross_sales_amount == 0,
                ShiftPayrollSnapshot.return_amount == 0,
                ShiftPayrollSnapshot.cost_amount == 0,
            )

        rows = (await db.execute(query)).all()

    if not rows:
        print('Подходящих закрытых смен не найдено.')
        return 0

    print(f'Найдено смен для пересчета: {len(rows)}')
    success_count = 0
    error_count = 0

    for shift, _snapshot, _point in rows:
        ok, message = await rebuild_snapshot_for_shift(shift, dry_run=args.dry_run)
        print(message)
        if ok:
            success_count += 1
        else:
            error_count += 1

    print(f'Готово. success={success_count} errors={error_count}')
    return 0 if error_count == 0 else 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
