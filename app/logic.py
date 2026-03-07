from __future__ import annotations

import hashlib
import hmac
import os
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import CheckResult, Report, User
from app.schemas import (
    AdminReport,
    CategoryModel,
    CategoryResult,
    DeleteResponse,
    DiscrepancyItem,
    InventoryStructureResponse,
    ItemModel,
    MeResponse,
    ReportHistoryItem,
    ReportHistoryResponse,
    RoleEnum,
    StatusEnum,
    SubcategoryModel,
    UserCreateRequest,
    UserInfo,
    UserListResponse,
    UserResponse,
    UserUpdateRequest,
    VerifyRequest,
    VerifyResponse,
    UserActionResponse,
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

    legacy_tables = {'category_results', 'discrepancies'}
    if 'reports' in tables:
        report_columns = {column['name'] for column in inspector.get_columns('reports')}
        required_report_columns = {'id', 'location', 'report_date', 'status', 'date_created'}
        if not required_report_columns.issubset(report_columns):
            sync_conn.execute(text('DROP TABLE IF EXISTS check_results'))
            sync_conn.execute(text('DROP TABLE IF EXISTS discrepancies'))
            sync_conn.execute(text('DROP TABLE IF EXISTS category_results'))
            sync_conn.execute(text('DROP TABLE IF EXISTS reports'))
    elif legacy_tables & tables:
        sync_conn.execute(text('DROP TABLE IF EXISTS check_results'))
        sync_conn.execute(text('DROP TABLE IF EXISTS discrepancies'))
        sync_conn.execute(text('DROP TABLE IF EXISTS category_results'))

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

    duplicate = await db.scalar(
        select(User).where(User.username == payload.username, User.id != user_id).limit(1)
    )
    if duplicate:
        raise HTTPException(status_code=400, detail='Пользователь с таким логином уже существует.')


    if user.id == current_admin_id and payload.role != RoleEnum.ADMIN:
        raise HTTPException(status_code=400, detail='Нельзя снять роль admin у своего аккаунта.')

    user.full_name = payload.full_name.strip()
    user.birth_date = payload.birth_date
    user.username = payload.username.strip()
    user.role = payload.role.value
    user.location = payload.location or None
    user.is_active = payload.is_active
    if payload.password:
        user.password_hash = hash_password(payload.password)

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

    await db.delete(user)
    await db.commit()
    return DeleteResponse(success=True, message='Пользователь удалён.')


async def get_or_create_daily_report(location: str, db: AsyncSession) -> Report:
    today = date.today()
    report = await db.scalar(
        select(Report).where(Report.location == location, Report.report_date == today).limit(1)
    )
    if report:
        return report

    report = Report(location=location, report_date=today, status='in_progress')
    db.add(report)
    await db.commit()
    await db.refresh(report)
    return report


async def get_inventory_data(location: str, db: AsyncSession) -> InventoryStructureResponse:
    normalized = location.strip().title()
    if normalized not in MOCK_INVENTORY:
        raise HTTPException(status_code=404, detail='Неизвестная точка.')

    report = await get_or_create_daily_report(normalized, db)

    categories: list[CategoryModel] = []
    for cat_index, raw_category in enumerate(MOCK_INVENTORY[normalized]['categories']):
        subcategories: list[SubcategoryModel] = []
        for sub_index, raw_sub in enumerate(raw_category['subcategories']):
            items = [
                ItemModel(
                    id=item['id'],
                    name=item['name'],
                    expected_qty=float(item['expected_qty']),
                )
                for item in raw_sub['items']
            ]
            subcategories.append(
                SubcategoryModel(
                    id=raw_sub['id'],
                    name=raw_sub['name'],
                    expected_total=sum(item.expected_qty for item in items),
                    is_locked=not (cat_index == 0 and sub_index == 0),
                    is_expanded=cat_index == 0 and sub_index == 0,
                    items=items,
                )
            )

        categories.append(
            CategoryModel(
                id=raw_category['id'],
                name=raw_category['name'],
                is_available=cat_index == 0,
                is_open=cat_index == 0,
                subcategories=subcategories,
            )
        )

    return InventoryStructureResponse(report_id=report.id, location=normalized, categories=categories)


def _find_target(target_id: str) -> tuple[str, str, str, float]:
    for location_data in MOCK_INVENTORY.values():
        for category in location_data['categories']:
            for subcategory in category['subcategories']:
                if subcategory['id'] == target_id:
                    expected_total = sum(item['expected_qty'] for item in subcategory['items'])
                    return category['name'], subcategory['name'], 'subcategory', float(expected_total)
                for item in subcategory['items']:
                    if item['id'] == target_id:
                        return category['name'], subcategory['name'], 'item', float(item['expected_qty'])
    raise HTTPException(status_code=404, detail='Цель проверки не найдена.')


async def verify_item_or_category(data: VerifyRequest, db: AsyncSession, checked_by_user_id: int | None = None) -> VerifyResponse:
    category_name, subcategory_name, target_type, expected_qty = _find_target(data.target_id)
    is_correct = abs(data.quantity - expected_qty) < 1e-9

    if is_correct:
        db.add(
            CheckResult(
                report_id=data.report_id,
                category_name=category_name,
                subcategory_name=subcategory_name,
                target_type=target_type,
                target_id=data.target_id,
                target_name=subcategory_name if target_type == 'subcategory' else next(
                    item['name']
                    for loc in MOCK_INVENTORY.values()
                    for cat in loc['categories']
                    for sub in cat['subcategories']
                    for item in sub['items']
                    if item['id'] == data.target_id
                ),
                expected_qty=expected_qty,
                actual_qty=data.quantity,
                diff=0,
                status='green',
                attempts_used=data.attempt_number,
                checked_by_user_id=checked_by_user_id,
            )
        )
        await db.commit()
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
    target_name = subcategory_name if target_type == 'subcategory' else next(
        item['name']
        for loc in MOCK_INVENTORY.values()
        for cat in loc['categories']
        for sub in cat['subcategories']
        for item in sub['items']
        if item['id'] == data.target_id
    )
    db.add(
        CheckResult(
            report_id=data.report_id,
            category_name=category_name,
            subcategory_name=subcategory_name,
            target_type=target_type,
            target_id=data.target_id,
            target_name=target_name,
            expected_qty=expected_qty,
            actual_qty=data.quantity,
            diff=float(data.quantity - expected_qty),
            status=status_value,
            attempts_used=data.attempt_number,
            checked_by_user_id=checked_by_user_id,
        )
    )
    await db.commit()
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
    report.status = 'completed'
    await db.commit()
    return True, 'Ревизия завершена.'


async def get_reports_history(location: str, db: AsyncSession) -> ReportHistoryResponse:
    normalized = location.strip().title()
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
    normalized = location.strip().title()
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
        return AdminReport(
            report_id=None,
            date='-',
            location=normalized,
            status='-',
            categories=[],
            total_plus=0.0,
            total_minus=0.0,
        )

    rows = (
        await db.scalars(select(CheckResult).where(CheckResult.report_id == report.id).order_by(CheckResult.id.asc()))
    ).all()

    grouped_problem_items: dict[str, list[DiscrepancyItem]] = defaultdict(list)
    category_status_map: dict[str, StatusEnum] = defaultdict(lambda: StatusEnum.GREY)

    for row in rows:
        row_status = _status_from_value(row.status)
        if row_status == StatusEnum.RED:
            category_status_map[row.category_name] = StatusEnum.RED
        elif row_status == StatusEnum.ORANGE and category_status_map[row.category_name] != StatusEnum.RED:
            category_status_map[row.category_name] = StatusEnum.ORANGE
        elif row_status == StatusEnum.GREEN and category_status_map[row.category_name] == StatusEnum.GREY:
            category_status_map[row.category_name] = StatusEnum.GREEN

        if row.target_type == 'item' and row.status == 'red':
            checked_by = None
            if row.checked_by_user_id:
                user = await db.get(User, row.checked_by_user_id)
                checked_by = user.full_name if user else None
            grouped_problem_items[row.category_name].append(
                DiscrepancyItem(
                    name=row.target_name,
                    expected=float(row.expected_qty),
                    actual=float(row.actual_qty or 0),
                    diff=float(row.diff or 0),
                    checked_by=checked_by,
                )
            )

    categories = []
    for raw_category in MOCK_INVENTORY.get(normalized, {}).get('categories', []):
        categories.append(
            CategoryResult(
                name=raw_category['name'],
                status=category_status_map[raw_category['name']],
                problem_items=grouped_problem_items.get(raw_category['name'], []),
            )
        )

    total_plus = sum(max(float(row.diff or 0), 0.0) for row in rows if row.status == 'red')
    total_minus = abs(sum(min(float(row.diff or 0), 0.0) for row in rows if row.status == 'red'))

    return AdminReport(
        report_id=report.id,
        date=_format_moscow_datetime(report.date_created),
        location=report.location,
        status=_report_status_label(report.status),
        categories=categories,
        total_plus=total_plus,
        total_minus=total_minus,
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


def _status_from_value(value: str) -> StatusEnum:
    if value == 'green':
        return StatusEnum.GREEN
    if value == 'orange':
        return StatusEnum.ORANGE
    if value == 'red':
        return StatusEnum.RED
    return StatusEnum.GREY


def _report_status_label(status_value: str) -> str:
    return 'Завершена' if status_value == 'completed' else 'В процессе'
