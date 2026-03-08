from __future__ import annotations

import hashlib
import hmac
import os
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, func, inspect, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import CategoryAssignment, CheckResult, Report, SelectionCycle, User
from app.schemas import (
    AdminReport,
    AssignSelectionResponse,
    CategoryModel,
    CategoryResult,
    DeleteResponse,
    DiscrepancyItem,
    EmployeeReportSummary,
    InventoryStructureResponse,
    ItemModel,
    MeResponse,
    ReportHistoryItem,
    ReportHistoryResponse,
    ResetSelectionCycleResponse,
    RoleEnum,
    StatusEnum,
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
    return 'Завершена' if status == 'completed' else 'В процессе'


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


def _get_inventory_for(location: str) -> dict[str, Any]:
    normalized = _normalize_location(location)
    if normalized not in MOCK_INVENTORY:
        raise HTTPException(status_code=404, detail='Неизвестная точка.')
    return MOCK_INVENTORY[normalized]


def _find_category(location: str, category_id: str) -> dict[str, Any]:
    for category in _get_inventory_for(location)['categories']:
        if category['id'] == category_id:
            return category
    raise HTTPException(status_code=404, detail='Категория не найдена.')


def _find_subcategory(location: str, category_id: str, subcategory_id: str) -> dict[str, Any]:
    category = _find_category(location, category_id)
    for sub in category['subcategories']:
        if sub['id'] == subcategory_id:
            return sub
    raise HTTPException(status_code=404, detail='Подкатегория не найдена.')


def _find_target(location: str, target_id: str) -> tuple[str, str, str | None, str | None, str, str, float]:
    inventory = _get_inventory_for(location)
    for category in inventory['categories']:
        for subcategory in category['subcategories']:
            if subcategory['id'] == target_id:
                expected_total = float(sum(item['expected_qty'] for item in subcategory['items']))
                return category['id'], category['name'], subcategory['id'], subcategory['name'], 'subcategory', subcategory['name'], expected_total
            for item in subcategory['items']:
                if item['id'] == target_id:
                    return category['id'], category['name'], subcategory['id'], subcategory['name'], 'item', item['name'], float(item['expected_qty'])
    raise HTTPException(status_code=404, detail='Цель проверки не найдена.')


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
    return bool(raw_category['subcategories']) and all(_subcategory_is_complete(sub, results_by_target)[0] for sub in raw_category['subcategories'])


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


async def get_or_create_daily_report(location: str, db: AsyncSession) -> Report:
    normalized = _normalize_location(location)
    cycle = await _get_or_create_selection_cycle(normalized, db)
    today = date.today()
    report = await db.scalar(select(Report).where(Report.location == normalized, Report.report_date == today).limit(1))
    if report:
        if report.cycle_version != cycle.cycle_version:
            report.cycle_version = cycle.cycle_version
            await db.commit()
            await db.refresh(report)
        return report

    report = Report(location=normalized, report_date=today, cycle_version=cycle.cycle_version, status='in_progress')
    db.add(report)
    await db.commit()
    await db.refresh(report)
    return report


async def _load_assignments(location: str, cycle_version: int, db: AsyncSession) -> list[CategoryAssignment]:
    return (await db.scalars(select(CategoryAssignment).where(CategoryAssignment.location == location, CategoryAssignment.cycle_version == cycle_version))).all()


async def _load_results(report_id: int, db: AsyncSession) -> list[CheckResult]:
    return (await db.scalars(select(CheckResult).where(CheckResult.report_id == report_id).order_by(CheckResult.id.asc()))).all()


def _category_assignments_map(assignments: list[CategoryAssignment]) -> tuple[dict[str, CategoryAssignment], dict[str, dict[str, CategoryAssignment]]]:
    category_map: dict[str, CategoryAssignment] = {}
    subcategory_map: dict[str, dict[str, CategoryAssignment]] = defaultdict(dict)
    for assignment in assignments:
        if assignment.target_type == 'category':
            category_map[assignment.category_id] = assignment
        elif assignment.target_type == 'subcategory' and assignment.subcategory_id:
            subcategory_map[assignment.category_id][assignment.subcategory_id] = assignment
    return category_map, subcategory_map


def _subcategories_user_can_work(raw_category: dict[str, Any], category_assignment: CategoryAssignment | None, sub_assignments: dict[str, CategoryAssignment], user_id: int) -> list[str]:
    if category_assignment and category_assignment.user_id == user_id:
        return [sub['id'] for sub in raw_category['subcategories']]
    return [sub['id'] for sub in raw_category['subcategories'] if sub_assignments.get(sub['id']) and sub_assignments[sub['id']].user_id == user_id]


async def _refresh_report_status(report: Report, db: AsyncSession) -> None:
    results = await _load_results(report.id, db)
    rows_by_category_target: dict[str, dict[str, CheckResult]] = defaultdict(dict)
    for row in results:
        rows_by_category_target[row.category_id][row.target_id] = row

    inventory = _get_inventory_for(report.location)
    all_complete = True
    for raw_category in inventory['categories']:
        if not _category_is_complete(raw_category, rows_by_category_target.get(raw_category['id'], {})):
            all_complete = False
            break

    report.status = 'completed' if all_complete else 'in_progress'
    await db.commit()


async def get_inventory_data(location: str, db: AsyncSession, user: User) -> InventoryStructureResponse:
    normalized = _normalize_location(location)
    cycle = await _get_or_create_selection_cycle(normalized, db)
    report = await get_or_create_daily_report(normalized, db)
    assignments = await _load_assignments(normalized, cycle.cycle_version, db)
    results = await _load_results(report.id, db)

    category_assignments, subcategory_assignments = _category_assignments_map(assignments)
    rows_by_category_target: dict[str, dict[str, CheckResult]] = defaultdict(dict)
    for row in results:
        rows_by_category_target[row.category_id][row.target_id] = row

    categories: list[CategoryModel] = []
    for raw_category in _get_inventory_for(normalized)['categories']:
        category_assignment = category_assignments.get(raw_category['id'])
        sub_assignments = subcategory_assignments.get(raw_category['id'], {})

        assigned_to_current_user = bool(category_assignment and category_assignment.user_id == user.id)
        assigned_to_other = bool(category_assignment and category_assignment.user_id != user.id)
        can_take_category = category_assignment is None and not sub_assignments
        has_my_subcategories = any(a.user_id == user.id for a in sub_assignments.values())
        has_other_subcategories = any(a.user_id != user.id for a in sub_assignments.values())

        owner_names = {a.user_full_name_snapshot for a in sub_assignments.values() if a.user_full_name_snapshot}
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
            is_completed, status = sub_states[raw_sub['id']]
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
                item_rows.append(ItemModel(id=item['id'], name=item['name'], status=item_status, is_final=is_final))

            sub_assignment = sub_assignments.get(raw_sub['id'])
            sub_assigned_to_current_user = bool(sub_assignment and sub_assignment.user_id == user.id)
            sub_assigned_to_other = bool(sub_assignment and sub_assignment.user_id != user.id)
            can_take_sub = category_assignment is None and sub_assignment is None

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

            subcategories.append(
                SubcategoryModel(
                    id=raw_sub['id'],
                    name=raw_sub['name'],
                    is_locked=is_locked,
                    is_completed=is_completed,
                    is_expanded=is_expanded,
                    status=status,
                    items=item_rows,
                    assigned_to=sub_assignment.user_full_name_snapshot if sub_assignment else (category_assignment.user_full_name_snapshot if category_assignment else None),
                    assigned_to_current_user=sub_assigned_to_current_user,
                    can_take=can_take_sub,
                    is_blocked_by_other=sub_assigned_to_other or assigned_to_other,
                    taken_as_part_of_category=assigned_to_current_user,
                )
            )

        category_is_completed = _category_is_complete(raw_category, category_results)
        categories.append(
            CategoryModel(
                id=raw_category['id'],
                name=raw_category['name'],
                is_available=assigned_to_current_user or has_my_subcategories or can_take_category,
                is_completed=category_is_completed,
                is_open=(assigned_to_current_user or has_my_subcategories) and not category_is_completed,
                subcategories=subcategories,
                assigned_to=assigned_to,
                assigned_to_current_user=assigned_to_current_user,
                can_take=can_take_category,
                is_blocked_by_other=assigned_to_other,
                has_my_subcategories=has_my_subcategories,
                has_other_subcategories=has_other_subcategories,
                mixed_assignment=mixed_assignment,
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


async def assign_selection_to_user(report_id: int, category_id: str, target_type: str, subcategory_id: str | None, db: AsyncSession, user: User) -> AssignSelectionResponse:
    if not user.location:
        raise HTTPException(status_code=403, detail='Сотруднику не назначена точка.')

    report = await db.get(Report, report_id)
    if not report or report.location != user.location:
        raise HTTPException(status_code=404, detail='Ревизия не найдена.')

    cycle = await _get_or_create_selection_cycle(report.location, db)
    assignments = await _load_assignments(report.location, cycle.cycle_version, db)
    category_map, sub_map = _category_assignments_map(assignments)

    category = _find_category(report.location, category_id)
    category_assignment = category_map.get(category_id)
    sub_assignments = sub_map.get(category_id, {})

    if target_type == 'category':
        if category_assignment:
            if category_assignment.user_id == user.id:
                return AssignSelectionResponse(success=True, message='Категория уже закреплена за вами.')
            raise HTTPException(status_code=400, detail=f'Категория уже закреплена за сотрудником {category_assignment.user_full_name_snapshot}.')
        if sub_assignments:
            raise HTTPException(status_code=400, detail='Внутри этой категории уже есть закреплённые подкатегории. Возьмите свободную подкатегорию отдельно.')

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

    if target_type != 'subcategory' or not subcategory_id:
        raise HTTPException(status_code=400, detail='Некорректный тип выбора.')

    if category_assignment:
        if category_assignment.user_id == user.id:
            return AssignSelectionResponse(success=True, message='Вся категория уже закреплена за вами.')
        raise HTTPException(status_code=400, detail=f'Вся категория уже закреплена за сотрудником {category_assignment.user_full_name_snapshot}.')

    subcategory = _find_subcategory(report.location, category_id, subcategory_id)
    existing = sub_assignments.get(subcategory_id)
    if existing:
        if existing.user_id == user.id:
            return AssignSelectionResponse(success=True, message='Подкатегория уже закреплена за вами.')
        raise HTTPException(status_code=400, detail=f'Подкатегория уже закреплена за сотрудником {existing.user_full_name_snapshot}.')

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


def _user_can_verify_target(user: User, report: Report, category_id: str, subcategory_id: str | None, assignments: list[CategoryAssignment]) -> bool:
    category_map, sub_map = _category_assignments_map(assignments)
    cat_assignment = category_map.get(category_id)
    if cat_assignment and cat_assignment.user_id == user.id:
        return True
    if subcategory_id and sub_map.get(category_id, {}).get(subcategory_id) and sub_map[category_id][subcategory_id].user_id == user.id:
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

    category_id, category_name, subcategory_id, subcategory_name, target_type, target_name, expected_qty = _find_target(report.location, data.target_id)
    assignments = await _load_assignments(report.location, report.cycle_version, db)
    if not _user_can_verify_target(checked_by_user, report, category_id, subcategory_id, assignments):
        raise HTTPException(status_code=403, detail='Эта категория или подкатегория не закреплена за вами.')

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
    reports = (await db.scalars(select(Report).where(Report.location == normalized).order_by(Report.report_date.desc(), Report.id.desc()))).all()
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


async def get_admin_report(location: str, db: AsyncSession, report_id: int | None = None) -> AdminReport:
    normalized = _normalize_location(location)
    report: Report | None = None
    if report_id is not None:
        report = await db.get(Report, report_id)
        if report and report.location != normalized:
            report = None
    if report is None:
        report = await db.scalar(select(Report).where(Report.location == normalized).order_by(Report.report_date.desc(), Report.id.desc()).limit(1))

    if not report:
        return AdminReport(report_id=None, date='-', location=normalized, status='-', categories=[], total_plus=0.0, total_minus=0.0, employees=[])

    assignments = await _load_assignments(report.location, report.cycle_version, db)
    results = await _load_results(report.id, db)

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
            grouped_problem_items[row.category_name].append(
                DiscrepancyItem(
                    name=row.target_name,
                    expected=float(row.expected_qty),
                    actual=float(row.actual_qty or 0),
                    diff=float(row.diff or 0),
                    checked_by=row.checked_by_name_snapshot,
                )
            )

    categories: list[CategoryResult] = []
    for raw_category in _get_inventory_for(normalized)['categories']:
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

    return AdminReport(
        report_id=report.id,
        date=_format_moscow_datetime(report.date_created),
        location=report.location,
        status=_report_status_label(report.status),
        categories=categories,
        total_plus=float(total_plus),
        total_minus=float(total_minus),
        employees=sorted(employee_bucket.values(), key=lambda item: item.full_name.lower()),
    )


async def delete_report(report_id: int, db: AsyncSession) -> DeleteResponse:
    report = await db.get(Report, report_id)
    if not report:
        raise HTTPException(status_code=404, detail='Ревизия не найдена.')
    await db.delete(report)
    await db.commit()
    return DeleteResponse(success=True, message='Ревизия удалена.')
