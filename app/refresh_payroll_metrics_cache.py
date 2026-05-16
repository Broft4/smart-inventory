from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import time
from datetime import date, datetime, timedelta
from typing import Any

from app.database import AsyncSessionLocal
from app.payroll import (
    auto_close_due_shifts,
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
    parser.add_argument(
        '--auto-close-due-shifts',
        action='store_true',
        help='После обновления кеша автоматически закрыть незакрытые смены в периоде, включая текущий день.',
    )
    parser.add_argument(
        '--heartbeat-seconds',
        type=int,
        default=15,
        help='Как часто печатать сообщение, что процесс всё ещё работает. По умолчанию 15 секунд.',
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


def _log(message: str) -> None:
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{timestamp}] {message}', flush=True)


async def _heartbeat(stage: dict[str, Any], interval_seconds: int) -> None:
    interval = max(int(interval_seconds or 15), 5)
    start_monotonic = time.monotonic()
    while True:
        await asyncio.sleep(interval)
        elapsed = int(time.monotonic() - start_monotonic)
        minutes, seconds = divmod(elapsed, 60)
        current_stage = str(stage.get('message') or 'идёт обработка')
        _log(f'Процесс работает: {current_stage}. Прошло {minutes} мин {seconds:02d} сек.')


def _format_money(value: Any) -> str:
    try:
        return f'{float(value or 0):,.2f}'.replace(',', ' ')
    except (TypeError, ValueError):
        return '0.00'


async def _run() -> None:
    args = _build_parser().parse_args()
    date_from, date_to = _resolve_period(args)
    normalized_location = args.location.strip().title() if args.location else None

    payload: dict[str, Any] = {
        'date_from': date_from.isoformat(),
        'date_to': date_to.isoformat(),
        'location': normalized_location,
    }

    stage: dict[str, Any] = {'message': 'подготовка'}
    heartbeat_task = asyncio.create_task(_heartbeat(stage, args.heartbeat_seconds))

    try:
        _log(
            'Старт обновления payroll-кеша: '
            f'{date_from.isoformat()} — {date_to.isoformat()}, '
            f'точка: {normalized_location or "все точки"}, '
            f'force_refresh={bool(args.force_refresh)}, '
            f'rebuild_closed_shifts={bool(args.rebuild_closed_shifts)}, '
            f'auto_close_due_shifts={bool(args.auto_close_due_shifts)}.'
        )

        async with AsyncSessionLocal() as db:
            async def cache_progress(event: str, data: dict[str, Any]) -> None:
                location_name = data.get('location') or 'все точки'
                if event == 'start':
                    total = data.get('total_locations') or 0
                    stage['message'] = f'поиск точек и подготовка выгрузки, точек: {total}'
                    _log(f'Найдено точек для обработки: {total}.')
                elif event == 'location_start':
                    stage['message'] = f'подготовка точки {location_name} ({data.get("index")}/{data.get("total")})'
                    _log(f'Точка {data.get("index")}/{data.get("total")}: {location_name}.')
                elif event == 'location_loading':
                    stage['message'] = f'выгрузка продаж/возвратов из МойСклад для {location_name}'
                    _log(f'Выгружаю продажи и возвраты из МойСклад для точки {location_name}...')
                elif event == 'location_skipped':
                    stage['message'] = f'точка {location_name} пропущена'
                    _log(f'Точка {location_name} пропущена: {data.get("reason")}.')
                elif event == 'location_done':
                    stage['message'] = f'точка {location_name} обработана'
                    _log(
                        f'Готово по точке {location_name}: дней={data.get("days")}, '
                        f'продажи={_format_money(data.get("gross_sales_amount"))}, '
                        f'возвраты={_format_money(data.get("return_amount"))}, '
                        f'себестоимость={_format_money(data.get("cost_amount"))}.'
                    )
                elif event == 'done':
                    stage['message'] = 'обновление payroll-кеша завершено'
                    _log(f'Payroll-кеш обновлён. Всего дней в результате: {data.get("total_days")}.')

            payload['cache_refresh'] = await refresh_payroll_metrics_cache(
                date_from,
                date_to,
                db,
                location=args.location,
                force_refresh=bool(args.force_refresh),
                progress_callback=cache_progress,
            )

            if args.auto_close_due_shifts:
                _log('Начинаю автоматическое закрытие незакрытых смен за период...')
                stage['message'] = 'автоматическое закрытие смен'

                async def close_progress(current: int, total: int, shift: Any) -> None:
                    shift_date = getattr(shift, 'shift_date', None)
                    stage['message'] = f'автозакрытие смен {current}/{total}'
                    _log(f'Автозакрытие смены {current}/{total}: {shift_date}.')

                payload['auto_close_due_shifts'] = await auto_close_due_shifts(
                    date_from,
                    date_to,
                    db,
                    location=args.location,
                    progress_callback=close_progress,
                )
                _log(f'Автозакрытие завершено. Закрыто смен: {payload["auto_close_due_shifts"].get("closed", 0)}.')

            if args.rebuild_closed_shifts:
                _log('Начинаю пересборку закрытых смен...')
                stage['message'] = 'подготовка пересборки закрытых смен'

                async def rebuild_progress(current: int, total: int) -> None:
                    stage['message'] = f'пересборка закрытых смен {current}/{total}'
                    _log(f'Пересобраны закрытые смены: {current}/{total}.')

                payload['closed_shift_snapshots'] = await rebuild_closed_shift_snapshots(
                    date_from,
                    date_to,
                    db,
                    location=args.location,
                    # Кеш уже обновили строкой выше, поэтому второй принудительный проход не нужен.
                    force_refresh_metrics=False,
                    progress_callback=rebuild_progress,
                )
                _log('Пересборка закрытых смен завершена.')

        stage['message'] = 'готово'
        _log('Готово. Итоговый JSON ниже.')
        print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    finally:
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task


if __name__ == '__main__':
    asyncio.run(_run())
