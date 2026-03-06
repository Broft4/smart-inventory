from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CheckResult, Report
from app.schemas import (
    AdminReport,
    CategoryModel,
    CategoryResult,
    DiscrepancyItem,
    InventoryStructureResponse,
    ItemModel,
    ReportHistoryItem,
    ReportHistoryResponse,
    StatusEnum,
    SubcategoryModel,
    VerifyRequest,
    VerifyResponse,
)


MOCK_INVENTORY = {
    "Дубна": {
        "store_id": "store-dubna-mock",
        "categories": [
            {
                "id": "cat-drinks",
                "name": "Напитки",
                "subcategories": [
                    {
                        "id": "sub-cola",
                        "name": "Газировка",
                        "items": [
                            {"id": "item-cola-05", "name": "Кола 0.5", "uom": "шт", "expected_qty": 6},
                            {"id": "item-fanta-05", "name": "Фанта 0.5", "uom": "шт", "expected_qty": 4},
                        ],
                    },
                    {
                        "id": "sub-juice",
                        "name": "Соки",
                        "items": [
                            {"id": "item-apple-1", "name": "Сок яблочный 1л", "uom": "шт", "expected_qty": 5},
                            {"id": "item-orange-1", "name": "Сок апельсиновый 1л", "uom": "шт", "expected_qty": 3},
                        ],
                    },
                ],
            },
            {
                "id": "cat-snacks",
                "name": "Снеки",
                "subcategories": [
                    {
                        "id": "sub-chips",
                        "name": "Чипсы",
                        "items": [
                            {"id": "item-lays-crab", "name": "Lays Краб", "uom": "шт", "expected_qty": 7},
                            {"id": "item-lays-cheese", "name": "Lays Сыр", "uom": "шт", "expected_qty": 5},
                        ],
                    },
                    {
                        "id": "sub-rusks",
                        "name": "Сухарики",
                        "items": [
                            {"id": "item-rusks-cold", "name": "Сухарики Холодец", "uom": "шт", "expected_qty": 4},
                            {"id": "item-rusks-bacon", "name": "Сухарики Бекон", "uom": "шт", "expected_qty": 6},
                        ],
                    },
                ],
            },
        ],
    },
    "Дмитров": {
        "store_id": "store-dmitrov-mock",
        "categories": [
            {
                "id": "cat-coffee",
                "name": "Кофе",
                "subcategories": [
                    {
                        "id": "sub-cold-coffee",
                        "name": "Холодный кофе",
                        "items": [
                            {"id": "item-ice-latte", "name": "Айс латте", "uom": "шт", "expected_qty": 8},
                            {"id": "item-ice-cappu", "name": "Айс капучино", "uom": "шт", "expected_qty": 5},
                        ],
                    }
                ],
            },
            {
                "id": "cat-food",
                "name": "Еда",
                "subcategories": [
                    {
                        "id": "sub-shawarma",
                        "name": "Шаурма",
                        "items": [
                            {"id": "item-chicken", "name": "Шаурма с курицей", "uom": "шт", "expected_qty": 9},
                            {"id": "item-cheese", "name": "Шаурма сырная", "uom": "шт", "expected_qty": 3},
                        ],
                    },
                    {
                        "id": "sub-sandwich",
                        "name": "Сэндвичи",
                        "items": [
                            {"id": "item-ham", "name": "Сэндвич с ветчиной", "uom": "шт", "expected_qty": 4},
                            {"id": "item-tuna", "name": "Сэндвич с тунцом", "uom": "шт", "expected_qty": 2},
                        ],
                    },
                ],
            },
        ],
    },
}

def _format_moscow_datetime(dt) -> str:
    if dt is None:
        return "-"

    return (dt + timedelta(hours=3)).strftime("%d.%m.%Y %H:%M")

def _normalize_location(location: str) -> str:
    value = (location or "").strip().lower()
    if value == "дмитров":
        return "Дмитров"
    return "Дубна"


def _inventory_for(location: str) -> dict:
    normalized = _normalize_location(location)
    return MOCK_INVENTORY[normalized]


