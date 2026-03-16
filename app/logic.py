from __future__ import annotations

import hashlib
import httpx
import hmac
import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import and_, delete, func, inspect, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.moysklad import DEFAULT_CATEGORY_NAME, DEFAULT_SUBCATEGORY_NAME, ms_client
from app.models import CategoryAssignment, CheckResult, LocationPoint, Report, SelectionCycle, SelectionTarget, User
from app.schemas import (
    AdminCycleTargetCategory,
    AdminCycleTargetItem,
    AdminCycleTargetsResponse,
    AdminReport,
    AssignSelectionResponse,
    CategoryModel,
    CategoryResult,
    CreateLocationRequest,
    CreateLocationResponse,
    UpdateLocationRequest,
    UpdateLocationResponse,
    DeleteResponse,
    DiscrepancyItem,
    EmployeeReportSummary,
    InventoryStructureResponse,
    ItemModel,
    LocationListResponse,
    LocationPointModel,
    MeResponse,
    ReportHistoryItem,
    ReportHistoryResponse,
    ResetSelectionCycleResponse,
    SaveCycleTargetsRequest,
    SaveCycleTargetsResponse,
    RoleEnum,
    StatusEnum,
    StoreListResponse,
    StoreOption,
    SubcategoryModel,
    UserActionResponse,
    UserCreateRequest,
    UserInfo,
    UserListResponse,
    UserResponse,
    UserUpdateRequest,
    VerifyRequest,
    VerifyResponse,
)

MOCK_INVENTORY: dict[str, dict[str, Any]] = {
    'Дубна': {
        'categories': [
            {
                'id': 'cat-drinks',
                'name': 'Напитки',
                'subcategories': [
                    {
                        'id': 'sub-soda',
                        'name': 'Газировка',
                        'items': [
                            {'id': 'dub-cola', 'name': 'Кола 0.5', 'expected_qty': 6},
                            {'id': 'dub-fanta', 'name': 'Фанта 0.5', 'expected_qty': 4},
                        ],
                    },
                    {
                        'id': 'sub-juice',
                        'name': 'Соки',
                        'items': [
                            {'id': 'dub-apple', 'name': 'Сок яблочный 1л', 'expected_qty': 5},
                            {'id': 'dub-orange', 'name': 'Сок апельсиновый 1л', 'expected_qty': 3},
                        ],
                    },
                ],
            },
            {
                'id': 'cat-snacks',
                'name': 'Снеки',
                'subcategories': [
                    {
                        'id': 'sub-chips',
                        'name': 'Чипсы',
                        'items': [
                            {'id': 'dub-chips-crab', 'name': 'Lays Краб', 'expected_qty': 7},
                            {'id': 'dub-chips-cheese', 'name': 'Lays Сыр', 'expected_qty': 5},
                        ],
                    },
                    {
                        'id': 'sub-croutons',
                        'name': 'Сухарики',
                        'items': [
                            {'id': 'dub-croutons-jelly', 'name': 'Сухарики Холодец', 'expected_qty': 4},
                            {'id': 'dub-croutons-bacon', 'name': 'Сухарики Бекон', 'expected_qty': 6},
                        ],
                    },
                ],
            },
        ]
    },
    'Дмитров': {
        'categories': [
            {
                'id': 'cat-coffee',
                'name': 'Кофе',
                'subcategories': [
                    {
                        'id': 'sub-ice-coffee',
                        'name': 'Холодный кофе',
                        'items': [
                            {'id': 'dm-ice-latte', 'name': 'Айс латте', 'expected_qty': 8},
                            {'id': 'dm-ice-capp', 'name': 'Айс капучино', 'expected_qty': 5},
                        ],
                    }
                ],
            },
            {
                'id': 'cat-food',
                'name': 'Еда',
                'subcategories': [
                    {
                        'id': 'sub-shawarma',
                        'name': 'Шаурма',
                        'items': [
                            {'id': 'dm-shawarma-chicken', 'name': 'Шаурма с курицей', 'expected_qty': 9},
                            {'id': 'dm-shawarma-cheese', 'name': 'Шаурма сырная', 'expected_qty': 3},
                        ],
                    },
                    {
                        'id': 'sub-sandwich',
                        'name': 'Сэндвичи',
                        'items': [
                            {'id': 'dm-sandwich-ham', 'name': 'Сэндвич с ветчиной', 'expected_qty': 4},
                            {'id': 'dm-sandwich-tuna', 'name': 'Сэндвич с тунцом', 'expected_qty': 2},
                        ],
                    },
                ],
            },
        ]
    },
}

MSK_SHIFT = timedelta(hours=3)
SELECTION_CYCLE_DAYS = 15

MSK_TZ = timezone(MSK_SHIFT)

def get_moscow_today() -> date:
    return datetime.now(MSK_TZ).date()


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 200_000)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt_hex, digest_hex = stored_hash.split('$', 1)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt_hex), 200_000)
    return hmac.compare_digest(digest.hex(), digest_hex)


def _normalize_location(location: str) -> str:
    return location.strip().title()


def _format_moscow_datetime(dt: datetime | None) -> str:
    if not dt:
        return '-'
    return (dt + MSK_SHIFT).strftime('%d.%m.%Y %H:%M')


def _report_status_label(status: str) -> str:
    if status == 'created':
        return 'Создана'
    if status == 'completed':
        return 'Завершена'
    return 'В процессе'


def bootstrap_schema_and_admin(sync_conn) -> None:
    inspector = inspect(sync_conn)
    tables = set(inspector.get_table_names())

    if 'users' in tables:
        columns = {c['name'] for c in inspector.get_columns('users')}
        required = {'id', 'full_name', 'birth_date', 'username', 'password_hash', 'role', 'location', 'is_active', 'created_at'}
        if not required.issubset(columns):
            sync_conn.execute(text('DROP TABLE IF EXISTS users'))

    reset_reports = False
    if 'reports' in tables:
        cols = {c['name'] for c in inspector.get_columns('reports')}
        if not {'id', 'location', 'report_date', 'cycle_version', 'status', 'date_created'}.issubset(cols):
            reset_reports = True

    if 'check_results' in tables:
        cols = {c['name'] for c in inspector.get_columns('check_results')}
        required = {'id', 'report_id', 'category_id', 'category_name', 'subcategory_id', 'subcategory_name', 'target_type', 'target_id', 'target_name', 'expected_qty', 'actual_qty', 'diff', 'status', 'attempts_used', 'checked_by_user_id', 'checked_by_name_snapshot', 'created_at'}
        if not required.issubset(cols):
            reset_reports = True

    reset_assignments = False
    if 'category_assignments' in tables:
        cols = {c['name'] for c in inspector.get_columns('category_assignments')}
        required = {'id', 'location', 'cycle_version', 'category_id', 'category_name', 'subcategory_id', 'subcategory_name', 'target_type', 'target_id', 'target_name', 'user_id', 'user_full_name_snapshot', 'assigned_at'}
        if not required.issubset(cols):
            reset_assignments = True

    if 'selection_cycles' in tables:
        cols = {c['name'] for c in inspector.get_columns('selection_cycles')}
        required = {'id', 'location', 'cycle_version', 'started_at', 'updated_at'}
        if not required.issubset(cols):
            reset_assignments = True
    elif 'selection_cycles' not in tables:
        reset_assignments = True if 'category_assignments' in tables else reset_assignments

    if 'selection_targets' in tables:
        cols = {c['name'] for c in inspector.get_columns('selection_targets')}
        required = {'id', 'location', 'cycle_version', 'category_id', 'category_name', 'subcategory_id', 'subcategory_name', 'target_type', 'target_id', 'target_name', 'created_at'}
        if not required.issubset(cols):
            sync_conn.execute(text('DROP TABLE IF EXISTS selection_targets'))

    if 'location_points' in tables:
        cols = {c['name'] for c in inspector.get_columns('location_points')}
        required = {'id', 'name', 'ms_token', 'ms_store_id', 'ms_store_name', 'created_at'}
        if not required.issubset(cols):
            sync_conn.execute(text('DROP TABLE IF EXISTS location_points'))

    if reset_reports:
        sync_conn.execute(text('DROP TABLE IF EXISTS check_results'))
        sync_conn.execute(text('DROP TABLE IF EXISTS reports'))

    if reset_assignments:
        sync_conn.execute(text('DROP TABLE IF EXISTS category_assignments'))
        sync_conn.execute(text('DROP TABLE IF EXISTS selection_cycles'))

    from app.database import Base
    Base.metadata.create_all(sync_conn)


async def ensure_default_admin(db: AsyncSession) -> None:
    admin = await db.scalar(select(User).where(User.role == RoleEnum.ADMIN.value).limit(1))
    if admin:
        return

    admin_user = User(
        full_name=settings.default_admin_full_name,
        birth_date=date.fromisoformat(settings.default_admin_birth_date),
        username=settings.default_admin_username,
        password_hash=hash_password(settings.default_admin_password),
        role=RoleEnum.ADMIN.value,
        location=None,
        is_active=True,
    )
    db.add(admin_user)
    await db.commit()


