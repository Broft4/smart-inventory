from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import date, timedelta

from app.database import AsyncSessionLocal
from app.payroll import get_moscow_today, rebuild_closed_shift_snapshots


def _parse_iso_date(value: str | None) -> date | None:
    raw = str(value or '').strip()
    if not raw:
        return None
    return date.fromisoformat(raw)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Пересчитывает snapshot закрытых смен за период.')
    parser.add_argument('--days', type=int, default=31, help='Сколько последних дней пересчитать, если не переданы --date-from/--date-to.')
    parser.add_argument('--date-from', type=str, default=None, help='Начальная дата периода в формате YYYY-MM-DD.')
    parser.add_argument('--date-to', type=str, default=None, help='Конечная дата периода в формате YYYY-MM-DD.')
    parser.add_argument('--location', type=str, default=None, help='Название точки. Если не указано, пересчитываются все точки.')
    return parser


def _resolve_period(args: argparse.Namespace) -> tuple[date, date]:
    date_from = _parse_iso_date(args.date_from)
    date_to = _parse_iso_date(args.date_to)
    if date_from and date_to:
        if date_from > date_to:
            raise SystemExit('--date-from не может быть позже --date-to.')
        return date_from, date_to
    if date_from or date_to:
        raise SystemExit('Нужно передать обе даты: и --date-from, и --date-to.')

    days = max(int(args.days or 1), 1)
    date_to = get_moscow_today()
    date_from = date_to - timedelta(days=days - 1)
    return date_from, date_to


async def _run() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    date_from, date_to = _resolve_period(args)

    async with AsyncSessionLocal() as db:
        result = await rebuild_closed_shift_snapshots(date_from, date_to, db, location=args.location)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run())
