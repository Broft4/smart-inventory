from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime

from app.payroll import refresh_current_open_shift_metrics


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Однократно обновляет payroll-метрики текущих открытых смен из МойСклад.'
    )
    parser.add_argument(
        '--reason',
        type=str,
        default='cli',
        help='Метка причины запуска для логов. По умолчанию cli.',
    )
    return parser


def _log(message: str) -> None:
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{timestamp}] {message}', flush=True)


async def _run() -> None:
    args = _build_parser().parse_args()
    _log('Старт обновления текущих открытых смен.')
    result = await refresh_current_open_shift_metrics(reason=args.reason)
    _log('Обновление текущих открытых смен завершено.')
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    asyncio.run(_run())