def user_to_schema(user: User) -> UserInfo:
    return UserInfo(
        id=user.id,
        full_name=user.full_name,
        birth_date=user.birth_date,
        username=user.username,
        role=RoleEnum(user.role),
        location=user.location,
        is_active=user.is_active,
    )


async def authenticate_user(username: str, password: str, db: AsyncSession) -> User | None:
    user = await db.scalar(select(User).where(User.username == username).limit(1))
    if not user or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


async def get_me_response(user: User | None) -> MeResponse:
    return MeResponse(authenticated=bool(user), user=user_to_schema(user) if user else None)


async def prewarm_inventory_cache(location: str | None) -> None:
    if not location:
        return
    normalized = _normalize_location(location)
    point = await _get_location_point(normalized)
    if point and point.ms_token and point.ms_store_id:
        await ms_client.prewarm_inventory(normalized, token=point.ms_token, store_id=point.ms_store_id)
        return
    if ms_client.enabled:
        await ms_client.prewarm_inventory(normalized)


async def _get_location_point(location: str) -> LocationPoint | None:
    normalized = _normalize_location(location)
    async with AsyncSessionLocal() as session:
        return await session.scalar(select(LocationPoint).where(LocationPoint.name == normalized).limit(1))


async def list_users(db: AsyncSession) -> UserListResponse:
    rows = await db.scalars(select(User).order_by(User.role.desc(), User.full_name.asc()))
    return UserListResponse(users=[UserResponse.model_validate(user) for user in rows.all()])


async def create_user(payload: UserCreateRequest, db: AsyncSession) -> UserActionResponse:
    existing = await db.scalar(select(User).where(User.username == payload.username).limit(1))
    if existing:
        raise HTTPException(status_code=400, detail='Пользователь с таким логином уже существует.')

    user = User(
        full_name=payload.full_name.strip(),
        birth_date=payload.birth_date,
        username=payload.username.strip(),
        password_hash=hash_password(payload.password),
        role=payload.role.value,
        location=payload.location or None,
        is_active=payload.is_active,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return UserActionResponse(success=True, message='Пользователь создан.', user=UserResponse.model_validate(user))


async def update_user(user_id: int, payload: UserUpdateRequest, db: AsyncSession, current_admin_id: int | None = None) -> UserActionResponse:
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail='Пользователь не найден.')

    duplicate = await db.scalar(select(User).where(User.username == payload.username, User.id != user_id).limit(1))
    if duplicate:
        raise HTTPException(status_code=400, detail='Пользователь с таким логином уже существует.')

    if user.id == current_admin_id and payload.role != RoleEnum.ADMIN:
        raise HTTPException(status_code=400, detail='Нельзя снять роль admin у своего аккаунта.')

    old_name = user.full_name
    user.full_name = payload.full_name.strip()
    user.birth_date = payload.birth_date
    user.username = payload.username.strip()
    user.role = payload.role.value
    user.location = payload.location or None
    user.is_active = payload.is_active
    if payload.password:
        user.password_hash = hash_password(payload.password)

    if old_name != user.full_name:
        await db.execute(update(CategoryAssignment).where(CategoryAssignment.user_id == user.id).values(user_full_name_snapshot=user.full_name))
        await db.execute(update(CheckResult).where(CheckResult.checked_by_user_id == user.id).values(checked_by_name_snapshot=user.full_name))

    await db.commit()
    await db.refresh(user)
    return UserActionResponse(success=True, message='Пользователь обновлён.', user=UserResponse.model_validate(user))


async def delete_user(user_id: int, db: AsyncSession, current_admin_id: int | None = None) -> DeleteResponse:
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail='Пользователь не найден.')

    if user.id == current_admin_id:
        raise HTTPException(status_code=400, detail='Нельзя удалить собственный аккаунт.')

    if user.role == RoleEnum.ADMIN.value:
        admin_count = await db.scalar(select(func.count()).select_from(User).where(User.role == RoleEnum.ADMIN.value))
        if (admin_count or 0) <= 1:
            raise HTTPException(status_code=400, detail='Нельзя удалить последнего администратора.')

    await db.execute(update(CategoryAssignment).where(CategoryAssignment.user_id == user.id).values(user_id=None))
    await db.execute(update(CheckResult).where(CheckResult.checked_by_user_id == user.id).values(checked_by_user_id=None))
    await db.delete(user)
    await db.commit()
    return DeleteResponse(success=True, message='Пользователь удалён.')


async def _get_inventory_for(location: str) -> dict[str, Any]:
    normalized = _normalize_location(location)
    point = await _get_location_point(normalized)

    if point and point.ms_token and point.ms_store_id:
        try:
            return await ms_client.get_inventory(normalized, token=point.ms_token, store_id=point.ms_store_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail='Не удалось получить данные из МойСклад. Попробуйте ещё раз.') from exc

    if ms_client.enabled:
        try:
            return await ms_client.get_inventory(normalized)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail='Не удалось получить данные из МойСклад. Попробуйте ещё раз.') from exc

    if normalized not in MOCK_INVENTORY:
        raise HTTPException(status_code=404, detail='Неизвестная точка.')
    return MOCK_INVENTORY[normalized]


async def _find_category(location: str, category_id: str) -> dict[str, Any]:
    inventory = await _get_inventory_for(location)
    for category in inventory['categories']:
        if category['id'] == category_id:
            return category
    raise HTTPException(status_code=404, detail='Категория не найдена.')


async def _find_subcategory(location: str, category_id: str, subcategory_id: str) -> dict[str, Any]:
    category = await _find_category(location, category_id)
    for sub in category['subcategories']:
        if sub['id'] == subcategory_id:
            return sub
    raise HTTPException(status_code=404, detail='Подкатегория не найдена.')


async def _find_target(location: str, target_id: str) -> tuple[str, str, str | None, str | None, str, str, float]:
    inventory = await _get_inventory_for(location)
    for category in inventory['categories']:
        for subcategory in category['subcategories']:
            if subcategory['id'] == target_id:
                expected_total = float(sum(item['expected_qty'] for item in subcategory['items']))
                return category['id'], category['name'], subcategory['id'], subcategory['name'], 'subcategory', subcategory['name'], expected_total
            for item in subcategory['items']:
                if item['id'] == target_id:
                    return category['id'], category['name'], subcategory['id'], subcategory['name'], 'item', item['name'], float(item['expected_qty'])
    raise HTTPException(status_code=404, detail='Цель проверки не найдена.')


async def get_inventory_diagnostics_details(location: str) -> list[dict[str, Any]]:
    inventory = await _get_inventory_for(location)
    rows: list[dict[str, Any]] = []
    normalized_location = inventory.get('location') or _normalize_location(location)

    for category in inventory['categories']:
        for subcategory in category['subcategories']:
            category_is_default = category['name'] == DEFAULT_CATEGORY_NAME
            subcategory_is_default = subcategory['name'] == DEFAULT_SUBCATEGORY_NAME
            if not (category_is_default or subcategory_is_default):
                continue

            issue_parts: list[str] = []
            if category_is_default:
                issue_parts.append('без категории')
            if subcategory_is_default:
                issue_parts.append('без подкатегории')
            issue_label = ' и '.join(issue_parts)

            for item in subcategory['items']:
                diagnostics = item.get('diagnostics') or {}
                folder_chain = diagnostics.get('folder_chain') or []
                folder_path = ' → '.join(part.get('name', '') for part in folder_chain if part.get('name'))
                rows.append({
                    'location': normalized_location,
                    'issue_type': issue_label,
                    'category_name': category['name'],
                    'subcategory_name': subcategory['name'],
                    'item_id': item['id'],
                    'item_name': item['name'],
                    'expected_qty': item.get('expected_qty', 0),
                    'reason': diagnostics.get('reason') or (
                        'У товара не определилась категория или подкатегория. Проверьте папку товара в МойСклад.'
                        if category_is_default or subcategory_is_default
                        else 'Товар размечен корректно.'
                    ),
                    'folder_path': folder_path or '-',
                    'folder_source': diagnostics.get('folder_source') or '-',
                    'assortment_lookup': diagnostics.get('assortment_lookup') or '-',
                })

    rows.sort(key=lambda row: (str(row['issue_type']).lower(), str(row['category_name']).lower(), str(row['subcategory_name']).lower(), str(row['item_name']).lower()))
    return rows


async def get_inventory_diagnostics_rows(location: str) -> list[dict[str, Any]]:
    rows = await get_inventory_diagnostics_details(location)
    return [
        {
            'location': row['location'],
            'issue_type': row['issue_type'],
            'category_name': row['category_name'],
            'subcategory_name': row['subcategory_name'],
            'item_id': row['item_id'],
            'item_name': row['item_name'],
            'expected_qty': row['expected_qty'],
            'reason': row['reason'],
            'folder_path': row['folder_path'],
            'folder_source': row['folder_source'],
            'assortment_lookup': row['assortment_lookup'],
        }
        for row in rows
    ]