def _iter_subcategories(location: str):
    data = _inventory_for(location)
    for category in data["categories"]:
        for subcategory in category["subcategories"]:
            yield category, subcategory


def _find_target(location: str, target_id: str, target_type: str) -> dict | None:
    for category, subcategory in _iter_subcategories(location):
        if target_type == "subcategory" and subcategory["id"] == target_id:
            expected_qty = sum(item["expected_qty"] for item in subcategory["items"])
            return {
                "category": category,
                "subcategory": subcategory,
                "target_name": subcategory["name"],
                "expected_qty": expected_qty,
            }
        if target_type == "item":
            for item in subcategory["items"]:
                if item["id"] == target_id:
                    return {
                        "category": category,
                        "subcategory": subcategory,
                        "item": item,
                        "target_name": item["name"],
                        "expected_qty": float(item["expected_qty"]),
                    }
    return None


def _status_from_value(value: str | None) -> StatusEnum:
    try:
        return StatusEnum(value or StatusEnum.GREY.value)
    except ValueError:
        return StatusEnum.GREY


def _report_status_label(status: str) -> str:
    if status == "completed":
        return "завершена"
    if status == "in_progress":
        return "в процессе"
    return status or "-"


async def _get_or_create_active_report(location: str, db: AsyncSession) -> Report:
    normalized = _normalize_location(location)
    stmt = (
        select(Report)
        .where(Report.location == normalized, Report.status == "in_progress")
        .order_by(Report.id.desc())
        .limit(1)
    )
    report = await db.scalar(stmt)
    if report:
        return report

    inventory = _inventory_for(normalized)
    report = Report(location=normalized, store_id=inventory["store_id"], status="in_progress")
    db.add(report)
    await db.commit()
    await db.refresh(report)
    return report


