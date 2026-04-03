from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import LocationPoint
from app.payroll import (
    _doc_matches_point,
    _extract_document_day,
    _extract_position_cost_amount,
    _extract_shift_cost_amount,
    _extract_shift_profit_amount,
    _extract_shift_return_amount,
    _extract_shift_sales_amount,
    _fetch_document_rows,
    _fetch_retail_shift_rows,
    _iter_positions,
    _load_point_sales_metrics_live,
    _load_profitability_metrics_by_day,
    _normalize_location,
)


def _parse_iso_date(value: str) -> date:
    return date.fromisoformat(str(value).strip())


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Сохраняет raw-ответы по retailshift/retaildemand/retailsalesreturn и извлеченные метрики.')
    parser.add_argument('--location', required=True, help='Название точки из БД.')
    parser.add_argument('--date-from', required=True, help='Начальная дата YYYY-MM-DD.')
    parser.add_argument('--date-to', default=None, help='Конечная дата YYYY-MM-DD. По умолчанию равна --date-from.')
    parser.add_argument('--output', default=None, help='Файл для сохранения JSON-дампа.')
    return parser


async def _load_point(location: str) -> LocationPoint:
    async with AsyncSessionLocal() as db:
        point = await db.scalar(select(LocationPoint).where(LocationPoint.name == _normalize_location(location)).limit(1))
        if not point:
            raise SystemExit(f'Точка "{location}" не найдена в БД.')
        return point


def _serialize_doc(doc: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(doc, ensure_ascii=False, default=str))


async def _run() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    date_from = _parse_iso_date(args.date_from)
    date_to = _parse_iso_date(args.date_to) if args.date_to else date_from
    if date_from > date_to:
        raise SystemExit('--date-from не может быть позже --date-to.')

    point = await _load_point(args.location)

    sales_result, return_result, shift_result = await asyncio.gather(
        _fetch_document_rows(
            'retaildemand',
            date_from,
            date_to,
            point,
            expand='store,retailStore,retailShift,retailShift.store,retailShift.retailStore',
            include_positions=True,
            positions_expand='assortment,assortment.productFolder',
        ),
        _fetch_document_rows(
            'retailsalesreturn',
            date_from,
            date_to,
            point,
            expand='store,retailStore,retailShift,retailShift.store,retailShift.retailStore,demand,demand.store,demand.retailStore,demand.retailShift,demand.retailShift.store,demand.retailShift.retailStore',
            include_positions=True,
            positions_expand='assortment,assortment.productFolder',
        ),
        _fetch_retail_shift_rows(date_from, date_to, point),
        return_exceptions=True,
    )

    errors: dict[str, str] = {}
    sales_rows: list[dict[str, Any]] = []
    return_rows: list[dict[str, Any]] = []
    shift_rows: list[dict[str, Any]] = []

    if isinstance(sales_result, Exception):
        errors['retaildemand'] = repr(sales_result)
    else:
        sales_rows = [row for row in sales_result if _doc_matches_point(row, point)]

    if isinstance(return_result, Exception):
        errors['retailsalesreturn'] = repr(return_result)
    else:
        return_rows = [row for row in return_result if _doc_matches_point(row, point)]

    if isinstance(shift_result, Exception):
        errors['retailshift'] = repr(shift_result)
    else:
        shift_rows = [row for row in shift_result if _doc_matches_point(row, point)]

    live_metrics: dict[date, dict[str, Any]] = {}
    profitability_by_day: dict[date, Any] = {}
    if errors:
        logging.warning('Пропускаем повторный live-расчёт в диагностическом дампе, потому что часть запросов уже завершилась ошибкой: %s', ', '.join(sorted(errors)))
    else:
        live_metrics = await _load_point_sales_metrics_live(point, date_from, date_to)
        profitability_by_day = await _load_profitability_metrics_by_day(point, date_from, date_to)
    shift_diagnostics: list[dict[str, Any]] = []
    for row in shift_rows:
        shift_day = _extract_document_day(row)
        shift_cost_amount = _extract_shift_cost_amount(row)
        shift_profit_amount = _extract_shift_profit_amount(row)
        shift_diagnostics.append({
            'day': shift_day.isoformat() if shift_day else None,
            'id': row.get('id'),
            'name': row.get('name'),
            'sales_amount': _extract_shift_sales_amount(row),
            'return_amount': _extract_shift_return_amount(row),
            'cost_amount': shift_cost_amount,
            'cost_source': None,
            'profit_amount': shift_profit_amount,
            'profit_source': None,
        })

    position_cost_samples: list[dict[str, Any]] = []
    for collection_name, rows in (('retaildemand', sales_rows), ('retailsalesreturn', return_rows)):
        for row in rows:
            row_day = _extract_document_day(row)
            for position in _iter_positions(row):
                position_cost_samples.append({
                    'collection': collection_name,
                    'day': row_day.isoformat() if row_day else None,
                    'document_id': row.get('id'),
                    'assortment_id': (position.get('assortment') or {}).get('id') if isinstance(position.get('assortment'), dict) else None,
                    'quantity': position.get('quantity'),
                    'sum': position.get('sum'),
                    'price': position.get('price'),
                    'cost_amount_extracted': _extract_position_cost_amount(position),
                    'position_keys': sorted(position.keys()),
                })
                if len(position_cost_samples) >= 200:
                    break
            if len(position_cost_samples) >= 200:
                break
        if len(position_cost_samples) >= 200:
            break

    profit_report_summary = {
        day.isoformat(): {
            'sales_amount': metrics.sales_amount,
            'return_amount': metrics.return_amount,
            'cost_amount': metrics.cost_amount,
            'gross_profit_amount': metrics.gross_profit_amount,
            'has_rows': metrics.has_rows,
        }
        for day, metrics in profitability_by_day.items()
    }

    payload = {
        'location': point.name,
        'date_from': date_from.isoformat(),
        'date_to': date_to.isoformat(),
        'errors': errors,
        'counts': {
            'retailshift': len(shift_rows),
            'retaildemand': len(sales_rows),
            'retailsalesreturn': len(return_rows),
        },
        'live_metrics': {
            day.isoformat(): metrics
            for day, metrics in live_metrics.items()
        },
        'profit_report_summary': profit_report_summary,
        'shift_diagnostics': shift_diagnostics,
        'position_cost_samples': position_cost_samples,
        'raw': {
            'retailshift': [_serialize_doc(row) for row in shift_rows],
            'retaildemand': [_serialize_doc(row) for row in sales_rows],
            'retailsalesreturn': [_serialize_doc(row) for row in return_rows],
        },
    }

    output_path = Path(args.output) if args.output else Path(
        f'debug_moysklad_{point.name.lower().replace(" ", "_")}_{date_from.isoformat()}_{date_to.isoformat()}.json'
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print(output_path.as_posix())


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run())