def _subcategory_is_complete(raw_subcategory: dict[str, Any], results_by_target: dict[str, CheckResult]) -> tuple[bool, StatusEnum]:
    sub_row = results_by_target.get(raw_subcategory['id'])
    item_rows = [results_by_target.get(item['id']) for item in raw_subcategory['items']]
    final_item_rows = [row for row in item_rows if row and row.status in {'green', 'red'}]
    has_red_items = any(row and row.status == 'red' for row in item_rows)

    if sub_row and sub_row.status == 'green':
        return True, StatusEnum.GREEN

    if sub_row and sub_row.status == 'orange':
        if len(final_item_rows) == len(raw_subcategory['items']):
            return True, StatusEnum.RED if has_red_items else StatusEnum.GREEN
        return False, StatusEnum.ORANGE

    return False, StatusEnum.GREY


def _category_is_complete(raw_category: dict[str, Any], results_by_target: dict[str, CheckResult]) -> bool:
    if raw_category['name'] == DEFAULT_CATEGORY_NAME:
        return True
    relevant_subcategories = [sub for sub in raw_category['subcategories'] if sub['name'] != DEFAULT_SUBCATEGORY_NAME]
    return bool(relevant_subcategories) and all(_subcategory_is_complete(sub, results_by_target)[0] for sub in relevant_subcategories)


async def _get_or_create_selection_cycle(location: str, db: AsyncSession) -> SelectionCycle:
    normalized = _normalize_location(location)
    cycle = await db.scalar(select(SelectionCycle).where(SelectionCycle.location == normalized).limit(1))
    today = date.today()

    if not cycle:
        cycle = SelectionCycle(location=normalized, cycle_version=1, started_at=today)
        db.add(cycle)
        await db.commit()
        await db.refresh(cycle)
        return cycle

    if (today - cycle.started_at).days >= SELECTION_CYCLE_DAYS:
        old_version = cycle.cycle_version
        cycle.cycle_version += 1
        cycle.started_at = today
        cycle.updated_at = datetime.utcnow()
        await db.execute(delete(CategoryAssignment).where(CategoryAssignment.location == normalized, CategoryAssignment.cycle_version == old_version))
        await db.commit()
        await db.refresh(cycle)

    return cycle


async def reset_selection_cycle(location: str, db: AsyncSession) -> ResetSelectionCycleResponse:
    cycle = await _get_or_create_selection_cycle(location, db)
    old_version = cycle.cycle_version
    cycle.cycle_version += 1
    cycle.started_at = date.today()
    cycle.updated_at = datetime.utcnow()
    await db.execute(delete(CategoryAssignment).where(CategoryAssignment.location == _normalize_location(location), CategoryAssignment.cycle_version == old_version))
    await db.commit()
    await db.refresh(cycle)
    return ResetSelectionCycleResponse(
        success=True,
        message='Выбор категорий и подкатегорий обновлён. Начался новый 15-дневный цикл.',
        cycle_version=cycle.cycle_version,
        cycle_started_at=cycle.started_at.strftime('%d.%m.%Y'),
    )



async def _sync_report_status(report: Report, db: AsyncSession) -> None:
    results_count = await db.scalar(select(func.count()).select_from(CheckResult).where(CheckResult.report_id == report.id))
    newer_report_exists = await db.scalar(
        select(func.count()).select_from(Report).where(
            Report.location == report.location,
            Report.report_date > report.report_date,
        )
    )

    if (results_count or 0) == 0:
        report.status = 'created'
    elif (newer_report_exists or 0) > 0:
        report.status = 'completed'
    else:
        report.status = 'in_progress'


async def _complete_previous_reports(location: str, current_report_date: date, db: AsyncSession) -> None:
    previous_reports = (
        await db.scalars(
            select(Report).where(
                Report.location == location,
                Report.report_date < current_report_date,
                Report.status != 'completed',
            )
        )
    ).all()
    for report in previous_reports:
        report.status = 'completed'


async def get_or_create_daily_report(location: str, cycle_version: int, db: AsyncSession) -> Report:
    today = get_moscow_today()
    normalized = _normalize_location(location)

    report = await db.scalar(
        select(Report).where(
            Report.location == normalized,
            Report.report_date == today,
        ).limit(1)
    )
    if report:
        await _sync_report_status(report, db)
        await db.commit()
        return report

    report = Report(location=normalized, report_date=today, cycle_version=cycle_version, report_type='daily', status='created')
    db.add(report)
    await _complete_previous_reports(normalized, today, db)
    await db.commit()
    await db.refresh(report)
    return report


async def _load_assignments(location: str, cycle_version: int, db: AsyncSession) -> list[CategoryAssignment]:
    return (await db.scalars(select(CategoryAssignment).where(CategoryAssignment.location == location, CategoryAssignment.cycle_version == cycle_version))).all()


async def _load_results(report_id: int, db: AsyncSession) -> list[CheckResult]:
    return (await db.scalars(select(CheckResult).where(CheckResult.report_id == report_id).order_by(CheckResult.id.asc()))).all()


async def _load_selection_targets(location: str, cycle_version: int, db: AsyncSession) -> list[SelectionTarget]:
    return (await db.scalars(select(SelectionTarget).where(SelectionTarget.location == location, SelectionTarget.cycle_version == cycle_version))).all()


def _selection_target_maps(targets: list[SelectionTarget]) -> tuple[set[str], dict[str, set[str]]]:
    category_ids: set[str] = set()
    subcategory_ids: dict[str, set[str]] = defaultdict(set)
    for row in targets:
        if row.target_type == 'category':
            category_ids.add(row.category_id)
        elif row.target_type == 'subcategory' and row.subcategory_id:
            subcategory_ids[row.category_id].add(row.subcategory_id)
    return category_ids, subcategory_ids


def _is_categoryless_subcategory(category: dict[str, Any], subcategory: dict[str, Any]) -> bool:
    return category['name'] != DEFAULT_CATEGORY_NAME and subcategory['name'] == DEFAULT_SUBCATEGORY_NAME


def _filter_inventory_by_targets(inventory: dict[str, Any], selected_category_ids: set[str], selected_subcategory_ids: dict[str, set[str]]) -> dict[str, Any]:
    if not selected_category_ids and not any(selected_subcategory_ids.values()):
        return inventory

    categories: list[dict[str, Any]] = []
    for category in inventory['categories']:
        if category['name'] == DEFAULT_CATEGORY_NAME:
            categories.append(category)
            continue

        include_full_category = category['id'] in selected_category_ids
        allowed_sub_ids = selected_subcategory_ids.get(category['id'], set())
        filtered_subcategories: list[dict[str, Any]] = []
        for sub in category['subcategories']:
            if _is_categoryless_subcategory(category, sub):
                if include_full_category:
                    filtered_subcategories.append(dict(sub))
                continue
            if include_full_category or sub['id'] in allowed_sub_ids:
                filtered_subcategories.append(dict(sub))
        if include_full_category or filtered_subcategories:
            categories.append({
                'id': category['id'],
                'name': category['name'],
                'subcategories': filtered_subcategories,
            })
    return {'location': inventory.get('location'), 'categories': categories}


async def _backfill_legacy_location_points(db: AsyncSession) -> None:
    legacy_names: set[str] = set()

    def _add_legacy(values: list[str | None]) -> None:
        for value in values:
            if value and value.strip():
                legacy_names.add(_normalize_location(value))

    _add_legacy((await db.scalars(select(User.location).where(User.location.is_not(None)))).all())
    _add_legacy((await db.scalars(select(Report.location))).all())
    _add_legacy((await db.scalars(select(SelectionCycle.location))).all())
    _add_legacy((await db.scalars(select(SelectionTarget.location))).all())
    _add_legacy((await db.scalars(select(CategoryAssignment.location))).all())
    legacy_names.update(sorted(MOCK_INVENTORY.keys()))

    configured_defaults: list[tuple[str, str | None, str | None, str | None]] = []
    if settings.store_dubna:
        configured_defaults.append((
            _normalize_location(settings.store_dubna),
            settings.moysklad_token or None,
            settings.store_dubna_id or None,
            settings.store_dubna or None,
        ))
    if settings.store_dmitrov:
        configured_defaults.append((
            _normalize_location(settings.store_dmitrov),
            settings.moysklad_token or None,
            settings.store_dmitrov_id or None,
            settings.store_dmitrov or None,
        ))

    for name, _token, _store_id, _store_name in configured_defaults:
        if name:
            legacy_names.add(name)

    if not legacy_names:
        return

    existing_rows = (await db.scalars(select(LocationPoint).where(LocationPoint.name.in_(legacy_names)))).all()
    existing_by_name = {row.name: row for row in existing_rows}
    changed = False

    defaults_by_name = {name: (token, store_id, store_name) for name, token, store_id, store_name in configured_defaults}

    for name in sorted(legacy_names):
        row = existing_by_name.get(name)
        default_token, default_store_id, default_store_name = defaults_by_name.get(name, (None, None, name))
        if row is None:
            db.add(LocationPoint(
                name=name,
                ms_token=default_token,
                ms_store_id=default_store_id,
                ms_store_name=default_store_name or name,
            ))
            changed = True
            continue

        row_updated = False
        if not row.ms_token and default_token:
            row.ms_token = default_token
            row_updated = True
        if not row.ms_store_id and default_store_id:
            row.ms_store_id = default_store_id
            row_updated = True
        if (not row.ms_store_name) and (default_store_name or name):
            row.ms_store_name = default_store_name or name
            row_updated = True
        changed = changed or row_updated

    if changed:
        await db.commit()


