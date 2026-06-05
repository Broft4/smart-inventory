from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import date, timedelta
from typing import Any

from app.database import AsyncSessionLocal
from app.payroll import backfill_closed_shift_sales_motivation_snapshots, get_moscow_today


def _parse_iso_date(value: str | None) -> date | None:
    raw = str(value or '').strip()
    if not raw:
        return None
    return date.fromisoformat(raw)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Точечно добавляет продажи мотивационных товаров в snapshots уже закрытых смен.'
    )
    parser.add_argument(
        '--date',
        type=str,
        default=None,
        help='Один день в формате YYYY-MM-DD. Удобно для восстановления вчерашней закрытой смены.',
    )
    parser.add_argument('--date-from', type=str, default=None, help='Начальная дата периода в формате YYYY-MM-DD.')
    parser.add_argument('--date-to', type=str, default=None, help='Конечная дата периода в формате YYYY-MM-DD.')
    parser.add_argument(
        '--days',
        type=int,
        default=1,
        help='Сколько дней восстановить, если даты не переданы. По умолчанию 1 день: вчера по МСК.',
    )
    parser.add_argument('--location', type=str, default=None, help='Название точки. Если не указано, обрабатываются все точки.')
    parser.add_argument(
        '--no-refresh-daily-snapshots',
        action='store_true',
        help='Не ходить в МойСклад; использовать уже сохранённые дневные снимки мотиваций.',
    )
    parser.add_argument(
        '--refresh-catalog',
        action='store_true',
        help='Дополнительно обновить каталог/остатки мотиваций. По умолчанию выключено, чтобы не делать тяжёлый stock/all.',
    )
    return parser


def _resolve_period(args: argparse.Namespace) -> tuple[date, date]:
    exact_date = _parse_iso_date(args.date)
    date_from = _parse_iso_date(args.date_from)
    date_to = _parse_iso_date(args.date_to)

    if exact_date:
        if date_from or date_to:
            raise SystemExit('Нельзя одновременно передавать --date и --date-from/--date-to.')
        return exact_date, exact_date

    if date_from and date_to:
        if date_from > date_to:
            raise SystemExit('--date-from не может быть позже --date-to.')
        return date_from, date_to

    if date_from or date_to:
        raise SystemExit('Для периода нужно передать обе даты: --date-from и --date-to.')

    days = max(int(args.days or 1), 1)
    date_to = get_moscow_today() - timedelta(days=1)
    date_from = date_to - timedelta(days=days - 1)
    return date_from, date_to


def _log(message: str) -> None:
    print(message, flush=True)


async def _run() -> None:
    args = _build_parser().parse_args()
    date_from, date_to = _resolve_period(args)

    async def progress(event: str, data: dict[str, Any]) -> None:
        if event == 'start':
            _log(
                'Старт восстановления мотиваций закрытых смен: '
                f'{data.get("date_from")} — {data.get("date_to")}, '
                f'точки: {", ".join(data.get("locations") or []) or "нет"}, '
                f'обновлять дневные снимки={data.get("refresh_daily_snapshots")}, '
                f'обновлять каталог={data.get("refresh_catalog")}.'
            )
        elif event == 'daily_snapshot_start':
            _log(f'Обновляю дневные снимки мотиваций: {data.get("location")}...')
        elif event == 'daily_snapshot_done':
            _log(
                f'Дневные снимки готовы: {data.get("location") or "точка"}, '
                f'строк={data.get("snapshots_created")}, продано={data.get("sold_rows")}, '
                f'сумма мотиваций={data.get("bonus_total")}. '
                f'Каталог обновлялся={data.get("catalog_refreshed")}.'
            )
        elif event == 'shift_done':
            _log(
                f'Смена {data.get("shift_id")} за {data.get("shift_date")} / {data.get("location")}: '
                f'строк={data.get("row_count")}, мотивация={data.get("sales_motivation_amount")}.'
            )

    async with AsyncSessionLocal() as db:
        result = await backfill_closed_shift_sales_motivation_snapshots(
            date_from,
            date_to,
            db,
            location=args.location,
            refresh_daily_snapshots=not args.no_refresh_daily_snapshots,
            refresh_catalog=bool(args.refresh_catalog),
            progress_callback=progress,
        )

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run())
