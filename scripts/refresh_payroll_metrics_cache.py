from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import date, timedelta

from app.database import AsyncSessionLocal
from app.logic import refresh_product_financial_cache
from app.payroll import (
    auto_close_open_shifts_in_period,
    get_moscow_today,
    rebuild_closed_shift_snapshots,
    refresh_payroll_metrics_cache,
)

logger = logging.getLogger(__name__)


def _parse_iso_date(value: str | None) -> date | None:
    raw = str(value or '').strip()
    if not raw:
        return None
    return date.fromisoformat(raw)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Обновляет кеш дневных метрик зарплаты из МоегоСклада.')
    parser.add_argument('--days', type=int, default=31, help='Сколько последних дней обновить, если не переданы --date-from/--date-to.')
    parser.add_argument('--date-from', type=str, default=None, help='Начальная дата периода в формате YYYY-MM-DD.')
    parser.add_argument('--date-to', type=str, default=None, help='Конечная дата периода в формате YYYY-MM-DD.')
    parser.add_argument('--yesterday-only', action='store_true', help='Обновить только вчерашний день по Москве.')
    parser.add_argument('--location', type=str, default=None, help='Название точки. Если не указано, обновляются все точки.')
    parser.add_argument('--auto-close-open-shifts', action='store_true', help='Перед обновлением кеша автоматически закрыть все открытые смены за выбранный период.')
    parser.add_argument('--force-refresh', action='store_true', help='Игнорировать существующий кеш и перезапросить данные.')
    parser.add_argument(
        '--rebuild-closed-shifts',
        action='store_true',
        help='После обновления кеша пересчитать snapshot закрытых смен за тот же период.',
    )
    parser.add_argument(
        '--skip-product-financials',
        action='store_true',
        help='Не обновлять локальный кеш себестоимости/цен товаров для быстрых админ-ревизий.',
    )
    return parser


def _resolve_period(args: argparse.Namespace) -> tuple[date, date]:
    if bool(args.yesterday_only):
        yesterday = get_moscow_today() - timedelta(days=1)
        return yesterday, yesterday

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
        payload: dict[str, object] = {}
        if args.auto_close_open_shifts:
            payload['auto_closed_shifts'] = await auto_close_open_shifts_in_period(
                date_from,
                date_to,
                db,
                location=args.location,
            )

        cache_result = await refresh_payroll_metrics_cache(
            date_from,
            date_to,
            db,
            location=args.location,
            force_refresh=bool(args.force_refresh),
        )

        payload['cache_refresh'] = cache_result
        if not args.skip_product_financials:
            payload['product_financial_cache'] = await refresh_product_financial_cache(
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
                force_refresh_metrics=False,
            )

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run())
