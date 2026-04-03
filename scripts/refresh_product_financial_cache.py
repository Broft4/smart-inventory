from __future__ import annotations

import argparse
import asyncio
import json
import logging

from app.database import AsyncSessionLocal
from app.logic import refresh_product_financial_cache

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Обновляет локальный кеш себестоимости и цен товаров из МоегоСклада.')
    parser.add_argument('--location', type=str, default=None, help='Название точки. Если не указано, обновляются все точки.')
    parser.add_argument('--force-refresh', action='store_true', help='Принудительно обновить значения и отметки синхронизации.')
    return parser


async def _run() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    async with AsyncSessionLocal() as db:
        result = await refresh_product_financial_cache(
            db,
            location=args.location,
            force_refresh=bool(args.force_refresh),
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run())