async def _fetch_results_for_report(report_id: int, db: AsyncSession) -> list[CheckResult]:
    stmt: Select[tuple[CheckResult]] = select(CheckResult).where(CheckResult.report_id == report_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_inventory_data(location_name: str, db: AsyncSession) -> InventoryStructureResponse:
    normalized = _normalize_location(location_name)
    inventory = _inventory_for(normalized)
    report = await _get_or_create_active_report(normalized, db)
    results = await _fetch_results_for_report(report.id, db)
    result_by_target = {row.target_id: row for row in results}

    completed_subcategory_ids = {
        row.subcategory_id
        for row in results
        if row.target_type == "subcategory" and row.status in {StatusEnum.GREEN.value, StatusEnum.RED.value}
    }

    raw_categories = inventory["categories"]
    active_category_index = next(
        (
            idx
            for idx, category in enumerate(raw_categories)
            if not all(sub["id"] in completed_subcategory_ids for sub in category["subcategories"])
        ),
        None,
    )

    categories: list[CategoryModel] = []

    for idx, category in enumerate(raw_categories):
        category_completed = all(
            sub["id"] in completed_subcategory_ids for sub in category["subcategories"]
        ) if category["subcategories"] else False
        category_locked = active_category_index is not None and idx > active_category_index
        category_expanded = active_category_index is not None and idx == active_category_index

        local_first_open_subcategory_id = next(
            (
                sub["id"]
                for sub in category["subcategories"]
                if sub["id"] not in completed_subcategory_ids
            ),
            None,
        )

        subcategories: list[SubcategoryModel] = []
        category_statuses: list[StatusEnum] = []

        for subcategory in category["subcategories"]:
            sub_row = result_by_target.get(subcategory["id"])
            sub_status = _status_from_value(sub_row.status if sub_row else None)
            is_completed = sub_status in {StatusEnum.GREEN, StatusEnum.RED}

            items: list[ItemModel] = []
            for item in subcategory["items"]:
                item_row = result_by_target.get(item["id"])
                item_status = _status_from_value(item_row.status if item_row else None)
                items.append(
                    ItemModel(
                        id=item["id"],
                        name=item["name"],
                        uom=item.get("uom", "шт"),
                        status=item_status,
                        entered_quantity=(item_row.actual_qty if item_row and item_status != StatusEnum.GREY else None),
                    )
                )

            if category_locked:
                is_locked = True
            elif category_completed:
                is_locked = False
            else:
                is_locked = not is_completed and subcategory["id"] != local_first_open_subcategory_id

            is_expanded = False
            if not category_locked:
                if sub_status == StatusEnum.ORANGE:
                    is_expanded = True
                elif not category_completed and subcategory["id"] == local_first_open_subcategory_id:
                    is_expanded = True

            subcategories.append(
                SubcategoryModel(
                    id=subcategory["id"],
                    name=subcategory["name"],
                    status=sub_status,
                    is_locked=is_locked,
                    is_completed=is_completed,
                    is_expanded=is_expanded,
                    items=items,
                    entered_quantity=(sub_row.actual_qty if sub_row and sub_status != StatusEnum.GREY else None),
                )
            )
            category_statuses.append(sub_status)

        category_status = StatusEnum.GREY
        if category_statuses:
            if all(status == StatusEnum.GREEN for status in category_statuses):
                category_status = StatusEnum.GREEN
            elif all(status in {StatusEnum.GREEN, StatusEnum.RED} for status in category_statuses) and any(
                status == StatusEnum.RED for status in category_statuses
            ):
                category_status = StatusEnum.RED
            elif any(status == StatusEnum.ORANGE for status in category_statuses):
                category_status = StatusEnum.ORANGE

        categories.append(
            CategoryModel(
                id=category["id"],
                name=category["name"],
                status=category_status,
                is_locked=category_locked,
                is_completed=category_completed,
                is_expanded=category_expanded,
                subcategories=subcategories,
            )
        )

    return InventoryStructureResponse(
        location=normalized,
        store_id=inventory["store_id"],
        report_id=report.id,
        categories=categories,
    )


async def _get_check_result(report_id: int, target_id: str, db: AsyncSession) -> CheckResult | None:
    stmt = select(CheckResult).where(CheckResult.report_id == report_id, CheckResult.target_id == target_id)
    return await db.scalar(stmt)


async def _ensure_check_result(report: Report, target_meta: dict, target_id: str, target_type: str, db: AsyncSession) -> CheckResult:
    existing = await _get_check_result(report.id, target_id, db)
    if existing:
        return existing

    row = CheckResult(
        report_id=report.id,
        category_id=target_meta["category"]["id"],
        category_name=target_meta["category"]["name"],
        subcategory_id=target_meta["subcategory"]["id"],
        subcategory_name=target_meta["subcategory"]["name"],
        target_type=target_type,
        target_id=target_id,
        target_name=target_meta["target_name"],
        expected_qty=float(target_meta["expected_qty"]),
        status=StatusEnum.GREY.value,
    )
    db.add(row)
    await db.flush()
    return row


async def _get_subcategory_item_rows(report_id: int, subcategory_id: str, db: AsyncSession) -> list[CheckResult]:
    stmt = select(CheckResult).where(
        CheckResult.report_id == report_id,
        CheckResult.subcategory_id == subcategory_id,
        CheckResult.target_type == "item",
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def _finalize_subcategory_by_items(report: Report, subcategory_id: str, db: AsyncSession) -> bool:
    target_meta = _find_target(report.location, subcategory_id, "subcategory")
    if not target_meta:
        return False

    item_rows = await _get_subcategory_item_rows(report.id, subcategory_id, db)
    expected_item_ids = {item["id"] for item in target_meta["subcategory"]["items"]}
    completed_item_ids = {
        row.target_id
        for row in item_rows
        if row.status in {StatusEnum.GREEN.value, StatusEnum.RED.value}
    }
    if expected_item_ids != completed_item_ids:
        return False

    sub_row = await _ensure_check_result(report, target_meta, subcategory_id, "subcategory", db)
    total_actual = sum((row.actual_qty or 0) for row in item_rows)
    total_expected = sum((row.expected_qty or 0) for row in item_rows)
    total_diff = total_actual - total_expected
    all_green = all(row.status == StatusEnum.GREEN.value for row in item_rows)

    sub_row.actual_qty = float(total_actual)
    sub_row.expected_qty = float(total_expected)
    sub_row.diff = float(total_diff)
    sub_row.status = StatusEnum.GREEN.value if all_green else StatusEnum.RED.value
    await db.flush()
    return True


async def verify_item_or_category(data: VerifyRequest, db: AsyncSession) -> VerifyResponse:
    report = await db.get(Report, data.report_id)
    if not report:
        return VerifyResponse(
            is_correct=False,
            attempts_left=0,
            message="Отчет не найден. Начните ревизию заново.",
            expand_items=False,
            subcategory_completed=False,
            target_status=StatusEnum.RED,
        )

    target_meta = _find_target(report.location, data.target_id, data.target_type)
    if not target_meta:
        return VerifyResponse(
            is_correct=False,
            attempts_left=0,
            message="Цель проверки не найдена в структуре точки.",
            expand_items=False,
            subcategory_completed=False,
            target_status=StatusEnum.RED,
        )

    row = await _ensure_check_result(report, target_meta, data.target_id, data.target_type, db)

    if row.status in {StatusEnum.GREEN.value, StatusEnum.RED.value}:
        return VerifyResponse(
            is_correct=row.status == StatusEnum.GREEN.value,
            attempts_left=0,
            message="Этот пункт уже зафиксирован.",
            expand_items=False,
            subcategory_completed=data.target_type == "subcategory",
            target_status=_status_from_value(row.status),
        )

    row.attempts_used += 1
    row.actual_qty = float(data.quantity)
    row.diff = float(data.quantity - row.expected_qty)

    if float(data.quantity) == float(row.expected_qty):
        row.status = StatusEnum.GREEN.value
        subcategory_completed = False
        if data.target_type == "item":
            subcategory_completed = await _finalize_subcategory_by_items(report, row.subcategory_id, db)
        await db.commit()
        return VerifyResponse(
            is_correct=True,
            attempts_left=max(0, 3 - row.attempts_used),
            message="Верно. Значение зафиксировано.",
            expand_items=False,
            subcategory_completed=subcategory_completed or data.target_type == "subcategory",
            target_status=StatusEnum.GREEN,
        )

    attempts_left = max(0, 3 - row.attempts_used)
    if attempts_left > 0:
        row.status = StatusEnum.GREY.value
        await db.commit()
        return VerifyResponse(
            is_correct=False,
            attempts_left=attempts_left,
            message=f"Неверно. Осталось попыток: {attempts_left}.",
            expand_items=False,
            subcategory_completed=False,
            target_status=StatusEnum.GREY,
        )

    if data.target_type == "subcategory":
        row.status = StatusEnum.ORANGE.value
        await db.commit()
        return VerifyResponse(
            is_correct=False,
            attempts_left=0,
            message="Расхождение по подкатегории. Переходим к поштучной проверке.",
            expand_items=True,
            subcategory_completed=False,
            target_status=StatusEnum.ORANGE,
        )

    row.status = StatusEnum.RED.value
    subcategory_completed = await _finalize_subcategory_by_items(report, row.subcategory_id, db)
    await db.commit()
    return VerifyResponse(
        is_correct=False,
        attempts_left=0,
        message="Расхождение по товару зафиксировано.",
        expand_items=False,
        subcategory_completed=subcategory_completed,
        target_status=StatusEnum.RED,
    )


async def _report_all_categories_completed(report: Report, db: AsyncSession) -> bool:
    inventory = _inventory_for(report.location)
    expected_subcategory_ids = {
        sub["id"]
        for category in inventory["categories"]
        for sub in category["subcategories"]
    }
    if not expected_subcategory_ids:
        return False

    stmt = select(CheckResult).where(
        CheckResult.report_id == report.id,
        CheckResult.target_type == "subcategory",
        CheckResult.status.in_([StatusEnum.GREEN.value, StatusEnum.RED.value]),
    )
    result = await db.execute(stmt)
    completed_subcategory_ids = {row.target_id for row in result.scalars().all()}
    return expected_subcategory_ids.issubset(completed_subcategory_ids)


async def finish_report(report_id: int, db: AsyncSession) -> tuple[bool, str]:
    report = await db.get(Report, report_id)
    if not report:
        return False, "Отчет не найден."

    if report.status == "completed":
        return True, "Ревизия уже была завершена."

    if not await _report_all_categories_completed(report, db):
        return False, "Нельзя завершить ревизию, пока не пройдены все категории."

    report.status = "completed"
    await db.commit()
    return True, "Ревизия завершена."


async def delete_report(report_id: int, location: str, db: AsyncSession) -> tuple[bool, str]:
    normalized = _normalize_location(location)
    report = await db.get(Report, report_id)
    if not report:
        return False, "Ревизия не найдена."

    if report.location != normalized:
        return False, "Эта ревизия относится к другой точке."

    await db.delete(report)
    await db.commit()
    return True, "Ревизия удалена."


async def get_reports_history(location: str, db: AsyncSession) -> ReportHistoryResponse:
    normalized = _normalize_location(location)
    stmt = select(Report).where(Report.location == normalized).order_by(Report.id.desc())
    result = await db.execute(stmt)
    reports = list(result.scalars().all())

    return ReportHistoryResponse(
        location=normalized,
        reports=[
            ReportHistoryItem(
                report_id=report.id,
                date=_format_moscow_datetime(report.date_created),
                status=report.status,
                label=f"{_format_moscow_datetime(report.date_created)} — {_report_status_label(report.status)}",
            )
            for report in reports
        ],
    )


async def get_admin_report(location: str, db: AsyncSession, report_id: int | None = None) -> AdminReport:
    normalized = _normalize_location(location)

    report: Report | None = None
    if report_id is not None:
        report = await db.get(Report, report_id)
        if report and report.location != normalized:
            report = None

    if report is None:
        stmt = select(Report).where(Report.location == normalized).order_by(Report.id.desc()).limit(1)
        report = await db.scalar(stmt)

    if not report:
        return AdminReport(
            report_id=None,
            date="-",
            location=normalized,
            status="-",
            categories=[],
            total_plus=0.0,
            total_minus=0.0,
        )

    results = await _fetch_results_for_report(report.id, db)
    subcategory_rows = [
        row for row in results if row.target_type == "subcategory" and row.status in {"green", "red", "orange"}
    ]
    item_rows = [row for row in results if row.target_type == "item" and row.status == "red"]

    grouped_problem_items: dict[str, list[DiscrepancyItem]] = defaultdict(list)
    for row in item_rows:
        grouped_problem_items[row.category_name].append(
            DiscrepancyItem(
                name=row.target_name,
                expected=float(row.expected_qty),
                actual=float(row.actual_qty or 0),
                diff=float(row.diff or 0),
            )
        )

    category_status_map: dict[str, StatusEnum] = defaultdict(lambda: StatusEnum.GREY)
    for row in subcategory_rows:
        current = category_status_map[row.category_name]
        row_status = _status_from_value(row.status)
        if row_status == StatusEnum.RED:
            category_status_map[row.category_name] = StatusEnum.RED
        elif row_status == StatusEnum.ORANGE and current != StatusEnum.RED:
            category_status_map[row.category_name] = StatusEnum.ORANGE
        elif row_status == StatusEnum.GREEN and current == StatusEnum.GREY:
            category_status_map[row.category_name] = StatusEnum.GREEN

    categories = []
    ordered_category_names = [cat["name"] for cat in _inventory_for(report.location)["categories"]]
    for category_name in ordered_category_names:
        categories.append(
            CategoryResult(
                name=category_name,
                status=category_status_map[category_name],
                problem_items=grouped_problem_items.get(category_name, []),
            )
        )

    total_plus = sum(max(float(row.diff or 0), 0.0) for row in item_rows)
    total_minus = abs(sum(min(float(row.diff or 0), 0.0) for row in item_rows))

    return AdminReport(
        report_id=report.id,
        date=_format_moscow_datetime(report.date_created),
        location=report.location,
        status=_report_status_label(report.status),
        categories=categories,
        total_plus=float(total_plus),
        total_minus=float(total_minus),
    )