async def list_locations(db: AsyncSession) -> LocationListResponse:
    await _backfill_legacy_location_points(db)
    rows = (await db.scalars(select(LocationPoint).order_by(LocationPoint.name.asc()))).all()
    locations = [LocationPointModel.model_validate(row) for row in rows]
    if not locations:
        fallback = sorted(MOCK_INVENTORY.keys())
        locations = [LocationPointModel(id=idx + 1, name=name, ms_store_name=name) for idx, name in enumerate(fallback)]
    return LocationListResponse(locations=locations)


async def list_moysklad_stores_by_token(ms_token: str) -> StoreListResponse:
    headers = {
        'Authorization': f'Bearer {ms_token}',
        'Accept-Encoding': 'gzip',
        'Content-Type': 'application/json',
    }
    timeout = httpx.Timeout(20.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(f"{settings.ms_api_base_url.rstrip('/')}/entity/store", headers=headers)
        response.raise_for_status()
        data = response.json()
    stores = [
        StoreOption(id=row.get('id'), name=row.get('name') or 'Без названия')
        for row in (data.get('rows') or []) if row.get('id')
    ]
    stores.sort(key=lambda item: item.name.lower())
    return StoreListResponse(stores=stores)


async def create_location_point(payload: CreateLocationRequest, db: AsyncSession) -> CreateLocationResponse:
    name = _normalize_location(payload.name)
    existing = await db.scalar(select(LocationPoint).where(LocationPoint.name == name).limit(1))
    if existing:
        raise HTTPException(status_code=400, detail='Точка с таким названием уже существует.')

    point = LocationPoint(
        name=name,
        ms_token=payload.ms_token.strip(),
        ms_store_id=payload.ms_store_id.strip(),
        ms_store_name=payload.ms_store_name.strip(),
    )
    db.add(point)
    await db.commit()
    await db.refresh(point)
    return CreateLocationResponse(success=True, message='Точка создана.', location=LocationPointModel.model_validate(point))


async def update_location_point(location_id: int, payload: UpdateLocationRequest, db: AsyncSession) -> UpdateLocationResponse:
    point = await db.get(LocationPoint, location_id)
    if not point:
        raise HTTPException(status_code=404, detail='Точка не найдена.')

    name = _normalize_location(payload.name)
    duplicate = await db.scalar(select(LocationPoint).where(LocationPoint.name == name, LocationPoint.id != location_id).limit(1))
    if duplicate:
        raise HTTPException(status_code=400, detail='Точка с таким названием уже существует.')

    old_name = point.name
    point.name = name
    point.ms_token = payload.ms_token.strip()
    point.ms_store_id = payload.ms_store_id.strip()
    point.ms_store_name = payload.ms_store_name.strip()

    if old_name != point.name:
        await db.execute(update(User).where(User.location == old_name).values(location=point.name))
        await db.execute(update(Report).where(Report.location == old_name).values(location=point.name))
        await db.execute(update(SelectionCycle).where(SelectionCycle.location == old_name).values(location=point.name))
        await db.execute(update(SelectionTarget).where(SelectionTarget.location == old_name).values(location=point.name))
        await db.execute(update(CategoryAssignment).where(CategoryAssignment.location == old_name).values(location=point.name))

    await db.commit()
    await db.refresh(point)
    ms_client.invalidate_inventory(old_name)
    ms_client.invalidate_inventory(point.name)
    return UpdateLocationResponse(success=True, message='Точка обновлена.', location=LocationPointModel.model_validate(point))


async def delete_location_point(location_id: int, db: AsyncSession) -> DeleteResponse:
    point = await db.get(LocationPoint, location_id)
    if not point:
        raise HTTPException(status_code=404, detail='Точка не найдена.')

    linked_entities: list[str] = []
    checks = [
        ('пользователи', User),
        ('ревизии', Report),
        ('циклы выбора', SelectionCycle),
        ('выбор категорий цикла', SelectionTarget),
        ('закрепления сотрудников', CategoryAssignment),
    ]
    for label, model in checks:
        count = await db.scalar(select(func.count()).select_from(model).where(model.location == point.name))
        if (count or 0) > 0:
            linked_entities.append(label)

    if linked_entities:
        raise HTTPException(
            status_code=400,
            detail='Нельзя удалить точку, пока с ней связаны: ' + ', '.join(linked_entities) + '.',
        )

    old_name = point.name
    await db.delete(point)
    await db.commit()
    ms_client.invalidate_inventory(old_name)
    return DeleteResponse(success=True, message='Точка удалена.')


async def get_cycle_targets(location: str, db: AsyncSession) -> AdminCycleTargetsResponse:
    normalized = _normalize_location(location)
    cycle = await _get_or_create_selection_cycle(normalized, db)
    inventory = await _get_inventory_for(normalized)
    targets = await _load_selection_targets(normalized, cycle.cycle_version, db)
    selected_category_ids, selected_subcategory_ids = _selection_target_maps(targets)

    categories: list[AdminCycleTargetCategory] = []
    for category in inventory['categories']:
        if category['name'] == DEFAULT_CATEGORY_NAME:
            continue
        subcategories: list[AdminCycleTargetItem] = []
        for sub in category['subcategories']:
            if _is_categoryless_subcategory(category, sub):
                continue
            subcategories.append(AdminCycleTargetItem(
                id=sub['id'],
                name=sub['name'],
                selected=sub['id'] in selected_subcategory_ids.get(category['id'], set()),
                disabled=category['id'] in selected_category_ids,
            ))
        categories.append(AdminCycleTargetCategory(
            id=category['id'],
            name=category['name'],
            selected=category['id'] in selected_category_ids,
            subcategories=subcategories,
        ))

    return AdminCycleTargetsResponse(
        location=normalized,
        cycle_version=cycle.cycle_version,
        cycle_started_at=cycle.started_at.strftime('%d.%m.%Y'),
        categories=categories,
    )


async def save_cycle_targets(payload: SaveCycleTargetsRequest, db: AsyncSession) -> SaveCycleTargetsResponse:
    normalized = _normalize_location(payload.location)
    cycle = await _get_or_create_selection_cycle(normalized, db)
    inventory = await _get_inventory_for(normalized)

    if payload.cycle_started_at:
        cycle.started_at = payload.cycle_started_at
        cycle.updated_at = datetime.utcnow()

    category_by_id = {row['id']: row for row in inventory['categories']}
    sub_by_id: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for category in inventory['categories']:
        for sub in category['subcategories']:
            sub_by_id[sub['id']] = (category, sub)

    await db.execute(delete(SelectionTarget).where(SelectionTarget.location == normalized, SelectionTarget.cycle_version == cycle.cycle_version))

    for category_id in sorted(set(payload.category_ids)):
        category = category_by_id.get(category_id)
        if not category or category['name'] == DEFAULT_CATEGORY_NAME:
            continue
        db.add(SelectionTarget(
            location=normalized,
            cycle_version=cycle.cycle_version,
            category_id=category_id,
            category_name=category['name'],
            subcategory_id=None,
            subcategory_name=None,
            target_type='category',
            target_id=category_id,
            target_name=category['name'],
        ))

    selected_categories = set(payload.category_ids)
    for subcategory_id in sorted(set(payload.subcategory_ids)):
        pair = sub_by_id.get(subcategory_id)
        if not pair:
            continue
        category, sub = pair
        if category['id'] in selected_categories or category['name'] == DEFAULT_CATEGORY_NAME or _is_categoryless_subcategory(category, sub):
            continue
        db.add(SelectionTarget(
            location=normalized,
            cycle_version=cycle.cycle_version,
            category_id=category['id'],
            category_name=category['name'],
            subcategory_id=sub['id'],
            subcategory_name=sub['name'],
            target_type='subcategory',
            target_id=sub['id'],
            target_name=sub['name'],
        ))

    await db.commit()
    return SaveCycleTargetsResponse(success=True, message='Изменения сохранены.', cycle_started_at=cycle.started_at.strftime('%d.%m.%Y'))


def _category_assignments_map(assignments: list[CategoryAssignment]) -> tuple[
    dict[str, CategoryAssignment],
    dict[str, dict[str, CategoryAssignment]],
    dict[str, dict[str, dict[str, CategoryAssignment]]],
]:
    category_map: dict[str, CategoryAssignment] = {}
    subcategory_map: dict[str, dict[str, CategoryAssignment]] = defaultdict(dict)
    item_map: dict[str, dict[str, dict[str, CategoryAssignment]]] = defaultdict(lambda: defaultdict(dict))
    for assignment in assignments:
        if assignment.target_type == 'category':
            category_map[assignment.category_id] = assignment
        elif assignment.target_type == 'subcategory' and assignment.subcategory_id:
            subcategory_map[assignment.category_id][assignment.subcategory_id] = assignment
        elif assignment.target_type == 'item' and assignment.subcategory_id:
            item_map[assignment.category_id][assignment.subcategory_id][assignment.target_id] = assignment
    return category_map, subcategory_map, item_map


def _subcategories_user_can_work(raw_category: dict[str, Any], category_assignment: CategoryAssignment | None, sub_assignments: dict[str, CategoryAssignment], user_id: int) -> list[str]:
    if category_assignment and category_assignment.user_id == user_id:
        return [sub['id'] for sub in raw_category['subcategories']]
    return [sub['id'] for sub in raw_category['subcategories'] if sub_assignments.get(sub['id']) and sub_assignments[sub['id']].user_id == user_id]


async def _refresh_report_status(report: Report, db: AsyncSession) -> None:
    await _sync_report_status(report, db)
    await db.commit()


async def get_inventory_data(location: str, db: AsyncSession, user: User) -> InventoryStructureResponse:
    normalized = _normalize_location(location)
    cycle = await _get_or_create_selection_cycle(normalized, db)
    report = await get_or_create_daily_report(normalized, cycle.cycle_version, db)
    assignments = await _load_assignments(normalized, cycle.cycle_version, db)
    results = await _load_results(report.id, db)
    targets = await _load_selection_targets(normalized, cycle.cycle_version, db)

    category_assignments, subcategory_assignments, item_assignments = _category_assignments_map(assignments)
    selected_category_ids, selected_subcategory_ids = _selection_target_maps(targets)
    rows_by_category_target: dict[str, dict[str, CheckResult]] = defaultdict(dict)
    for row in results:
        rows_by_category_target[row.category_id][row.target_id] = row

    categories: list[CategoryModel] = []
    inventory = await _get_inventory_for(normalized)
    inventory = _filter_inventory_by_targets(inventory, selected_category_ids, selected_subcategory_ids)

    for raw_category in inventory['categories']:
        category_is_diagnostic = raw_category['name'] == DEFAULT_CATEGORY_NAME
        category_assignment = category_assignments.get(raw_category['id'])
        sub_assignments = subcategory_assignments.get(raw_category['id'], {})
        item_assignments_by_sub = item_assignments.get(raw_category['id'], {})

        assigned_to_current_user = bool(category_assignment and category_assignment.user_id == user.id)
        assigned_to_other = bool(category_assignment and category_assignment.user_id != user.id)
        has_my_subcategories = any(a.user_id == user.id for a in sub_assignments.values())
        has_other_subcategories = any(a.user_id != user.id for a in sub_assignments.values())
        has_my_items = any(a.user_id == user.id for sub_items in item_assignments_by_sub.values() for a in sub_items.values())
        has_other_items = any(a.user_id != user.id for sub_items in item_assignments_by_sub.values() for a in sub_items.values())

        has_free_diag_items = False
        for raw_sub in raw_category['subcategories']:
            diagnostic_sub = category_is_diagnostic or raw_sub['name'] == DEFAULT_SUBCATEGORY_NAME
            if not diagnostic_sub:
                continue
            assigned_item_ids = set(item_assignments_by_sub.get(raw_sub['id'], {}).keys())
            if any(item['id'] not in assigned_item_ids for item in raw_sub['items']):
                has_free_diag_items = True
                break

        has_diagnostic_subcategories = any(sub['name'] == DEFAULT_SUBCATEGORY_NAME for sub in raw_category['subcategories'])
        can_take_category = (not category_is_diagnostic) and (not has_diagnostic_subcategories) and category_assignment is None and not sub_assignments and not item_assignments_by_sub

        owner_names = {a.user_full_name_snapshot for a in sub_assignments.values() if a.user_full_name_snapshot}
        owner_names.update(a.user_full_name_snapshot for sub_items in item_assignments_by_sub.values() for a in sub_items.values() if a.user_full_name_snapshot)
        assigned_to = None
        mixed_assignment = False
        if category_assignment:
            assigned_to = category_assignment.user_full_name_snapshot
        elif owner_names:
            if len(owner_names) == 1:
                assigned_to = next(iter(owner_names))
            else:
                assigned_to = 'Несколько сотрудников'
                mixed_assignment = True

        category_results = rows_by_category_target.get(raw_category['id'], {})
        subcategories: list[SubcategoryModel] = []

        selected_sub_ids = _subcategories_user_can_work(raw_category, category_assignment, sub_assignments, user.id)
        first_incomplete_selected: str | None = None
        sub_states: dict[str, tuple[bool, StatusEnum]] = {}
        for raw_sub in raw_category['subcategories']:
            is_completed, status = _subcategory_is_complete(raw_sub, category_results)
            sub_states[raw_sub['id']] = (is_completed, status)
            if raw_sub['id'] in selected_sub_ids and not is_completed and first_incomplete_selected is None:
                first_incomplete_selected = raw_sub['id']

        for raw_sub in raw_category['subcategories']:
            diagnostic_sub = category_is_diagnostic or raw_sub['name'] == DEFAULT_SUBCATEGORY_NAME
            is_completed, status = sub_states[raw_sub['id']]
            sub_item_assignments = item_assignments_by_sub.get(raw_sub['id'], {})
            has_my_items_in_sub = any(a.user_id == user.id for a in sub_item_assignments.values())
            has_other_items_in_sub = any(a.user_id != user.id for a in sub_item_assignments.values())

            item_rows = []
            for item in raw_sub['items']:
                item_row = category_results.get(item['id'])
                item_status = StatusEnum.GREY
                is_final = False
                if item_row:
                    if item_row.status == 'green':
                        item_status = StatusEnum.GREEN
                        is_final = True
                    elif item_row.status == 'red':
                        item_status = StatusEnum.RED
                        is_final = True
                    elif item_row.status == 'orange':
                        item_status = StatusEnum.ORANGE

                item_assignment = sub_item_assignments.get(item['id'])
                item_rows.append(ItemModel(
                    id=item['id'],
                    name=item['name'],
                    status=item_status,
                    is_final=is_final,
                    assigned_to=item_assignment.user_full_name_snapshot if item_assignment else None,
                    assigned_to_current_user=bool(item_assignment and item_assignment.user_id == user.id),
                    can_take=diagnostic_sub and category_assignment is None and sub_assignments.get(raw_sub['id']) is None and item_assignment is None,
                    is_blocked_by_other=bool(item_assignment and item_assignment.user_id != user.id),
                    is_diagnostic=diagnostic_sub,
                ))

            sub_assignment = sub_assignments.get(raw_sub['id'])
            sub_assigned_to_current_user = bool(sub_assignment and sub_assignment.user_id == user.id)
            sub_assigned_to_other = bool(sub_assignment and sub_assignment.user_id != user.id)
            can_take_sub = (not diagnostic_sub) and category_assignment is None and sub_assignment is None and not sub_item_assignments

            is_locked = True
            is_expanded = False
            if raw_sub['id'] in selected_sub_ids:
                if first_incomplete_selected is None:
                    is_locked = False
                else:
                    is_locked = raw_sub['id'] != first_incomplete_selected and not is_completed
                    is_expanded = raw_sub['id'] == first_incomplete_selected and not is_completed
                if status == StatusEnum.ORANGE:
                    is_expanded = True
                    is_locked = False

            sub_owner_names = {a.user_full_name_snapshot for a in sub_item_assignments.values() if a.user_full_name_snapshot}
            sub_assigned_to = None
            if sub_assignment:
                sub_assigned_to = sub_assignment.user_full_name_snapshot
            elif category_assignment:
                sub_assigned_to = category_assignment.user_full_name_snapshot
            elif sub_owner_names:
                sub_assigned_to = next(iter(sub_owner_names)) if len(sub_owner_names) == 1 else 'Несколько сотрудников'

            subcategories.append(
                SubcategoryModel(
                    id=raw_sub['id'],
                    name=raw_sub['name'],
                    is_locked=is_locked,
                    is_completed=is_completed,
                    is_expanded=is_expanded,
                    status=status,
                    items=item_rows,
                    assigned_to=sub_assigned_to,
                    assigned_to_current_user=sub_assigned_to_current_user,
                    can_take=can_take_sub,
                    is_blocked_by_other=sub_assigned_to_other or assigned_to_other,
                    taken_as_part_of_category=assigned_to_current_user,
                    is_diagnostic=diagnostic_sub,
                    has_my_items=has_my_items_in_sub,
                    has_other_items=has_other_items_in_sub,
                )
            )

        category_is_completed = _category_is_complete(raw_category, category_results)
        categories.append(
            CategoryModel(
                id=raw_category['id'],
                name=raw_category['name'],
                is_available=assigned_to_current_user or has_my_subcategories or has_my_items or can_take_category or has_free_diag_items,
                is_completed=category_is_completed,
                is_open=(assigned_to_current_user or has_my_subcategories or has_my_items) and not category_is_completed,
                subcategories=subcategories,
                assigned_to=assigned_to,
                assigned_to_current_user=assigned_to_current_user,
                can_take=can_take_category,
                is_blocked_by_other=assigned_to_other,
                has_my_subcategories=has_my_subcategories,
                has_other_subcategories=has_other_subcategories,
                mixed_assignment=mixed_assignment,
                is_diagnostic=category_is_diagnostic,
                has_my_items=has_my_items,
                has_other_items=has_other_items,
            )
        )

    days_left = max(0, SELECTION_CYCLE_DAYS - (date.today() - cycle.started_at).days)
    return InventoryStructureResponse(
        report_id=report.id,
        location=normalized,
        report_date=report.report_date.strftime('%d.%m.%Y'),
        categories=categories,
        cycle_version=cycle.cycle_version,
        cycle_started_at=cycle.started_at.strftime('%d.%m.%Y'),
        cycle_days_left=days_left,
    )


async def assign_selection_to_user(report_id: int, category_id: str, target_type: str, subcategory_id: str | None, item_id: str | None, db: AsyncSession, user: User) -> AssignSelectionResponse:
    if not user.location:
        raise HTTPException(status_code=403, detail='Сотруднику не назначена точка.')

    report = await db.get(Report, report_id)
    if not report or report.location != user.location:
        raise HTTPException(status_code=404, detail='Ревизия не найдена.')

    cycle = await _get_or_create_selection_cycle(report.location, db)
    assignments = await _load_assignments(report.location, cycle.cycle_version, db)
    targets = await _load_selection_targets(report.location, cycle.cycle_version, db)
    category_map, sub_map, item_map = _category_assignments_map(assignments)
    selected_category_ids, selected_subcategory_ids = _selection_target_maps(targets)

    category = await _find_category(report.location, category_id)
    category_assignment = category_map.get(category_id)
    sub_assignments = sub_map.get(category_id, {})
    item_assignments_by_sub = item_map.get(category_id, {})
    category_is_diagnostic = category['name'] == DEFAULT_CATEGORY_NAME

    if target_type == 'category':
        if category_is_diagnostic:
            raise HTTPException(status_code=400, detail='Категорию «Без категории» нельзя брать целиком.')
        if category_id not in selected_category_ids:
            raise HTTPException(status_code=400, detail='Эта категория не выбрана администратором для текущего цикла.')
        if any(sub['name'] == DEFAULT_SUBCATEGORY_NAME for sub in category['subcategories']):
            raise HTTPException(status_code=400, detail='Категории со служебными ветками «Без категории/Без подкатегории» нельзя брать целиком. Выберите обычную подкатегорию или конкретные товары.')
        if category_assignment:
            if category_assignment.user_id == user.id:
                return AssignSelectionResponse(success=True, message='Категория уже закреплена за вами.')
            raise HTTPException(status_code=400, detail=f'Категория уже закреплена за сотрудником {category_assignment.user_full_name_snapshot}.')
        if sub_assignments or item_assignments_by_sub:
            raise HTTPException(status_code=400, detail='Внутри этой категории уже есть закреплённые подкатегории или товары. Возьмите свободную подкатегорию либо товар отдельно.')

        db.add(CategoryAssignment(
            location=report.location,
            cycle_version=cycle.cycle_version,
            category_id=category_id,
            category_name=category['name'],
            subcategory_id=None,
            subcategory_name=None,
            target_type='category',
            target_id=category_id,
            target_name=category['name'],
            user_id=user.id,
            user_full_name_snapshot=user.full_name,
        ))
        await db.commit()
        return AssignSelectionResponse(success=True, message='Категория закреплена за вами на текущий 15-дневный цикл.')

    if target_type == 'subcategory':
        if not subcategory_id:
            raise HTTPException(status_code=400, detail='Не указана подкатегория.')
        if category_assignment:
            if category_assignment.user_id == user.id:
                return AssignSelectionResponse(success=True, message='Вся категория уже закреплена за вами.')
            raise HTTPException(status_code=400, detail=f'Вся категория уже закреплена за сотрудником {category_assignment.user_full_name_snapshot}.')

        if category_id not in selected_category_ids and subcategory_id not in selected_subcategory_ids.get(category_id, set()):
            raise HTTPException(status_code=400, detail='Эта подкатегория не выбрана администратором для текущего цикла.')

        subcategory = await _find_subcategory(report.location, category_id, subcategory_id)
        diagnostic_sub = category_is_diagnostic or subcategory['name'] == DEFAULT_SUBCATEGORY_NAME
        if diagnostic_sub:
            raise HTTPException(status_code=400, detail='Служебные ветки «Без категории/Без подкатегории» нельзя брать целиком. Выберите конкретные товары.')

        existing = sub_assignments.get(subcategory_id)
        if existing:
            if existing.user_id == user.id:
                return AssignSelectionResponse(success=True, message='Подкатегория уже закреплена за вами.')
            raise HTTPException(status_code=400, detail=f'Подкатегория уже закреплена за сотрудником {existing.user_full_name_snapshot}.')
        if item_assignments_by_sub.get(subcategory_id):
            raise HTTPException(status_code=400, detail='Внутри этой подкатегории уже есть закреплённые товары. Выберите свободный товар отдельно.')

        db.add(CategoryAssignment(
            location=report.location,
            cycle_version=cycle.cycle_version,
            category_id=category_id,
            category_name=category['name'],
            subcategory_id=subcategory_id,
            subcategory_name=subcategory['name'],
            target_type='subcategory',
            target_id=subcategory_id,
            target_name=subcategory['name'],
            user_id=user.id,
            user_full_name_snapshot=user.full_name,
        ))
        await db.commit()
        return AssignSelectionResponse(success=True, message='Подкатегория закреплена за вами на текущий 15-дневный цикл.')

    if target_type == 'item':
        if not subcategory_id or not item_id:
            raise HTTPException(status_code=400, detail='Для выбора товара нужно указать подкатегорию и товар.')
        if category_assignment:
            raise HTTPException(status_code=400, detail='Сейчас внутри этой категории нельзя закреплять отдельные товары, потому что уже есть выбор категории целиком.')

        if category_id not in selected_category_ids and subcategory_id not in selected_subcategory_ids.get(category_id, set()):
            raise HTTPException(status_code=400, detail='Эта подкатегория не выбрана администратором для текущего цикла.')

        subcategory = await _find_subcategory(report.location, category_id, subcategory_id)
        diagnostic_sub = category_is_diagnostic or subcategory['name'] == DEFAULT_SUBCATEGORY_NAME
        if not diagnostic_sub:
            raise HTTPException(status_code=400, detail='Поштучный выбор доступен только для служебных веток «Без категории/Без подкатегории».')

        sub_assignment = sub_assignments.get(subcategory_id)
        if sub_assignment:
            if sub_assignment.user_id == user.id:
                raise HTTPException(status_code=400, detail='У вас уже закреплена вся подкатегория. Отдельный товар выбирать не нужно.')
            raise HTTPException(status_code=400, detail=f'Подкатегория уже закреплена за сотрудником {sub_assignment.user_full_name_snapshot}.')

        existing_item = item_assignments_by_sub.get(subcategory_id, {}).get(item_id)
        if existing_item:
            if existing_item.user_id == user.id:
                return AssignSelectionResponse(success=True, message='Товар уже закреплён за вами.')
            raise HTTPException(status_code=400, detail=f'Товар уже закреплён за сотрудником {existing_item.user_full_name_snapshot}.')

        item = next((row for row in subcategory['items'] if row['id'] == item_id), None)
        if not item:
            raise HTTPException(status_code=404, detail='Товар не найден в выбранной подкатегории.')

        db.add(CategoryAssignment(
            location=report.location,
            cycle_version=cycle.cycle_version,
            category_id=category_id,
            category_name=category['name'],
            subcategory_id=subcategory_id,
            subcategory_name=subcategory['name'],
            target_type='item',
            target_id=item_id,
            target_name=item['name'],
            user_id=user.id,
            user_full_name_snapshot=user.full_name,
        ))
        await db.commit()
        return AssignSelectionResponse(success=True, message='Товар закреплён за вами на текущий 15-дневный цикл.')

    raise HTTPException(status_code=400, detail='Некорректный тип выбора.')


def _user_can_verify_target(user: User, report: Report, category_id: str, subcategory_id: str | None, target_id: str, target_type: str, assignments: list[CategoryAssignment]) -> bool:
    category_map, sub_map, item_map = _category_assignments_map(assignments)
    cat_assignment = category_map.get(category_id)
    if cat_assignment and cat_assignment.user_id == user.id:
        return True
    if subcategory_id and sub_map.get(category_id, {}).get(subcategory_id) and sub_map[category_id][subcategory_id].user_id == user.id:
        return True
    if target_type == 'item' and subcategory_id:
        item_assignment = item_map.get(category_id, {}).get(subcategory_id, {}).get(target_id)
        if item_assignment and item_assignment.user_id == user.id:
            return True
    return False



async def _upsert_check_result(
    *,
    report_id: int,
    category_id: str,
    category_name: str,
    subcategory_id: str | None,
    subcategory_name: str | None,
    target_type: str,
    target_id: str,
    target_name: str,
    expected_qty: float,
    actual_qty: float,
    diff: float,
    status_value: str,
    attempts_used: int,
    checked_by_user_id: int | None,
    checked_by_name_snapshot: str | None,
    db: AsyncSession,
) -> None:
    existing = await db.scalar(select(CheckResult).where(CheckResult.report_id == report_id, CheckResult.target_id == target_id).limit(1))
    if existing:
        existing.category_id = category_id
        existing.category_name = category_name
        existing.subcategory_id = subcategory_id
        existing.subcategory_name = subcategory_name
        existing.target_type = target_type
        existing.target_name = target_name
        existing.expected_qty = expected_qty
        existing.actual_qty = actual_qty
        existing.diff = diff
        existing.status = status_value
        existing.attempts_used = attempts_used
        existing.checked_by_user_id = checked_by_user_id
        existing.checked_by_name_snapshot = checked_by_name_snapshot
    else:
        db.add(CheckResult(
            report_id=report_id,
            category_id=category_id,
            category_name=category_name,
            subcategory_id=subcategory_id,
            subcategory_name=subcategory_name,
            target_type=target_type,
            target_id=target_id,
            target_name=target_name,
            expected_qty=expected_qty,
            actual_qty=actual_qty,
            diff=diff,
            status=status_value,
            attempts_used=attempts_used,
            checked_by_user_id=checked_by_user_id,
            checked_by_name_snapshot=checked_by_name_snapshot,
        ))


async def verify_item_or_category(data: VerifyRequest, db: AsyncSession, checked_by_user: User) -> VerifyResponse:
    report = await db.get(Report, data.report_id)
    if not report:
        raise HTTPException(status_code=404, detail='Ревизия не найдена.')
    if checked_by_user.location != report.location:
        raise HTTPException(status_code=403, detail='Ревизия относится к другой точке.')

    category_id, category_name, subcategory_id, subcategory_name, target_type, target_name, expected_qty = await _find_target(report.location, data.target_id)
    assignments = await _load_assignments(report.location, report.cycle_version, db)
    if not _user_can_verify_target(checked_by_user, report, category_id, subcategory_id, data.target_id, target_type, assignments):
        raise HTTPException(status_code=403, detail='Эта категория, подкатегория или товар не закреплены за вами.')

    is_correct = abs(data.quantity - expected_qty) < 1e-9
    if is_correct:
        await _upsert_check_result(
            report_id=report.id,
            category_id=category_id,
            category_name=category_name,
            subcategory_id=subcategory_id,
            subcategory_name=subcategory_name,
            target_type=target_type,
            target_id=data.target_id,
            target_name=target_name,
            expected_qty=expected_qty,
            actual_qty=data.quantity,
            diff=0.0,
            status_value='green',
            attempts_used=data.attempt_number,
            checked_by_user_id=checked_by_user.id,
            checked_by_name_snapshot=checked_by_user.full_name,
            db=db,
        )
        await db.commit()
        await _refresh_report_status(report, db)
        return VerifyResponse(is_correct=True, attempts_left=0, message='Верно!', expand_category=False)

    attempts_left = max(0, 3 - data.attempt_number)
    if attempts_left > 0:
        return VerifyResponse(is_correct=False, attempts_left=attempts_left, message=f'Неверно. Осталось {attempts_left} попытк(и).', expand_category=False)

    status_value = 'orange' if data.is_category else 'red'
    await _upsert_check_result(
        report_id=report.id,
        category_id=category_id,
        category_name=category_name,
        subcategory_id=subcategory_id,
        subcategory_name=subcategory_name,
        target_type=target_type,
        target_id=data.target_id,
        target_name=target_name,
        expected_qty=expected_qty,
        actual_qty=data.quantity,
        diff=float(data.quantity - expected_qty),
        status_value=status_value,
        attempts_used=data.attempt_number,
        checked_by_user_id=checked_by_user.id,
        checked_by_name_snapshot=checked_by_user.full_name,
        db=db,
    )
    await db.commit()
    await _refresh_report_status(report, db)
    return VerifyResponse(
        is_correct=False,
        attempts_left=0,
        message='Расхождение! Переходим к поштучной проверке...' if data.is_category else 'Расхождение зафиксировано.',
        expand_category=data.is_category,
    )


async def finish_report(report_id: int, db: AsyncSession) -> tuple[bool, str]:
    report = await db.get(Report, report_id)
    if not report:
        return False, 'Ревизия не найдена.'
    await _refresh_report_status(report, db)
    if report.status != 'completed':
        return False, 'Общая ревизия завершится автоматически, когда все выбранные категории и подкатегории будут проверены.'
    return True, 'Ревизия завершена.'


async def get_reports_history(location: str, db: AsyncSession) -> ReportHistoryResponse:
    normalized = _normalize_location(location)
    reports = (await db.scalars(select(Report).where(Report.location == normalized).order_by(Report.date_created.desc(), Report.id.desc()))).all()
    for report in reports:
        await _sync_report_status(report, db)
    await db.commit()

    report_numbers = _build_report_numbers(reports)

    return ReportHistoryResponse(
        location=normalized,
        reports=[
            ReportHistoryItem(
                report_id=report.id,
                report_number=report_numbers.get(report.id),
                date=_format_moscow_datetime(report.date_created),
                status=report.status,
                label=f"№{report_numbers.get(report.id, '-')} · {_format_moscow_datetime(report.date_created)} — {_report_status_label(report.status)}",
            )
            for report in reports
        ],
    )


def _category_assignment_label(category_id: str, report_assignments: list[CategoryAssignment]) -> str | None:
    category_level = [a for a in report_assignments if a.category_id == category_id and a.target_type == 'category']
    if category_level:
        return category_level[0].user_full_name_snapshot
    owners = sorted({a.user_full_name_snapshot for a in report_assignments if a.category_id == category_id and a.user_full_name_snapshot})
    if not owners:
        return None
    if len(owners) == 1:
        return owners[0]
    return 'Несколько сотрудников'


def _build_report_numbers(reports: list[Report]) -> dict[int, int]:
    ordered = sorted(reports, key=lambda item: (item.date_created, item.id))
    return {report.id: index + 1 for index, report in enumerate(ordered)}


async def _get_report_number(report: Report, db: AsyncSession) -> int:
    result = await db.scalar(
        select(func.count())
        .select_from(Report)
        .where(
            Report.location == report.location,
            or_(
                Report.date_created < report.date_created,
                and_(Report.date_created == report.date_created, Report.id <= report.id),
            ),
        )
    )
    return int(result or 0)


async def _load_discrepancy_financials(location: str, results: list[CheckResult]) -> dict[str, dict[str, float | None]]:
    point = await _get_location_point(location)
    token = point.ms_token if point and point.ms_token and point.ms_store_id else None
    store_id = point.ms_store_id if point and point.ms_token and point.ms_store_id else None

    if not token and not ms_client.enabled:
        return {}

    unique_item_ids = sorted({
        row.target_id
        for row in results
        if row.target_type == 'item' and row.status == 'red' and row.target_id
    })
    if not unique_item_ids:
        return {}

    async def fetch(item_id: str) -> tuple[str, dict[str, float | None]]:
        try:
            values = await ms_client.get_item_financials(location, item_id, token=token, store_id=store_id)
        except Exception:
            return item_id, {'cost_price': None, 'retail_price': None}
        return item_id, values

    loaded = await asyncio.gather(*(fetch(item_id) for item_id in unique_item_ids))
    return {item_id: values for item_id, values in loaded}


def _build_report_inventory_fallback(results: list[CheckResult], targets: list[SelectionTarget]) -> dict[str, Any]:
    categories_map: dict[str, dict[str, Any]] = {}

    def ensure_category(category_id: str | None, category_name: str | None) -> dict[str, Any]:
        key = category_id or category_name or '__unknown_category__'
        category = categories_map.get(key)
        if category is None:
            category = {
                'id': category_id or key,
                'name': category_name or 'Без категории',
                'subcategories': [],
                '_sub_map': {},
            }
            categories_map[key] = category
        return category

    def ensure_subcategory(category: dict[str, Any], subcategory_id: str | None, subcategory_name: str | None) -> dict[str, Any]:
        sub_key = subcategory_id or subcategory_name or f"{category['id']}::__default__"
        sub_map = category['_sub_map']
        subcategory = sub_map.get(sub_key)
        if subcategory is None:
            subcategory = {
                'id': subcategory_id or sub_key,
                'name': subcategory_name or 'Без подкатегории',
                'items': [],
                '_item_ids': set(),
            }
            sub_map[sub_key] = subcategory
            category['subcategories'].append(subcategory)
        return subcategory

    def ensure_item(subcategory: dict[str, Any], item_id: str | None, item_name: str | None, expected_qty: float | None) -> None:
        key = item_id or item_name or f"item-{len(subcategory['items']) + 1}"
        if key in subcategory['_item_ids']:
            return
        subcategory['_item_ids'].add(key)
        subcategory['items'].append({
            'id': item_id or key,
            'name': item_name or 'Без названия',
            'expected_qty': float(expected_qty or 0),
        })

    for target in targets:
        category = ensure_category(target.category_id, target.category_name)
        if target.target_type == 'category':
            continue
        subcategory = ensure_subcategory(category, target.subcategory_id, target.subcategory_name)
        if target.target_type == 'item':
            ensure_item(subcategory, target.target_id, target.target_name, None)

    for row in results:
        category = ensure_category(row.category_id, row.category_name)
        if row.target_type == 'category':
            continue
        subcategory = ensure_subcategory(category, row.subcategory_id, row.subcategory_name)
        if row.target_type == 'item':
            ensure_item(subcategory, row.target_id, row.target_name, row.expected_qty)

    categories: list[dict[str, Any]] = []
    for category in categories_map.values():
        for subcategory in category['subcategories']:
            subcategory.pop('_item_ids', None)
        category.pop('_sub_map', None)
        categories.append(category)

    categories.sort(key=lambda item: str(item.get('name') or '').lower())
    for category in categories:
        category['subcategories'].sort(key=lambda item: str(item.get('name') or '').lower())
        for subcategory in category['subcategories']:
            subcategory['items'].sort(key=lambda item: str(item.get('name') or '').lower())

    return {'location': None, 'categories': categories}


async def get_admin_report(location: str, db: AsyncSession, report_id: int | None = None) -> AdminReport:
    normalized = _normalize_location(location)
    report: Report | None = None
    if report_id is not None:
        report = await db.get(Report, report_id)
        if report and report.location != normalized:
            report = None
    if report is None:
        report = await db.scalar(select(Report).where(Report.location == normalized).order_by(Report.date_created.desc(), Report.id.desc()).limit(1))

    if not report:
        return AdminReport(report_id=None, report_number=None, date='-', location=normalized, status='-', categories=[], total_plus=0.0, total_minus=0.0, employees=[])

    await _sync_report_status(report, db)
    await db.commit()

    assignments = await _load_assignments(report.location, report.cycle_version, db)
    results = await _load_results(report.id, db)
    targets = await _load_selection_targets(normalized, report.cycle_version, db)

    try:
        inventory = await _get_inventory_for(normalized)
    except HTTPException:
        inventory = _build_report_inventory_fallback(results, targets)
    except Exception:
        inventory = _build_report_inventory_fallback(results, targets)

    try:
        discrepancy_financials = await _load_discrepancy_financials(normalized, results)
    except Exception:
        discrepancy_financials = {}

    rows_by_category_target: dict[str, dict[str, CheckResult]] = defaultdict(dict)
    for row in results:
        rows_by_category_target[row.category_id][row.target_id] = row

    grouped_problem_items: dict[str, list[DiscrepancyItem]] = defaultdict(list)
    employee_bucket: dict[str, EmployeeReportSummary] = {}

    for row in results:
        if row.checked_by_name_snapshot:
            bucket = employee_bucket.setdefault(
                row.checked_by_name_snapshot,
                EmployeeReportSummary(full_name=row.checked_by_name_snapshot, categories=[], completed_categories=0, discrepancy_items=0),
            )
            if row.category_name not in bucket.categories:
                bucket.categories.append(row.category_name)
            if row.target_type == 'item' and row.status == 'red':
                bucket.discrepancy_items += 1

        if row.target_type == 'item' and row.status == 'red':
            financials = discrepancy_financials.get(row.target_id, {})
            diff_qty = abs(float(row.diff or 0))
            cost_price = financials.get('cost_price')
            retail_price = financials.get('retail_price')
            cost_total = round(diff_qty * cost_price, 2) if cost_price is not None else None
            retail_total = round(diff_qty * retail_price, 2) if retail_price is not None else None
            lost_profit = None
            if cost_total is not None and retail_total is not None:
                lost_profit = round(retail_total - cost_total, 2)

            grouped_problem_items[row.category_name].append(
                DiscrepancyItem(
                    name=row.target_name,
                    expected=float(row.expected_qty),
                    actual=float(row.actual_qty or 0),
                    diff=float(row.diff or 0),
                    checked_by=row.checked_by_name_snapshot,
                    cost_price=cost_price,
                    retail_price=retail_price,
                    cost_total=cost_total,
                    retail_total=retail_total,
                    lost_profit=lost_profit,
                )
            )

    categories: list[CategoryResult] = []
    selected_category_ids, selected_subcategory_ids = _selection_target_maps(targets)
    inventory = _filter_inventory_by_targets(inventory, selected_category_ids, selected_subcategory_ids)
    for raw_category in inventory['categories']:
        result_map = rows_by_category_target.get(raw_category['id'], {})
        is_completed, status = (True, StatusEnum.GREEN) if _category_is_complete(raw_category, result_map) else (False, StatusEnum.GREY)
        if not is_completed:
            # If any subcategory has progress/problem reflect it
            statuses = [_subcategory_is_complete(sub, result_map)[1] for sub in raw_category['subcategories']]
            if any(s == StatusEnum.RED for s in statuses):
                status = StatusEnum.RED
            elif any(s == StatusEnum.ORANGE for s in statuses):
                status = StatusEnum.ORANGE
            elif any(s == StatusEnum.GREEN for s in statuses):
                status = StatusEnum.ORANGE
            else:
                status = StatusEnum.GREY
        elif grouped_problem_items.get(raw_category['name']):
            status = StatusEnum.RED

        categories.append(CategoryResult(
            name=raw_category['name'],
            status=status,
            assigned_to=_category_assignment_label(raw_category['id'], assignments),
            problem_items=grouped_problem_items.get(raw_category['name'], []),
        ))

    for category in categories:
        owners = {item.checked_by for item in category.problem_items if item.checked_by}
        if len(owners) == 1:
            owner = next(iter(owners))
            employee_bucket.setdefault(owner, EmployeeReportSummary(full_name=owner, categories=[], completed_categories=0, discrepancy_items=0))
            if category.name not in employee_bucket[owner].categories:
                employee_bucket[owner].categories.append(category.name)

    for summary in employee_bucket.values():
        summary.completed_categories = sum(1 for category in categories if category.assigned_to == summary.full_name and category.status in {StatusEnum.GREEN, StatusEnum.RED})

    total_plus = sum(max(float(item.diff), 0.0) for items in grouped_problem_items.values() for item in items)
    total_minus = abs(sum(min(float(item.diff), 0.0) for items in grouped_problem_items.values() for item in items))

    known_costs = [float(item.cost_total) for items in grouped_problem_items.values() for item in items if item.cost_total is not None]
    known_retails = [float(item.retail_total) for items in grouped_problem_items.values() for item in items if item.retail_total is not None]
    known_lost_profits = [float(item.lost_profit) for items in grouped_problem_items.values() for item in items if item.lost_profit is not None]

    total_cost = round(sum(known_costs), 2) if known_costs else None
    total_retail = round(sum(known_retails), 2) if known_retails else None
    total_lost_profit = round(sum(known_lost_profits), 2) if known_lost_profits else None

    report_number = await _get_report_number(report, db)

    return AdminReport(
        report_id=report.id,
        report_number=report_number,
        date=_format_moscow_datetime(report.date_created),
        location=report.location,
        status=_report_status_label(report.status),
        categories=categories,
        total_plus=float(total_plus),
        total_minus=float(total_minus),
        total_cost=total_cost,
        total_retail=total_retail,
        total_lost_profit=total_lost_profit,
        employees=sorted(employee_bucket.values(), key=lambda item: item.full_name.lower()),
    )


async def delete_report(report_id: int, db: AsyncSession) -> DeleteResponse:
    report = await db.get(Report, report_id)
    if not report:
        raise HTTPException(status_code=404, detail='Ревизия не найдена.')
    await db.delete(report)
    await db.commit()
    return DeleteResponse(success=True, message='Ревизия удалена.')
