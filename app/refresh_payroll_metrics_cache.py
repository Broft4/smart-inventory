from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date, timedelta
from typing import Any

from app.database import AsyncSessionLocal
from app.payroll import (
    get_moscow_today,
    rebuild_closed_shift_snapshots,
    refresh_payroll_metrics_cache,
)


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
        description='Обновляет кеш payroll-метрик МойСклад и при необходимости пересобирает snapshots закрытых смен.'
    )
    parser.add_argument(
        '--days',
        type=int,
        default=31,
        help='Сколько последних дней обработать, если не переданы --date-from/--date-to.',
    )
    parser.add_argument('--date-from', type=str, default=None, help='Начальная дата периода в формате YYYY-MM-DD.')
    parser.add_argument('--date-to', type=str, default=None, help='Конечная дата периода в формате YYYY-MM-DD. По умолчанию — сегодня.')
    parser.add_argument(
        '--location',
        type=str,
        default=None,
        help='Название точки. Если не указано, обрабатываются все точки.',
    )
    parser.add_argument(
        '--force-refresh',
        action='store_true',
        help='Игнорировать существующий кеш и заново выгрузить данные из МойСклад.',
    )
    parser.add_argument(
        '--rebuild-closed-shifts',
        action='store_true',
        help='После обновления кеша пересобрать snapshots закрытых смен за период.',
    )
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


async def _run() -> None:
    args = _build_parser().parse_args()
    date_from, date_to = _resolve_period(args)

    payload: dict[str, Any] = {
        'date_from': date_from.isoformat(),
        'date_to': date_to.isoformat(),
        'location': args.location.strip().title() if args.location else None,
    }

    async with AsyncSessionLocal() as db:
        payload['cache_refresh'] = await refresh_payroll_metrics_cache(
            date_from,
            date_to,
            db,
            location=args.location,
            force_refresh=bool(args.force_refresh),
        )

        if args.rebuild_closed_shifts:
            payload['closed_shift_snapshots'] = await rebuild_closed_shift_snapshots(
                date_from,
                date_to,
                db,
                location=args.location,
                # Кеш уже обновили строкой выше, поэтому второй принудительный проход не нужен.
                force_refresh_metrics=False,
            )

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    asyncio.run(_run())
