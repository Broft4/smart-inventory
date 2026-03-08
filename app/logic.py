from __future__ import annotations

import hashlib
import hmac
import os
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, inspect, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import CategoryAssignment, CheckResult, Report, User
from app.schemas import (
    AdminReport,
    AssignCategoryResponse,
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


def bootstrap_schema_and_admin(sync_conn) -> None:
    inspector = inspect(sync_conn)
    tables = set(inspector.get_table_names())

    if 'users' in tables:
        columns = {column['name'] for column in inspector.get_columns('users')}
        required = {'id', 'full_name', 'birth_date', 'username', 'password_hash', 'role', 'location', 'is_active', 'created_at'}
        if not required.issubset(columns):
            sync_conn.execute(text('DROP TABLE IF EXISTS users'))

    reset_reports = False
    if 'reports' in tables:
        report_columns = {column['name'] for column in inspector.get_columns('reports')}
        required_report_columns = {'id', 'location', 'report_date', 'status', 'date_created'}
        if not required_report_columns.issubset(report_columns):
            reset_reports = True

    if 'check_results' in tables:
        result_columns = {column['name'] for column in inspector.get_columns('check_results')}
        required_result_columns = {
            'id', 'report_id', 'category_id', 'category_name', 'subcategory_id', 'subcategory_name',
            'target_type', 'target_id', 'target_name', 'expected_qty', 'actual_qty', 'diff',
            'status', 'attempts_used', 'checked_by_user_id', 'checked_by_name_snapshot', 'created_at'
        }
        if not required_result_columns.issubset(result_columns):
            reset_reports = True

    if 'category_assignments' in tables:
        assignment_columns = {column['name'] for column in inspector.get_columns('category_assignments')}
        required_assignment_columns = {
            'id', 'report_id', 'category_id', 'category_name', 'user_id', 'user_full_name_snapshot',
            'is_completed', 'assigned_at', 'completed_at'
        }
        if not required_assignment_columns.issubset(assignment_columns):
            reset_reports = True

    if reset_reports:
        sync_conn.execute(text('DROP TABLE IF EXISTS category_assignments'))
        sync_conn.execute(text('DROP TABLE IF EXISTS check_results'))
        sync_conn.execute(text('DROP TABLE IF EXISTS reports'))

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


async def list_users(db: AsyncSession) -> UserListResponse:
    rows = await db.scalars(select(User).order_by(User.role.desc(), User.full_name.asc()))
    return UserListResponse(users=[user_to_schema(user) for user in rows.all()])


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
        location=(payload.location or None),
        is_active=payload.is_active,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return UserActionResponse(success=True, message='Пользователь создан.', user=user_to_schema(user))


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
        await db.execute(
            update(CategoryAssignment)
            .where(CategoryAssignment.user_id == user.id)
            .values(user_full_name_snapshot=user.full_name)
        )
        await db.execute(
            update(CheckResult)
            .where(CheckResult.checked_by_user_id == user.id)
            .values(checked_by_name_snapshot=user.full_name)
        )

    await db.commit()
    await db.refresh(user)
    return UserActionResponse(success=True, message='Пользователь обновлён.', user=user_to_schema(user))


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


def _normalize_location(location: str) -> str:
    return location.strip().title()


def _get_inventory_for(location: str) -> dict[str, Any]:
    normalized = _normalize_location(location)
    if normalized not in MOCK_INVENTORY:
        raise HTTPException(status_code=404, detail='Неизвестная точка.')
    return MOCK_INVENTORY[normalized]


def _find_category(location: str, category_id: str) -> dict[str, Any]:
    inventory = _get_inventory_for(location)
    for category in inventory['categories']:
        if category['id'] == category_id:
            return category
    raise HTTPException(status_code=404, detail='Категория не найдена.')


def _find_target(location: str, target_id: str) -> tuple[str, str, str | None, str | None, str, str, float]:
    inventory = _get_inventory_for(location)
    for category in inventory['categories']:
        for subcategory in category['subcategories']:
            if subcategory['id'] == target_id:
                expected_total = float(sum(item['expected_qty'] for item in subcategory['items']))
                return (
                    category['id'],
                    category['name'],
                    subcategory['id'],
                    subcategory['name'],
                    'subcategory',
                    subcategory['name'],
                    expected_total,
                )
            for item in subcategory['items']:
                if item['id'] == target_id:
                    return (
                        category['id'],
                        category['name'],
                        subcategory['id'],
                        subcategory['name'],
                        'item',
                        item['name'],
                        float(item['expected_qty']),
                    )
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
    if not raw_category['subcategories']:
        return False
    return all(_subcategory_is_complete(sub, results_by_target)[0] for sub in raw_category['subcategories'])


async def get_or_create_daily_report(location: str, db: AsyncSession) -> Report:
    today = date.today()
    normalized = _normalize_location(location)
    report = await db.scalar(select(Report).where(Report.location == normalized, Report.report_date == today).limit(1))
    if report:
        return report

    report = Report(location=normalized, report_date=today, status='in_progress')
    db.add(report)
    await db.commit()
    await db.refresh(report)
    return report


async def _load_assignments(report_id: int, db: AsyncSession) -> list[CategoryAssignment]:
    return (await db.scalars(select(CategoryAssignment).where(CategoryAssignment.report_id == report_id))).all()


async def _load_results(report_id: int, db: AsyncSession) -> list[CheckResult]:
    return (await db.scalars(select(CheckResult).where(CheckResult.report_id == report_id).order_by(CheckResult.id.asc()))).all()


async def _recalculate_assignment_status(report: Report, category_id: str, db: AsyncSession) -> None:
    assignment = await db.scalar(
        select(CategoryAssignment).where(CategoryAssignment.report_id == report.id, CategoryAssignment.category_id == category_id).limit(1)
    )
    if not assignment:
        return

    raw_category = _find_category(report.location, category_id)
    category_rows = (
        await db.scalars(select(CheckResult).where(CheckResult.report_id == report.id, CheckResult.category_id == category_id))
    ).all()
    results_by_target = {row.target_id: row for row in category_rows}
    is_completed = _category_is_complete(raw_category, results_by_target)
    assignment.is_completed = is_completed
    assignment.completed_at = datetime.utcnow() if is_completed else None
    await db.commit()


async def _refresh_report_status(report: Report, db: AsyncSession) -> None:
    inventory = _get_inventory_for(report.location)
    assignments = await _load_assignments(report.id, db)
    total_categories = len(inventory['categories'])

    report.status = 'completed' if assignments and len(assignments) == total_categories and all(item.is_completed for item in assignments) else 'in_progress'
    await db.commit()


async def get_inventory_data(location: str, db: AsyncSession, user: User) -> InventoryStructureResponse:
    normalized = _normalize_location(location)
    report = await get_or_create_daily_report(normalized, db)
    assignments = await _load_assignments(report.id, db)
    results = await _load_results(report.id, db)

    assignment_by_category = {assignment.category_id: assignment for assignment in assignments}
    rows_by_category_target: dict[str, dict[str, CheckResult]] = defaultdict(dict)
    for row in results:
        rows_by_category_target[row.category_id][row.target_id] = row

    categories: list[CategoryModel] = []
    for raw_category in _get_inventory_for(normalized)['categories']:
        assignment = assignment_by_category.get(raw_category['id'])
        assigned_to_current_user = bool(assignment and assignment.user_id == user.id)
        assigned_to_other = bool(assignment and assignment.user_id != user.id)
        assigned_to = assignment.user_full_name_snapshot if assignment else None
        can_take = assignment is None

        category_results = rows_by_category_target.get(raw_category['id'], {})
        subcategories: list[SubcategoryModel] = []

        first_incomplete_index: int | None = None
        tmp_states: list[tuple[bool, StatusEnum]] = []
        for idx, raw_sub in enumerate(raw_category['subcategories']):
            is_completed, status = _subcategory_is_complete(raw_sub, category_results)
            tmp_states.append((is_completed, status))
            if assigned_to_current_user and not is_completed and first_incomplete_index is None:
                first_incomplete_index = idx

        for idx, raw_sub in enumerate(raw_category['subcategories']):
            is_completed, status = tmp_states[idx]
            items = []
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

                items.append(
                    ItemModel(
                        id=item['id'],
                        name=item['name'],
                        status=item_status,
                        is_final=is_final,
                    )
                )
            is_locked = True
            is_expanded = False
            if assigned_to_current_user:
                if first_incomplete_index is None:
                    is_locked = False
                    is_expanded = False
                else:
                    is_locked = idx > first_incomplete_index
                    is_expanded = idx == first_incomplete_index and not is_completed
                if status == StatusEnum.ORANGE:
                    is_expanded = True

            subcategories.append(
                SubcategoryModel(
                    id=raw_sub['id'],
                    name=raw_sub['name'],
                    is_locked=is_locked,
                    is_completed=is_completed,
                    is_expanded=is_expanded,
                    status=status,
                    items=items,
                )
            )

        is_completed = bool(assignment and assignment.is_completed)
        categories.append(
            CategoryModel(
                id=raw_category['id'],
                name=raw_category['name'],
                is_available=assigned_to_current_user or can_take,
                is_completed=is_completed,
                is_open=assigned_to_current_user and not is_completed,
                subcategories=subcategories,
                assigned_to=assigned_to,
                assigned_to_current_user=assigned_to_current_user,
                can_take=can_take,
                is_blocked_by_other=assigned_to_other,
            )
        )

    return InventoryStructureResponse(
        report_id=report.id,
        location=normalized,
        report_date=report.report_date.strftime('%d.%m.%Y'),
        categories=categories,
    )


async def assign_category_to_user(report_id: int, category_id: str, db: AsyncSession, user: User) -> AssignCategoryResponse:
    if not user.location:
        raise HTTPException(status_code=403, detail='Сотруднику не назначена точка.')

    report = await db.get(Report, report_id)
    if not report or report.location != user.location:
        raise HTTPException(status_code=404, detail='Ревизия не найдена.')

    category = _find_category(report.location, category_id)
    existing = await db.scalar(
        select(CategoryAssignment).where(CategoryAssignment.report_id == report_id, CategoryAssignment.category_id == category_id).limit(1)
    )
    if existing:
        if existing.user_id == user.id:
            return AssignCategoryResponse(success=True, message='Категория уже закреплена за вами.')
        raise HTTPException(status_code=400, detail=f'Категория уже закреплена за сотрудником {existing.user_full_name_snapshot}.')

    assignment = CategoryAssignment(
        report_id=report_id,
        category_id=category_id,
        category_name=category['name'],
        user_id=user.id,
        user_full_name_snapshot=user.full_name,
        is_completed=False,
    )
    db.add(assignment)
    await db.commit()
    await _refresh_report_status(report, db)
    return AssignCategoryResponse(success=True, message='Категория закреплена за вами.')


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
    existing = await db.scalar(
        select(CheckResult).where(CheckResult.report_id == report_id, CheckResult.target_id == target_id).limit(1)
    )
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
        db.add(
            CheckResult(
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
            )
        )


async def verify_item_or_category(data: VerifyRequest, db: AsyncSession, checked_by_user: User) -> VerifyResponse:
    report = await db.get(Report, data.report_id)
    if not report:
        raise HTTPException(status_code=404, detail='Ревизия не найдена.')
    if checked_by_user.location != report.location:
        raise HTTPException(status_code=403, detail='Ревизия относится к другой точке.')

    category_id, category_name, subcategory_id, subcategory_name, target_type, target_name, expected_qty = _find_target(report.location, data.target_id)
    assignment = await db.scalar(
        select(CategoryAssignment).where(CategoryAssignment.report_id == report.id, CategoryAssignment.category_id == category_id).limit(1)
    )
    if not assignment or assignment.user_id != checked_by_user.id:
        raise HTTPException(status_code=403, detail='Эта категория закреплена не за вами.')

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
        await _recalculate_assignment_status(report, category_id, db)
        await _refresh_report_status(report, db)
        return VerifyResponse(is_correct=True, attempts_left=0, message='Верно!', expand_category=False)

    attempts_left = max(0, 3 - data.attempt_number)
    if attempts_left > 0:
        return VerifyResponse(
            is_correct=False,
            attempts_left=attempts_left,
            message=f'Неверно. Осталось {attempts_left} попытк(и).',
            expand_category=False,
        )

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
    await _recalculate_assignment_status(report, category_id, db)
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
        return False, 'Общая ревизия завершится автоматически, когда все категории точки будут проверены.'
    return True, 'Ревизия завершена.'


async def get_reports_history(location: str, db: AsyncSession) -> ReportHistoryResponse:
    normalized = _normalize_location(location)
    reports = (
        await db.scalars(
            select(Report).where(Report.location == normalized).order_by(Report.report_date.desc(), Report.id.desc())
        )
    ).all()
    history = [
        ReportHistoryItem(
            report_id=report.id,
            date=_format_moscow_datetime(report.date_created),
            status=report.status,
            label=f"{_format_moscow_datetime(report.date_created)} — {_report_status_label(report.status)}",
        )
        for report in reports
    ]
    return ReportHistoryResponse(location=normalized, reports=history)


async def get_admin_report(location: str, db: AsyncSession, report_id: int | None = None) -> AdminReport:
    normalized = _normalize_location(location)
    report: Report | None = None
    if report_id is not None:
        report = await db.get(Report, report_id)
        if report and report.location != normalized:
            report = None

    if report is None:
        report = await db.scalar(
            select(Report).where(Report.location == normalized).order_by(Report.report_date.desc(), Report.id.desc()).limit(1)
        )

    if not report:
        return AdminReport(report_id=None, date='-', location=normalized, status='-', categories=[], total_plus=0.0, total_minus=0.0, employees=[])

    assignments = await _load_assignments(report.id, db)
    results = await _load_results(report.id, db)
    assignment_by_category = {assignment.category_id: assignment for assignment in assignments}
    rows_by_category_target: dict[str, dict[str, CheckResult]] = defaultdict(dict)
    for row in results:
        rows_by_category_target[row.category_id][row.target_id] = row

    categories: list[CategoryResult] = []
    grouped_problem_items: dict[str, list[DiscrepancyItem]] = defaultdict(list)

    for row in results:
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

    for raw_category in _get_inventory_for(normalized)['categories']:
        assignment = assignment_by_category.get(raw_category['id'])
        category_rows = rows_by_category_target.get(raw_category['id'], {})
        if grouped_problem_items.get(raw_category['name']):
            status_value = StatusEnum.RED
        elif assignment and assignment.is_completed:
            status_value = StatusEnum.GREEN
        elif assignment:
            status_value = StatusEnum.ORANGE
        else:
            status_value = StatusEnum.GREY

        categories.append(
            CategoryResult(
                name=raw_category['name'],
                status=status_value,
                assigned_to=assignment.user_full_name_snapshot if assignment else None,
                problem_items=grouped_problem_items.get(raw_category['name'], []),
            )
        )

    employee_map: dict[str, EmployeeReportSummary] = {}
    discrepancy_count_by_employee: dict[str, int] = defaultdict(int)
    for row in results:
        if row.target_type == 'item' and row.status == 'red' and row.checked_by_name_snapshot:
            discrepancy_count_by_employee[row.checked_by_name_snapshot] += 1

    for assignment in assignments:
        key = assignment.user_full_name_snapshot
        employee_map.setdefault(key, EmployeeReportSummary(full_name=key))
        employee_map[key].categories.append(assignment.category_name)
        if assignment.is_completed:
            employee_map[key].completed_categories += 1

    for key, count in discrepancy_count_by_employee.items():
        employee_map.setdefault(key, EmployeeReportSummary(full_name=key))
        employee_map[key].discrepancy_items = count

    total_plus = sum(max(float(row.diff or 0), 0.0) for row in results if row.target_type == 'item' and row.status == 'red')
    total_minus = abs(sum(min(float(row.diff or 0), 0.0) for row in results if row.target_type == 'item' and row.status == 'red'))

    return AdminReport(
        report_id=report.id,
        date=_format_moscow_datetime(report.date_created),
        location=report.location,
        status=_report_status_label(report.status),
        categories=categories,
        total_plus=total_plus,
        total_minus=total_minus,
        employees=sorted(employee_map.values(), key=lambda item: item.full_name.lower()),
    )


async def delete_report(report_id: int, db: AsyncSession) -> DeleteResponse:
    report = await db.get(Report, report_id)
    if not report:
        raise HTTPException(status_code=404, detail='Ревизия не найдена.')
    await db.delete(report)
    await db.commit()
    return DeleteResponse(success=True, message='Ревизия удалена.')


async def get_me_response(user: User | None) -> MeResponse:
    if not user:
        return MeResponse(authenticated=False, user=None)
    return MeResponse(authenticated=True, user=user_to_schema(user))


def _format_moscow_datetime(dt: datetime | None) -> str:
    if dt is None:
        return '-'
    return (dt + MSK_SHIFT).strftime('%d.%m.%Y %H:%M')


def _report_status_label(status_value: str) -> str:
    return 'Завершена' if status_value == 'completed' else 'В процессе'
