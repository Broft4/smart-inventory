from __future__ import annotations

import asyncio
import hashlib
import httpx
import hmac
import os
import logging
from collections import defaultdict
from dataclasses import dataclass
from time import monotonic
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import and_, delete, func, inspect, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.moysklad import DEFAULT_CATEGORY_NAME, DEFAULT_SUBCATEGORY_NAME, ms_client
from app.models import AdminLocationAccess, CategoryAssignment, CheckResult, LocationPoint, Report, ReportEmployeeCompletion, ReportEmployeeStart, ReportTargetSnapshot, SelectionCycle, SelectionTarget, User, VerifyAttemptProgress
logger = logging.getLogger(__name__)


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
    DeleteResponse,
    DiscrepancyItem,
    CompletedSubcategoryInfo,
    InProgressSubcategoryInfo,
    EmployeeReportSummary,
    InventoryStructureResponse,
    ItemModel,
    LocationListResponse,
    LocationPointModel,
    MeResponse,
    ReopenEmployeeAccessResponse,
    ReportHistoryItem,
    ReportHistoryResponse,
    ResetSelectionCycleResponse,
    SaveCycleTargetsRequest,
    SaveCycleTargetsResponse,
    RoleEnum,
    StartReportResponse,
    StatusEnum,
    StoreListResponse,
    UpdateLocationRequest,
    UpdateLocationResponse,
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



@dataclass(slots=True)
class RuntimeCacheEntry:
    value: Any
    expires_at: float
    inventory_identity: int | None = None


STRIPPED_INVENTORY_CACHE_TTL = max(15, int(settings.ms_inventory_cache_ttl_seconds or 120))
TARGET_LOOKUP_CACHE_TTL = max(60, STRIPPED_INVENTORY_CACHE_TTL)

_stripped_inventory_cache: dict[str, RuntimeCacheEntry] = {}
_target_lookup_cache: dict[str, RuntimeCacheEntry] = {}

DAILY_REPORT_TYPE = 'daily'
FINAL_REPORT_TYPE = 'final'


def _get_cached_stripped_inventory(location: str, raw_inventory: dict[str, Any]) -> dict[str, Any]:
    cache_key = _normalize_location(location)
    inventory_identity = id(raw_inventory)
    cached = _stripped_inventory_cache.get(cache_key)
    now = monotonic()
    if cached and cached.inventory_identity == inventory_identity and cached.expires_at > now:
        return cached.value

    stripped = _strip_ignored_inventory_branches(raw_inventory)
    _stripped_inventory_cache[cache_key] = RuntimeCacheEntry(
        value=stripped,
        expires_at=now + STRIPPED_INVENTORY_CACHE_TTL,
        inventory_identity=inventory_identity,
    )
    return stripped


def _build_target_lookup(inventory: dict[str, Any]) -> dict[str, tuple[str, str, str | None, str | None, str, str, float]]:
    lookup: dict[str, tuple[str, str, str | None, str | None, str, str, float]] = {}
    for category in inventory.get('categories', []):
        for subcategory in category.get('subcategories', []):
            expected_total = float(sum(float(item.get('expected_qty') or 0) for item in subcategory.get('items', [])))
            lookup[subcategory['id']] = (
                category['id'],
                category['name'],
                subcategory['id'],
                subcategory['name'],
                'subcategory',
                subcategory['name'],
                expected_total,
            )
            for item in subcategory.get('items', []):
                lookup[item['id']] = (
                    category['id'],
                    category['name'],
                    subcategory['id'],
                    subcategory['name'],
                    'item',
                    item['name'],
                    float(item.get('expected_qty') or 0),
                )
    return lookup


def _get_target_lookup(location: str, inventory: dict[str, Any]) -> dict[str, tuple[str, str, str | None, str | None, str, str, float]]:
    cache_key = _normalize_location(location)
    inventory_identity = id(inventory)
    cached = _target_lookup_cache.get(cache_key)
    now = monotonic()
    if cached and cached.inventory_identity == inventory_identity and cached.expires_at > now:
        return cached.value

    lookup = _build_target_lookup(inventory)
    _target_lookup_cache[cache_key] = RuntimeCacheEntry(
        value=lookup,
        expires_at=now + TARGET_LOOKUP_CACHE_TTL,
        inventory_identity=inventory_identity,
    )
    return lookup


def _invalidate_runtime_inventory_cache(location: str | None = None) -> None:
    if location is None:
        _stripped_inventory_cache.clear()
        _target_lookup_cache.clear()
        return

    normalized = _normalize_location(location)
    _stripped_inventory_cache.pop(normalized, None)
    _target_lookup_cache.pop(normalized, None)


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


def _strip_ignored_inventory_branches(inventory: dict[str, Any]) -> dict[str, Any]:
    categories: list[dict[str, Any]] = []
    for category in inventory.get('categories', []):
        if category.get('name') == DEFAULT_CATEGORY_NAME:
            continue
        filtered_subcategories: list[dict[str, Any]] = []
        for subcategory in category.get('subcategories', []):
            if subcategory.get('name') == DEFAULT_SUBCATEGORY_NAME:
                continue
            filtered_subcategories.append({
                'id': subcategory['id'],
                'name': subcategory['name'],
                'items': [dict(item) for item in subcategory.get('items', [])],
            })
        if filtered_subcategories:
            categories.append({
                'id': category['id'],
                'name': category['name'],
                'subcategories': filtered_subcategories,
            })
    return {'location': inventory.get('location'), 'categories': categories}


async def _ensure_default_location_points(db: AsyncSession) -> None:
    existing_count = await db.scalar(select(func.count()).select_from(LocationPoint))
    if (existing_count or 0) > 0:
        return

    candidates: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for name, store_id in [
        (settings.store_dubna, settings.store_dubna_id),
        (settings.store_dmitrov, settings.store_dmitrov_id),
    ]:
        normalized = _normalize_location(name or '') if name else ''
        if normalized and normalized not in seen:
            candidates.append((normalized, store_id))
            seen.add(normalized)

    if not candidates:
        for name in sorted(MOCK_INVENTORY.keys()):
            normalized = _normalize_location(name)
            if normalized not in seen:
                candidates.append((normalized, None))
                seen.add(normalized)

    for name, store_id in candidates:
        db.add(LocationPoint(
            name=name,
            ms_token=(settings.moysklad_token or '').strip() or None,
            ms_store_id=store_id,
            ms_store_name=name,
        ))
    await db.commit()


def _format_moscow_datetime(dt: datetime | None) -> str:
    if not dt:
        return '-'
    return (dt + MSK_SHIFT).strftime('%d.%m.%Y %H:%M')


def _report_status_label(status: str) -> str:
    if status == 'created':
        return 'Не начата'
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
        elif 'report_type' not in cols:
            sync_conn.execute(text("ALTER TABLE reports ADD COLUMN report_type VARCHAR(20) NOT NULL DEFAULT 'daily'"))

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

    if 'admin_location_access' in tables:
        cols = {c['name'] for c in inspector.get_columns('admin_location_access')}
        required = {'id', 'admin_user_id', 'location_point_id', 'granted_by_user_id', 'created_at'}
        if not required.issubset(cols):
            sync_conn.execute(text('DROP TABLE IF EXISTS admin_location_access'))

    if 'verify_attempt_progress' in tables:
        cols = {c['name'] for c in inspector.get_columns('verify_attempt_progress')}
        required = {'id', 'report_id', 'target_type', 'target_id', 'checked_by_user_id', 'attempts_used', 'created_at', 'updated_at'}
        if not required.issubset(cols):
            sync_conn.execute(text('DROP TABLE IF EXISTS verify_attempt_progress'))

    if 'report_target_snapshots' in tables:
        cols = {c['name'] for c in inspector.get_columns('report_target_snapshots')}
        required = {'id', 'report_id', 'category_id', 'category_name', 'subcategory_id', 'subcategory_name', 'target_type', 'target_id', 'target_name', 'assigned_user_id_snapshot', 'assigned_user_name_snapshot', 'created_at'}
        if not required.issubset(cols):
            sync_conn.execute(text('DROP TABLE IF EXISTS report_target_snapshots'))

    if 'report_employee_completions' in tables:
        cols = {c['name'] for c in inspector.get_columns('report_employee_completions')}
        required = {'id', 'report_id', 'user_id', 'user_full_name_snapshot', 'finished_at'}
        if not required.issubset(cols):
            sync_conn.execute(text('DROP TABLE IF EXISTS report_employee_completions'))

    if 'report_employee_starts' in tables:
        cols = {c['name'] for c in inspector.get_columns('report_employee_starts')}
        required = {'id', 'report_id', 'user_id', 'user_full_name_snapshot', 'started_at'}
        if not required.issubset(cols):
            sync_conn.execute(text('DROP TABLE IF EXISTS report_employee_starts'))

    if reset_reports:
        sync_conn.execute(text('DROP TABLE IF EXISTS check_results'))
        sync_conn.execute(text('DROP TABLE IF EXISTS reports'))

    if reset_assignments:
        sync_conn.execute(text('DROP TABLE IF EXISTS category_assignments'))
        sync_conn.execute(text('DROP TABLE IF EXISTS selection_cycles'))

    from app.database import Base
    Base.metadata.create_all(sync_conn)


async def _assign_admin_location_access_by_ids(admin_user_id: int, location_ids: list[int], db: AsyncSession, granted_by_user_id: int | None = None) -> None:
    await db.execute(delete(AdminLocationAccess).where(AdminLocationAccess.admin_user_id == admin_user_id))
    unique_ids: list[int] = []
    seen: set[int] = set()
    for location_id in location_ids:
        normalized_id = int(location_id)
        if normalized_id not in seen:
            unique_ids.append(normalized_id)
            seen.add(normalized_id)
    for location_id in unique_ids:
        db.add(AdminLocationAccess(
            admin_user_id=admin_user_id,
            location_point_id=location_id,
            granted_by_user_id=granted_by_user_id,
        ))


async def _get_admin_location_rows(admin_user_id: int, db: AsyncSession) -> list[LocationPoint]:
    return (
        await db.scalars(
            select(LocationPoint)
            .join(AdminLocationAccess, AdminLocationAccess.location_point_id == LocationPoint.id)
            .where(AdminLocationAccess.admin_user_id == admin_user_id)
            .order_by(LocationPoint.name.asc())
        )
    ).all()


async def get_user_accessible_locations(user: User, db: AsyncSession) -> list[str]:
    if user.role == RoleEnum.SUPERADMIN.value:
        rows = (await db.scalars(select(LocationPoint).order_by(LocationPoint.name.asc()))).all()
        return [row.name for row in rows]
    if user.role == RoleEnum.ADMIN.value:
        return [row.name for row in await _get_admin_location_rows(user.id, db)]
    if user.location:
        return [_normalize_location(user.location)]
    return []


async def get_user_accessible_location_ids(user: User, db: AsyncSession) -> list[int]:
    if user.role == RoleEnum.SUPERADMIN.value:
        rows = (await db.scalars(select(LocationPoint.id).order_by(LocationPoint.name.asc()))).all()
        return [int(row) for row in rows]
    if user.role == RoleEnum.ADMIN.value:
        rows = (await db.scalars(
            select(AdminLocationAccess.location_point_id)
            .where(AdminLocationAccess.admin_user_id == user.id)
            .order_by(AdminLocationAccess.location_point_id.asc())
        )).all()
        return [int(row) for row in rows]
    if not user.location:
        return []
    location_id = await db.scalar(select(LocationPoint.id).where(LocationPoint.name == _normalize_location(user.location)).limit(1))
    return [int(location_id)] if location_id else []


async def user_has_location_access(user: User, location: str, db: AsyncSession) -> bool:
    normalized = _normalize_location(location)
    if user.role == RoleEnum.SUPERADMIN.value:
        return True
    if user.role == RoleEnum.ADMIN.value:
        return normalized in set(await get_user_accessible_locations(user, db))
    return normalized == _normalize_location(user.location or '')


async def ensure_user_can_access_location(user: User, location: str, db: AsyncSession) -> None:
    if not await user_has_location_access(user, location, db):
        raise HTTPException(status_code=403, detail='Нет доступа к выбранной точке.')


async def _validate_location_ids(location_ids: list[int], db: AsyncSession) -> list[LocationPoint]:
    unique_ids = sorted({int(location_id) for location_id in location_ids})
    if not unique_ids:
        return []
    rows = (await db.scalars(select(LocationPoint).where(LocationPoint.id.in_(unique_ids)).order_by(LocationPoint.name.asc()))).all()
    if len(rows) != len(unique_ids):
        raise HTTPException(status_code=400, detail='Выбраны несуществующие точки.')
    return rows


async def _build_user_response(user: User, db: AsyncSession) -> UserResponse:
    admin_rows = await _get_admin_location_rows(user.id, db) if user.role == RoleEnum.ADMIN.value else []
    return UserResponse(
        id=user.id,
        full_name=user.full_name,
        birth_date=user.birth_date,
        username=user.username,
        role=RoleEnum(user.role),
        location=user.location,
        is_active=user.is_active,
        admin_location_ids=[row.id for row in admin_rows],
        admin_locations=[row.name for row in admin_rows],
    )


async def _migrate_admin_location_access(db: AsyncSession) -> None:
    admins = (await db.scalars(select(User).where(User.role == RoleEnum.ADMIN.value))).all()
    changed = False
    for admin in admins:
        access_count = await db.scalar(select(func.count()).select_from(AdminLocationAccess).where(AdminLocationAccess.admin_user_id == admin.id))
        if (access_count or 0) > 0:
            continue
        if not admin.location:
            continue
        point = await db.scalar(select(LocationPoint).where(LocationPoint.name == _normalize_location(admin.location)).limit(1))
        if not point:
            continue
        db.add(AdminLocationAccess(admin_user_id=admin.id, location_point_id=point.id, granted_by_user_id=None))
        changed = True
    if changed:
        await db.commit()


async def ensure_default_admin(db: AsyncSession) -> None:
    await _ensure_default_location_points(db)

    superadmin = await db.scalar(select(User).where(User.role == RoleEnum.SUPERADMIN.value).limit(1))
    if not superadmin:
        default_user = await db.scalar(select(User).where(User.username == settings.default_admin_username).limit(1))
        if default_user:
            default_user.role = RoleEnum.SUPERADMIN.value
            default_user.location = None
            default_user.is_active = True
            await db.commit()
        else:
            existing_admin = await db.scalar(select(User).where(User.role == RoleEnum.ADMIN.value).order_by(User.id.asc()).limit(1))
            if existing_admin:
                existing_admin.role = RoleEnum.SUPERADMIN.value
                existing_admin.location = None
                existing_admin.is_active = True
                await db.commit()
            else:
                admin_user = User(
                    full_name=settings.default_admin_full_name,
                    birth_date=date.fromisoformat(settings.default_admin_birth_date),
                    username=settings.default_admin_username,
                    password_hash=hash_password(settings.default_admin_password),
                    role=RoleEnum.SUPERADMIN.value,
                    location=None,
                    is_active=True,
                )
                db.add(admin_user)
                await db.commit()

    await _migrate_admin_location_access(db)


def user_to_schema(user: User) -> UserInfo:
    return UserInfo(
        id=user.id,
        full_name=user.full_name,
        birth_date=user.birth_date,
        username=user.username,
        role=RoleEnum(user.role),
        location=user.location,
        is_active=user.is_active,
        admin_location_ids=[],
        admin_locations=[],
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
    if ms_client.enabled:
        await ms_client.prewarm_inventory(normalized)


async def list_users(db: AsyncSession, current_user: User) -> UserListResponse:
    accessible_locations = set(await get_user_accessible_locations(current_user, db))

    if current_user.role == RoleEnum.SUPERADMIN.value:
        users = (await db.scalars(select(User))).all()
    else:
        conditions = [User.id == current_user.id]
        if accessible_locations:
            conditions.append(and_(User.role == RoleEnum.EMPLOYEE.value, User.location.in_(accessible_locations)))
        users = (await db.scalars(select(User).where(or_(*conditions)))).all()

    role_order = {
        RoleEnum.SUPERADMIN.value: 0,
        RoleEnum.ADMIN.value: 1,
        RoleEnum.EMPLOYEE.value: 2,
    }
    ordered_users = sorted(users, key=lambda item: (role_order.get(item.role, 99), item.full_name.lower()))
    return UserListResponse(users=[await _build_user_response(user, db) for user in ordered_users])


async def create_user(payload: UserCreateRequest, db: AsyncSession, current_user: User) -> UserActionResponse:
    existing = await db.scalar(select(User).where(User.username == payload.username).limit(1))
    if existing:
        raise HTTPException(status_code=400, detail='Пользователь с таким логином уже существует.')

    requested_role = payload.role.value
    normalized_location = _normalize_location(payload.location) if payload.location else None
    requested_access_ids = sorted({int(location_id) for location_id in payload.admin_location_ids})

    if current_user.role == RoleEnum.ADMIN.value and requested_role != RoleEnum.EMPLOYEE.value:
        raise HTTPException(status_code=403, detail='Обычный администратор может создавать только сотрудников.')
    if requested_role == RoleEnum.SUPERADMIN.value and current_user.role != RoleEnum.SUPERADMIN.value:
        raise HTTPException(status_code=403, detail='Создавать главного администратора может только главный администратор.')
    if requested_role == RoleEnum.ADMIN.value and current_user.role != RoleEnum.SUPERADMIN.value:
        raise HTTPException(status_code=403, detail='Создавать администраторов может только главный администратор.')

    if requested_role == RoleEnum.EMPLOYEE.value:
        if not normalized_location:
            raise HTTPException(status_code=400, detail='Сотруднику нужно назначить точку.')
        location_point = await db.scalar(select(LocationPoint).where(LocationPoint.name == normalized_location).limit(1))
        if not location_point:
            raise HTTPException(status_code=400, detail='Выбрана несуществующая точка.')
        if current_user.role == RoleEnum.ADMIN.value:
            await ensure_user_can_access_location(current_user, normalized_location, db)
    else:
        normalized_location = None

    admin_location_rows: list[LocationPoint] = []
    if requested_role == RoleEnum.ADMIN.value:
        admin_location_rows = await _validate_location_ids(requested_access_ids, db)
        if not admin_location_rows:
            raise HTTPException(status_code=400, detail='Администратору нужно назначить хотя бы одну точку.')

    user = User(
        full_name=payload.full_name.strip(),
        birth_date=payload.birth_date,
        username=payload.username.strip(),
        password_hash=hash_password(payload.password),
        role=requested_role,
        location=normalized_location,
        is_active=payload.is_active,
    )
    db.add(user)
    await db.flush()

    if requested_role == RoleEnum.ADMIN.value:
        await _assign_admin_location_access_by_ids(user.id, [row.id for row in admin_location_rows], db, granted_by_user_id=current_user.id)

    await db.commit()
    await db.refresh(user)
    return UserActionResponse(success=True, message='Пользователь создан.', user=await _build_user_response(user, db))


async def update_user(user_id: int, payload: UserUpdateRequest, db: AsyncSession, current_user: User) -> UserActionResponse:
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail='Пользователь не найден.')

    duplicate = await db.scalar(select(User).where(User.username == payload.username, User.id != user_id).limit(1))
    if duplicate:
        raise HTTPException(status_code=400, detail='Пользователь с таким логином уже существует.')

    requested_role = payload.role.value
    normalized_location = _normalize_location(payload.location) if payload.location else None
    requested_access_ids = sorted({int(location_id) for location_id in payload.admin_location_ids})

    if current_user.role == RoleEnum.ADMIN.value:
        accessible_locations = set(await get_user_accessible_locations(current_user, db))
        if user.id == current_user.id:
            if user.role != RoleEnum.ADMIN.value or requested_role != RoleEnum.ADMIN.value:
                raise HTTPException(status_code=400, detail='Нельзя снять роль admin у своего аккаунта.')
        else:
            if user.role != RoleEnum.EMPLOYEE.value:
                raise HTTPException(status_code=403, detail='Обычный администратор может редактировать только сотрудников.')
            if user.location and _normalize_location(user.location) not in accessible_locations:
                raise HTTPException(status_code=403, detail='Нет доступа к пользователю из другой точки.')
            if requested_role != RoleEnum.EMPLOYEE.value:
                raise HTTPException(status_code=403, detail='Обычный администратор не может менять роль сотрудника.')
            if not normalized_location:
                raise HTTPException(status_code=400, detail='Сотруднику нужно назначить точку.')
            if normalized_location not in accessible_locations:
                raise HTTPException(status_code=403, detail='Нельзя назначить сотруднику чужую точку.')

    if requested_role == RoleEnum.SUPERADMIN.value and current_user.role != RoleEnum.SUPERADMIN.value:
        raise HTTPException(status_code=403, detail='Назначать роль главного администратора может только главный администратор.')
    if requested_role == RoleEnum.ADMIN.value and current_user.role != RoleEnum.SUPERADMIN.value and user.id != current_user.id:
        raise HTTPException(status_code=403, detail='Назначать роль администратора может только главный администратор.')

    if user.id == current_user.id and user.role == RoleEnum.SUPERADMIN.value and requested_role != RoleEnum.SUPERADMIN.value:
        raise HTTPException(status_code=400, detail='Нельзя снять роль главного администратора у своего аккаунта.')

    old_superadmin = user.role == RoleEnum.SUPERADMIN.value

    admin_location_rows: list[LocationPoint] = []
    if requested_role == RoleEnum.ADMIN.value:
        if current_user.role == RoleEnum.SUPERADMIN.value:
            admin_location_rows = await _validate_location_ids(requested_access_ids, db)
            if user.id != current_user.id and not admin_location_rows:
                raise HTTPException(status_code=400, detail='Администратору нужно назначить хотя бы одну точку.')
        normalized_location = None
    elif requested_role == RoleEnum.EMPLOYEE.value:
        if not normalized_location:
            raise HTTPException(status_code=400, detail='Сотруднику нужно назначить точку.')
        location_point = await db.scalar(select(LocationPoint).where(LocationPoint.name == normalized_location).limit(1))
        if not location_point:
            raise HTTPException(status_code=400, detail='Выбрана несуществующая точка.')
    else:
        normalized_location = None

    old_name = user.full_name
    user.full_name = payload.full_name.strip()
    user.birth_date = payload.birth_date
    user.username = payload.username.strip()
    user.role = requested_role
    user.location = normalized_location
    user.is_active = payload.is_active
    if payload.password:
        user.password_hash = hash_password(payload.password)

    if old_name != user.full_name:
        await db.execute(update(CategoryAssignment).where(CategoryAssignment.user_id == user.id).values(user_full_name_snapshot=user.full_name))
        await db.execute(update(CheckResult).where(CheckResult.checked_by_user_id == user.id).values(checked_by_name_snapshot=user.full_name))

    if requested_role == RoleEnum.ADMIN.value:
        if current_user.role == RoleEnum.SUPERADMIN.value:
            if admin_location_rows:
                await _assign_admin_location_access_by_ids(user.id, [row.id for row in admin_location_rows], db, granted_by_user_id=current_user.id)
            elif user.id != current_user.id:
                await _assign_admin_location_access_by_ids(user.id, [], db, granted_by_user_id=current_user.id)
        user.location = None
    else:
        await db.execute(delete(AdminLocationAccess).where(AdminLocationAccess.admin_user_id == user.id))

    if old_superadmin and requested_role != RoleEnum.SUPERADMIN.value:
        superadmin_count = await db.scalar(select(func.count()).select_from(User).where(User.role == RoleEnum.SUPERADMIN.value))
        if (superadmin_count or 0) <= 1:
            raise HTTPException(status_code=400, detail='Нельзя снять роль у последнего главного администратора.')

    await db.commit()
    await db.refresh(user)
    return UserActionResponse(success=True, message='Пользователь обновлён.', user=await _build_user_response(user, db))


async def delete_user(user_id: int, db: AsyncSession, current_user: User) -> DeleteResponse:
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail='Пользователь не найден.')

    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail='Нельзя удалить собственный аккаунт.')

    if current_user.role == RoleEnum.ADMIN.value:
        accessible_locations = set(await get_user_accessible_locations(current_user, db))
        if user.role != RoleEnum.EMPLOYEE.value:
            raise HTTPException(status_code=403, detail='Обычный администратор может удалять только сотрудников.')
        if not user.location or _normalize_location(user.location) not in accessible_locations:
            raise HTTPException(status_code=403, detail='Нет доступа к пользователю из другой точки.')

    if user.role == RoleEnum.SUPERADMIN.value:
        superadmin_count = await db.scalar(select(func.count()).select_from(User).where(User.role == RoleEnum.SUPERADMIN.value))
        if (superadmin_count or 0) <= 1:
            raise HTTPException(status_code=400, detail='Нельзя удалить последнего главного администратора.')

    await db.execute(delete(CategoryAssignment).where(CategoryAssignment.user_id == user.id))
    await db.execute(update(CheckResult).where(CheckResult.checked_by_user_id == user.id).values(checked_by_user_id=None))
    await db.execute(delete(AdminLocationAccess).where(AdminLocationAccess.admin_user_id == user.id))
    await db.delete(user)
    await db.commit()
    return DeleteResponse(success=True, message='Пользователь удалён.')


async def _get_inventory_for(location: str) -> dict[str, Any]:
    normalized = _normalize_location(location)
    started = monotonic()
    if ms_client.enabled:
        logger.info('Загрузка inventory началась. location=%s source=moysklad', normalized)
        try:
            inventory = await ms_client.get_inventory(normalized)
            stripped = _get_cached_stripped_inventory(normalized, inventory)
            duration_ms = round((monotonic() - started) * 1000, 1)
            logger.info(
                'Загрузка inventory завершена. location=%s source=moysklad categories=%s duration_ms=%s',
                normalized,
                len(stripped.get('categories', [])),
                duration_ms,
            )
            return stripped
        except ValueError as exc:
            duration_ms = round((monotonic() - started) * 1000, 1)
            logger.warning('Inventory не найден. location=%s duration_ms=%s detail=%s', normalized, duration_ms, exc)
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except httpx.HTTPError as exc:
            duration_ms = round((monotonic() - started) * 1000, 1)
            logger.exception('Ошибка загрузки inventory из МойСклад. location=%s duration_ms=%s', normalized, duration_ms)
            raise HTTPException(status_code=502, detail='Не удалось получить данные из МойСклад. Попробуйте ещё раз.') from exc
        except Exception:
            duration_ms = round((monotonic() - started) * 1000, 1)
            logger.exception('Непредвиденная ошибка загрузки inventory. location=%s duration_ms=%s', normalized, duration_ms)
            raise

    if normalized not in MOCK_INVENTORY:
        logger.warning('Inventory не найден в mock-данных. location=%s', normalized)
        raise HTTPException(status_code=404, detail='Неизвестная точка.')

    stripped = _get_cached_stripped_inventory(normalized, MOCK_INVENTORY[normalized])
    duration_ms = round((monotonic() - started) * 1000, 1)
    logger.info(
        'Загрузка inventory завершена. location=%s source=mock categories=%s duration_ms=%s',
        normalized,
        len(stripped.get('categories', [])),
        duration_ms,
    )
    return stripped


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
    target = _get_target_lookup(location, inventory).get(target_id)
    if target is not None:
        return target
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



async def _load_report_target_snapshots(report_id: int, db: AsyncSession) -> list[ReportTargetSnapshot]:
    return (
        await db.scalars(
            select(ReportTargetSnapshot)
            .where(ReportTargetSnapshot.report_id == report_id)
            .order_by(ReportTargetSnapshot.id.asc())
        )
    ).all()


async def _upsert_report_target_snapshot(
    *,
    report_id: int,
    category_id: str,
    category_name: str,
    subcategory_id: str | None,
    subcategory_name: str | None,
    target_type: str,
    target_id: str,
    target_name: str,
    assigned_user_id_snapshot: int | None,
    assigned_user_name_snapshot: str | None,
    db: AsyncSession,
) -> None:
    existing = await db.scalar(
        select(ReportTargetSnapshot)
        .where(ReportTargetSnapshot.report_id == report_id)
        .where(ReportTargetSnapshot.target_type == target_type)
        .where(ReportTargetSnapshot.target_id == target_id)
        .where(ReportTargetSnapshot.assigned_user_id_snapshot == assigned_user_id_snapshot)
        .limit(1)
    )
    if existing:
        existing.category_id = category_id
        existing.category_name = category_name
        existing.subcategory_id = subcategory_id
        existing.subcategory_name = subcategory_name
        existing.target_name = target_name
        existing.assigned_user_name_snapshot = assigned_user_name_snapshot
        return

    db.add(ReportTargetSnapshot(
        report_id=report_id,
        category_id=category_id,
        category_name=category_name,
        subcategory_id=subcategory_id,
        subcategory_name=subcategory_name,
        target_type=target_type,
        target_id=target_id,
        target_name=target_name,
        assigned_user_id_snapshot=assigned_user_id_snapshot,
        assigned_user_name_snapshot=assigned_user_name_snapshot,
    ))


async def _bootstrap_report_target_snapshots(
    report: Report,
    assignments: list[CategoryAssignment],
    results: list[CheckResult],
    db: AsyncSession,
) -> list[ReportTargetSnapshot]:
    existing = await _load_report_target_snapshots(report.id, db)
    existing_keys = {
        (row.target_type, row.target_id, row.assigned_user_id_snapshot)
        for row in existing
    }
    created = False

    for assignment in assignments:
        key = (assignment.target_type, assignment.target_id, assignment.user_id)
        if key in existing_keys:
            continue
        db.add(ReportTargetSnapshot(
            report_id=report.id,
            category_id=assignment.category_id,
            category_name=assignment.category_name,
            subcategory_id=assignment.subcategory_id,
            subcategory_name=assignment.subcategory_name,
            target_type=assignment.target_type,
            target_id=assignment.target_id,
            target_name=assignment.target_name,
            assigned_user_id_snapshot=assignment.user_id,
            assigned_user_name_snapshot=assignment.user_full_name_snapshot,
        ))
        existing_keys.add(key)
        created = True

    for row in results:
        if row.checked_by_user_id is None and not row.checked_by_name_snapshot:
            continue
        key = (row.target_type, row.target_id, row.checked_by_user_id)
        if key in existing_keys:
            continue
        db.add(ReportTargetSnapshot(
            report_id=report.id,
            category_id=row.category_id,
            category_name=row.category_name,
            subcategory_id=row.subcategory_id,
            subcategory_name=row.subcategory_name,
            target_type=row.target_type,
            target_id=row.target_id,
            target_name=row.target_name,
            assigned_user_id_snapshot=row.checked_by_user_id,
            assigned_user_name_snapshot=row.checked_by_name_snapshot,
        ))
        existing_keys.add(key)
        created = True

    if created:
        await db.commit()
        return await _load_report_target_snapshots(report.id, db)
    return existing


def _build_retained_scope_for_user(
    user_id: int,
    snapshots: list[ReportTargetSnapshot],
    results: list[CheckResult],
) -> tuple[set[str], dict[str, set[str]], dict[str, dict[str, set[str]]]]:
    retained_category_ids: set[str] = set()
    retained_subcategory_ids: dict[str, set[str]] = defaultdict(set)
    retained_item_ids: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))

    for row in snapshots:
        if row.assigned_user_id_snapshot != user_id:
            continue
        if row.target_type == 'category':
            retained_category_ids.add(row.category_id)
        elif row.target_type == 'subcategory' and row.subcategory_id:
            retained_subcategory_ids[row.category_id].add(row.subcategory_id)
        elif row.target_type == 'item' and row.subcategory_id:
            retained_item_ids[row.category_id][row.subcategory_id].add(row.target_id)

    for row in results:
        if row.checked_by_user_id != user_id:
            continue
        if row.target_type == 'category':
            retained_category_ids.add(row.category_id)
        elif row.target_type == 'subcategory' and row.subcategory_id:
            retained_subcategory_ids[row.category_id].add(row.subcategory_id)
        elif row.target_type == 'item' and row.subcategory_id:
            retained_item_ids[row.category_id][row.subcategory_id].add(row.target_id)

    return retained_category_ids, retained_subcategory_ids, retained_item_ids


def _report_snapshot_maps(
    snapshots: list[ReportTargetSnapshot],
) -> tuple[
    dict[str, set[int]],
    dict[str, dict[str, set[int]]],
    dict[str, dict[str, dict[str, set[int]]]],
    dict[str, str],
    dict[str, dict[str, str]],
    dict[str, dict[str, dict[str, str]]],
]:
    category_user_ids: dict[str, set[int]] = defaultdict(set)
    subcategory_user_ids: dict[str, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
    item_user_ids: dict[str, dict[str, dict[str, set[int]]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(set)))
    category_owner_names: dict[str, str] = {}
    subcategory_owner_names: dict[str, dict[str, str]] = defaultdict(dict)
    item_owner_names: dict[str, dict[str, dict[str, str]]] = defaultdict(lambda: defaultdict(dict))

    for row in snapshots:
        if row.target_type == 'category':
            if row.assigned_user_id_snapshot is not None:
                category_user_ids[row.category_id].add(int(row.assigned_user_id_snapshot))
            if row.assigned_user_name_snapshot and row.category_id not in category_owner_names:
                category_owner_names[row.category_id] = row.assigned_user_name_snapshot
        elif row.target_type == 'subcategory' and row.subcategory_id:
            if row.assigned_user_id_snapshot is not None:
                subcategory_user_ids[row.category_id][row.subcategory_id].add(int(row.assigned_user_id_snapshot))
            if row.assigned_user_name_snapshot and row.subcategory_id not in subcategory_owner_names[row.category_id]:
                subcategory_owner_names[row.category_id][row.subcategory_id] = row.assigned_user_name_snapshot
        elif row.target_type == 'item' and row.subcategory_id:
            if row.assigned_user_id_snapshot is not None:
                item_user_ids[row.category_id][row.subcategory_id][row.target_id].add(int(row.assigned_user_id_snapshot))
            if row.assigned_user_name_snapshot and row.target_id not in item_owner_names[row.category_id][row.subcategory_id]:
                item_owner_names[row.category_id][row.subcategory_id][row.target_id] = row.assigned_user_name_snapshot

    return (
        category_user_ids,
        subcategory_user_ids,
        item_user_ids,
        category_owner_names,
        subcategory_owner_names,
        item_owner_names,
    )

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
    relevant_subcategories = list(raw_category['subcategories'])
    return bool(relevant_subcategories) and all(_subcategory_is_complete(sub, results_by_target)[0] for sub in relevant_subcategories)


async def _find_available_report_date(location: str, preferred_date: date, db: AsyncSession) -> date:
    used_dates = set(
        await db.scalars(
            select(Report.report_date).where(Report.location == location)
        )
    )
    candidate = preferred_date
    while candidate in used_dates:
        candidate -= timedelta(days=1)
    return candidate


async def _ensure_cycle_final_report(location: str, cycle_version: int, cycle_started_at: date, db: AsyncSession) -> Report | None:
    normalized = _normalize_location(location)
    existing = await db.scalar(
        select(Report).where(
            Report.location == normalized,
            Report.cycle_version == cycle_version,
            Report.report_type == FINAL_REPORT_TYPE,
        ).limit(1)
    )
    if existing:
        if existing.status != 'completed':
            existing.status = 'completed'
            await db.commit()
            await db.refresh(existing)
        return existing

    daily_reports = (
        await db.scalars(
            select(Report).where(
                Report.location == normalized,
                Report.cycle_version == cycle_version,
                Report.report_type == DAILY_REPORT_TYPE,
            )
        )
    ).all()
    if not daily_reports:
        return None

    report_date = await _find_available_report_date(normalized, cycle_started_at - timedelta(days=1), db)
    final_report = Report(
        location=normalized,
        report_date=report_date,
        cycle_version=cycle_version,
        report_type=FINAL_REPORT_TYPE,
        status='completed',
    )
    db.add(final_report)
    await db.commit()
    await db.refresh(final_report)
    return final_report


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
        old_started_at = cycle.started_at
        await _ensure_cycle_final_report(normalized, old_version, old_started_at, db)
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
    old_started_at = cycle.started_at
    await _ensure_cycle_final_report(_normalize_location(location), old_version, old_started_at, db)
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
    if (report.report_type or DAILY_REPORT_TYPE) == FINAL_REPORT_TYPE:
        report.status = 'completed'
        return

    participant_user_ids = await _get_report_participant_user_ids(report.id, db)
    completion_count = await db.scalar(
        select(func.count()).select_from(ReportEmployeeCompletion).where(ReportEmployeeCompletion.report_id == report.id)
    )
    required_finish_count = len(participant_user_ids)
    newer_report_exists = await db.scalar(
        select(func.count()).select_from(Report).where(
            Report.location == report.location,
            Report.report_date > report.report_date,
        )
    )

    if required_finish_count > 0 and (completion_count or 0) >= required_finish_count:
        report.status = 'completed'
    elif (newer_report_exists or 0) > 0:
        report.status = 'completed'
    elif not participant_user_ids:
        report.status = 'created'
    else:
        report.status = 'in_progress'


async def _complete_previous_reports(location: str, current_report_date: date, db: AsyncSession) -> None:
    previous_reports = (
        await db.scalars(
            select(Report).where(
                Report.location == location,
                Report.report_date < current_report_date,
                Report.report_type == DAILY_REPORT_TYPE,
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
            Report.report_type == DAILY_REPORT_TYPE,
        ).limit(1)
    )
    if report:
        await _sync_report_status(report, db)
        await db.commit()
        return report

    report = Report(location=normalized, report_date=today, cycle_version=cycle_version, report_type=DAILY_REPORT_TYPE, status='created')
    db.add(report)
    await db.execute(
        delete(CategoryAssignment).where(
            CategoryAssignment.location == normalized,
            CategoryAssignment.cycle_version == cycle_version,
        )
    )
    await _complete_previous_reports(normalized, today, db)
    await db.commit()
    await db.refresh(report)
    return report


async def _load_assignments(location: str, cycle_version: int, db: AsyncSession) -> list[CategoryAssignment]:
    return (await db.scalars(select(CategoryAssignment).where(CategoryAssignment.location == location, CategoryAssignment.cycle_version == cycle_version))).all()


async def _load_results(report_id: int, db: AsyncSession) -> list[CheckResult]:
    return (await db.scalars(select(CheckResult).where(CheckResult.report_id == report_id).order_by(CheckResult.id.asc()))).all()


async def _load_results_for_report_ids(report_ids: list[int], db: AsyncSession) -> list[CheckResult]:
    if not report_ids:
        return []
    return (
        await db.scalars(
            select(CheckResult)
            .where(CheckResult.report_id.in_(report_ids))
            .order_by(CheckResult.created_at.asc(), CheckResult.id.asc())
        )
    ).all()


async def _load_report_target_snapshots_for_report_ids(report_ids: list[int], db: AsyncSession) -> list[ReportTargetSnapshot]:
    if not report_ids:
        return []
    return (
        await db.scalars(
            select(ReportTargetSnapshot)
            .where(ReportTargetSnapshot.report_id.in_(report_ids))
            .order_by(ReportTargetSnapshot.created_at.asc(), ReportTargetSnapshot.id.asc())
        )
    ).all()


async def _load_selection_targets(location: str, cycle_version: int, db: AsyncSession) -> list[SelectionTarget]:
    return (await db.scalars(select(SelectionTarget).where(SelectionTarget.location == location, SelectionTarget.cycle_version == cycle_version))).all()


async def _load_completed_subcategory_ids_for_cycle(
    location: str,
    cycle_version: int,
    inventory: dict[str, Any],
    db: AsyncSession,
    before_report_date: date | None = None,
) -> dict[str, set[str]]:
    report_query = select(Report).where(
        Report.location == location,
        Report.cycle_version == cycle_version,
    )
    if before_report_date is not None:
        report_query = report_query.where(Report.report_date < before_report_date)

    reports = (await db.scalars(report_query.order_by(Report.report_date.asc(), Report.id.asc()))).all()
    if not reports:
        return {}

    report_ids = [report.id for report in reports]
    results = (
        await db.scalars(
            select(CheckResult)
            .where(CheckResult.report_id.in_(report_ids))
            .order_by(CheckResult.report_id.asc(), CheckResult.id.asc())
        )
    ).all()
    if not results:
        return {}

    category_by_id = {category['id']: category for category in inventory.get('categories', [])}
    results_by_report: dict[int, dict[str, dict[str, CheckResult]]] = defaultdict(lambda: defaultdict(dict))
    for row in results:
        results_by_report[row.report_id][row.category_id][row.target_id] = row

    completed_subcategory_ids: dict[str, set[str]] = defaultdict(set)
    for report in reports:
        rows_by_category = results_by_report.get(report.id, {})
        for category_id, result_map in rows_by_category.items():
            raw_category = category_by_id.get(category_id)
            if not raw_category or raw_category.get('name') == DEFAULT_CATEGORY_NAME:
                continue
            for raw_sub in raw_category.get('subcategories', []):
                if _is_categoryless_subcategory(raw_category, raw_sub):
                    continue
                is_completed, _ = _subcategory_is_complete(raw_sub, result_map)
                if is_completed:
                    completed_subcategory_ids[category_id].add(raw_sub['id'])

    return {category_id: set(sub_ids) for category_id, sub_ids in completed_subcategory_ids.items()}


def _selection_target_maps(targets: list[SelectionTarget]) -> tuple[set[str], dict[str, set[str]]]:
    category_ids: set[str] = set()
    subcategory_ids: dict[str, set[str]] = defaultdict(set)
    for row in targets:
        if row.target_type == 'category':
            category_ids.add(row.category_id)
        elif row.target_type == 'subcategory' and row.subcategory_id:
            subcategory_ids[row.category_id].add(row.subcategory_id)
    return category_ids, subcategory_ids


def _report_history_target_maps(
    report_snapshots: list[ReportTargetSnapshot],
    results: list[CheckResult],
) -> tuple[set[str], dict[str, set[str]], dict[str, dict[str, set[str]]]]:
    category_ids: set[str] = set()
    subcategory_ids: dict[str, set[str]] = defaultdict(set)
    item_ids: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))

    def absorb(target_type: str, category_id: str, subcategory_id: str | None, target_id: str) -> None:
        if target_type == 'category':
            category_ids.add(category_id)
        elif target_type == 'subcategory' and subcategory_id:
            subcategory_ids[category_id].add(subcategory_id)
        elif target_type == 'item' and subcategory_id:
            item_ids[category_id][subcategory_id].add(target_id)

    for row in report_snapshots:
        absorb(row.target_type, row.category_id, row.subcategory_id, row.target_id)

    for row in results:
        absorb(row.target_type, row.category_id, row.subcategory_id, row.target_id)

    return category_ids, subcategory_ids, item_ids


def _is_categoryless_subcategory(category: dict[str, Any], subcategory: dict[str, Any]) -> bool:
    return category['name'] != DEFAULT_CATEGORY_NAME and subcategory['name'] == DEFAULT_SUBCATEGORY_NAME


def _filter_inventory_by_targets(
    inventory: dict[str, Any],
    selected_category_ids: set[str],
    selected_subcategory_ids: dict[str, set[str]],
    retained_category_ids: set[str] | None = None,
    retained_subcategory_ids: dict[str, set[str]] | None = None,
    retained_item_ids: dict[str, dict[str, set[str]]] | None = None,
    excluded_subcategory_ids: dict[str, set[str]] | None = None,
) -> dict[str, Any]:
    retained_category_ids = retained_category_ids or set()
    retained_subcategory_ids = retained_subcategory_ids or {}
    retained_item_ids = retained_item_ids or {}
    excluded_subcategory_ids = excluded_subcategory_ids or {}

    has_retained_scope = bool(retained_category_ids) or any(retained_subcategory_ids.values()) or any(
        retained_item_ids.get(category_id, {}) for category_id in retained_item_ids
    )
    if not selected_category_ids and not any(selected_subcategory_ids.values()) and not has_retained_scope:
        return inventory

    categories: list[dict[str, Any]] = []
    for category in inventory['categories']:
        include_full_category = category['id'] in selected_category_ids or category['id'] in retained_category_ids
        allowed_sub_ids = set(selected_subcategory_ids.get(category['id'], set())) | set(retained_subcategory_ids.get(category['id'], set()))
        retained_items_by_sub = retained_item_ids.get(category['id'], {})

        filtered_subcategories: list[dict[str, Any]] = []
        for sub in category['subcategories']:
            retained_sub_item_ids = set(retained_items_by_sub.get(sub['id'], set()))
            keep_subcategory = False
            keep_all_items = False
            excluded_for_cycle = sub['id'] in excluded_subcategory_ids.get(category['id'], set())
            preserve_excluded_subcategory = sub['id'] in retained_subcategory_ids.get(category['id'], set()) or bool(retained_sub_item_ids)

            if excluded_for_cycle and not preserve_excluded_subcategory:
                continue

            if include_full_category or sub['id'] in allowed_sub_ids:
                keep_subcategory = True
                keep_all_items = True
            elif retained_sub_item_ids:
                keep_subcategory = True

            if not keep_subcategory:
                continue

            items = [dict(item) for item in sub['items']]
            if not keep_all_items:
                items = [item for item in items if item['id'] in retained_sub_item_ids]
                if not items:
                    continue

            filtered_subcategories.append({
                'id': sub['id'],
                'name': sub['name'],
                'items': items,
            })

        if filtered_subcategories:
            categories.append({
                'id': category['id'],
                'name': category['name'],
                'subcategories': filtered_subcategories,
            })

    return {'location': inventory.get('location'), 'categories': categories}


async def list_locations(db: AsyncSession, current_user: User) -> LocationListResponse:
    await _ensure_default_location_points(db)
    if current_user.role == RoleEnum.SUPERADMIN.value:
        rows = (await db.scalars(select(LocationPoint).order_by(LocationPoint.name.asc()))).all()
    elif current_user.role == RoleEnum.ADMIN.value:
        rows = (
            await db.scalars(
                select(LocationPoint)
                .join(AdminLocationAccess, AdminLocationAccess.location_point_id == LocationPoint.id)
                .where(AdminLocationAccess.admin_user_id == current_user.id)
                .order_by(LocationPoint.name.asc())
            )
        ).all()
    else:
        rows = []
    locations = [LocationPointModel.model_validate(row) for row in rows]
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
    ms_client.invalidate_inventory(point.name)
    _invalidate_runtime_inventory_cache(point.name)
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
    _invalidate_runtime_inventory_cache(old_name)
    _invalidate_runtime_inventory_cache(point.name)
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

    access_count = await db.scalar(select(func.count()).select_from(AdminLocationAccess).where(AdminLocationAccess.location_point_id == point.id))
    if (access_count or 0) > 0:
        linked_entities.append('доступы администраторов')

    if linked_entities:
        raise HTTPException(
            status_code=400,
            detail='Нельзя удалить точку, пока с ней связаны: ' + ', '.join(linked_entities) + '.',
        )

    old_name = point.name
    await db.delete(point)
    await db.commit()
    ms_client.invalidate_inventory(old_name)
    _invalidate_runtime_inventory_cache(old_name)
    return DeleteResponse(success=True, message='Точка удалена.')


async def get_cycle_targets(location: str, db: AsyncSession) -> AdminCycleTargetsResponse:
    normalized = _normalize_location(location)
    cycle = await _get_or_create_selection_cycle(normalized, db)
    inventory = await _get_inventory_for(normalized)
    targets = await _load_selection_targets(normalized, cycle.cycle_version, db)
    selected_category_ids, selected_subcategory_ids = _selection_target_maps(targets)
    completed_subcategory_ids = await _load_completed_subcategory_ids_for_cycle(
        normalized,
        cycle.cycle_version,
        inventory,
        db,
    )

    categories: list[AdminCycleTargetCategory] = []
    for category in inventory['categories']:
        if category['name'] == DEFAULT_CATEGORY_NAME:
            continue
        subcategories: list[AdminCycleTargetItem] = []
        completed_subcategories: list[AdminCycleTargetItem] = []
        completed_ids_for_category = completed_subcategory_ids.get(category['id'], set())
        for sub in category['subcategories']:
            if _is_categoryless_subcategory(category, sub):
                continue
            item = AdminCycleTargetItem(
                id=sub['id'],
                name=sub['name'],
                selected=sub['id'] in selected_subcategory_ids.get(category['id'], set()),
                disabled=category['id'] in selected_category_ids,
            )
            if sub['id'] in completed_ids_for_category:
                completed_subcategories.append(item)
            else:
                subcategories.append(item)
        categories.append(AdminCycleTargetCategory(
            id=category['id'],
            name=category['name'],
            selected=category['id'] in selected_category_ids,
            subcategories=subcategories,
            completed_subcategories=completed_subcategories,
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

    requested_category_ids = sorted(set(payload.category_ids))
    requested_subcategory_ids = sorted(set(payload.subcategory_ids))
    existing_targets = await _load_selection_targets(normalized, cycle.cycle_version, db)

    if payload.cycle_started_at:
        cycle.started_at = payload.cycle_started_at
        cycle.updated_at = datetime.utcnow()

    if not requested_category_ids and not requested_subcategory_ids:
        await db.commit()
        if existing_targets:
            return SaveCycleTargetsResponse(
                success=True,
                message='Пустой выбор не сохранён. Оставлен предыдущий выбор цикла.',
                cycle_started_at=cycle.started_at.strftime('%d.%m.%Y'),
            )
        return SaveCycleTargetsResponse(
            success=True,
            message='Нечего сохранять: категории и подкатегории не выбраны.',
            cycle_started_at=cycle.started_at.strftime('%d.%m.%Y'),
        )

    inventory = await _get_inventory_for(normalized)

    completed_subcategory_ids = await _load_completed_subcategory_ids_for_cycle(
        normalized,
        cycle.cycle_version,
        inventory,
        db,
    )

    category_by_id = {row['id']: row for row in inventory['categories']}
    sub_by_id: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for category in inventory['categories']:
        for sub in category['subcategories']:
            sub_by_id[sub['id']] = (category, sub)

    await db.execute(delete(SelectionTarget).where(SelectionTarget.location == normalized, SelectionTarget.cycle_version == cycle.cycle_version))

    for category_id in requested_category_ids:
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

    selected_categories = set(requested_category_ids)
    skipped_completed_subcategories = 0
    for subcategory_id in requested_subcategory_ids:
        pair = sub_by_id.get(subcategory_id)
        if not pair:
            continue
        category, sub = pair
        if category['id'] in selected_categories or category['name'] == DEFAULT_CATEGORY_NAME or _is_categoryless_subcategory(category, sub):
            continue
        if sub['id'] in completed_subcategory_ids.get(category['id'], set()):
            skipped_completed_subcategories += 1
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
    _invalidate_runtime_inventory_cache(normalized)
    message = 'Изменения сохранены.'
    if skipped_completed_subcategories:
        message = f'{message} Уже пройденных подкатегорий, пропущенных при сохранении: {skipped_completed_subcategories}.'
    return SaveCycleTargetsResponse(success=True, message=message, cycle_started_at=cycle.started_at.strftime('%d.%m.%Y'))


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


async def _load_report_employee_completions(report_id: int, db: AsyncSession) -> list[ReportEmployeeCompletion]:
    return (
        await db.scalars(
            select(ReportEmployeeCompletion)
            .where(ReportEmployeeCompletion.report_id == report_id)
            .order_by(ReportEmployeeCompletion.finished_at.asc(), ReportEmployeeCompletion.id.asc())
        )
    ).all()


async def _load_report_employee_starts(report_id: int, db: AsyncSession) -> list[ReportEmployeeStart]:
    return (
        await db.scalars(
            select(ReportEmployeeStart)
            .where(ReportEmployeeStart.report_id == report_id)
            .order_by(ReportEmployeeStart.started_at.asc(), ReportEmployeeStart.id.asc())
        )
    ).all()


async def _is_employee_started_report(report_id: int, user_id: int, db: AsyncSession) -> bool:
    started = await db.scalar(
        select(ReportEmployeeStart)
        .where(ReportEmployeeStart.report_id == report_id)
        .where(ReportEmployeeStart.user_id == user_id)
        .limit(1)
    )
    return started is not None


async def _get_report_participant_user_ids(report_id: int, db: AsyncSession) -> set[int]:
    started_ids = {
        int(value)
        for value in (
            await db.scalars(
                select(ReportEmployeeStart.user_id).where(ReportEmployeeStart.report_id == report_id)
            )
        ).all()
        if value is not None
    }
    completion_ids = {
        int(value)
        for value in (
            await db.scalars(
                select(ReportEmployeeCompletion.user_id).where(ReportEmployeeCompletion.report_id == report_id)
            )
        ).all()
        if value is not None
    }
    result_user_ids = {
        int(value)
        for value in (
            await db.scalars(
                select(CheckResult.checked_by_user_id)
                .where(CheckResult.report_id == report_id)
                .where(CheckResult.checked_by_user_id.is_not(None))
            )
        ).all()
        if value is not None
    }
    return started_ids | completion_ids | result_user_ids


def _format_completion_datetime(value: datetime | None) -> str | None:
    if not value:
        return None
    return _format_moscow_datetime(value)


async def _active_employee_users_for_location(location: str, db: AsyncSession) -> list[User]:
    return (
        await db.scalars(
            select(User)
            .where(User.role == RoleEnum.EMPLOYEE.value)
            .where(User.is_active.is_(True))
            .where(User.location == location)
            .order_by(User.full_name.asc(), User.id.asc())
        )
    ).all()


async def _is_employee_finished_report(report_id: int, user_id: int, db: AsyncSession) -> bool:
    completion = await db.scalar(
        select(ReportEmployeeCompletion)
        .where(ReportEmployeeCompletion.report_id == report_id)
        .where(ReportEmployeeCompletion.user_id == user_id)
        .limit(1)
    )
    return completion is not None


async def _refresh_report_status(report: Report, db: AsyncSession) -> None:
    await _sync_report_status(report, db)
    await db.commit()


async def get_inventory_data(location: str, db: AsyncSession, user: User) -> InventoryStructureResponse:
    normalized = _normalize_location(location)
    cycle = await _get_or_create_selection_cycle(normalized, db)
    report = await get_or_create_daily_report(normalized, cycle.cycle_version, db)
    assignments = await _load_assignments(normalized, cycle.cycle_version, db)
    results = [
        row for row in await _load_results(report.id, db)
        if row.category_name != DEFAULT_CATEGORY_NAME and (row.subcategory_name is None or row.subcategory_name != DEFAULT_SUBCATEGORY_NAME)
    ]
    targets = await _load_selection_targets(normalized, cycle.cycle_version, db)
    report_snapshots = await _bootstrap_report_target_snapshots(report, assignments, results, db)
    employee_started = await _is_employee_started_report(report.id, user.id, db)
    employee_finished = await _is_employee_finished_report(report.id, user.id, db)
    await _sync_report_status(report, db)
    report_started = report.status != 'created'
    report_completed = report.status == 'completed'

    category_assignments, subcategory_assignments, item_assignments = _category_assignments_map(assignments)
    selected_category_ids, selected_subcategory_ids = _selection_target_maps(targets)
    retained_category_ids, retained_subcategory_ids, retained_item_ids = _build_retained_scope_for_user(user.id, report_snapshots, results)
    (
        snapshot_category_user_ids,
        snapshot_subcategory_user_ids,
        snapshot_item_user_ids,
        snapshot_category_owner_names,
        snapshot_subcategory_owner_names,
        snapshot_item_owner_names,
    ) = _report_snapshot_maps(report_snapshots)

    rows_by_category_target: dict[str, dict[str, CheckResult]] = defaultdict(dict)
    for row in results:
        rows_by_category_target[row.category_id][row.target_id] = row

    categories: list[CategoryModel] = []
    inventory = await _get_inventory_for(normalized)
    completed_subcategory_ids = await _load_completed_subcategory_ids_for_cycle(
        normalized,
        cycle.cycle_version,
        inventory,
        db,
        before_report_date=report.report_date,
    )
    inventory = _filter_inventory_by_targets(
        inventory,
        selected_category_ids,
        selected_subcategory_ids,
        retained_category_ids,
        retained_subcategory_ids,
        retained_item_ids,
        excluded_subcategory_ids=completed_subcategory_ids,
    )

    for raw_category in inventory['categories']:
        category_is_diagnostic = raw_category['name'] == DEFAULT_CATEGORY_NAME
        category_assignment = category_assignments.get(raw_category['id'])
        sub_assignments = subcategory_assignments.get(raw_category['id'], {})
        item_assignments_by_sub = item_assignments.get(raw_category['id'], {})

        snapshot_category_taken_by_user = user.id in snapshot_category_user_ids.get(raw_category['id'], set())
        assigned_to_current_user = bool((category_assignment and category_assignment.user_id == user.id) or snapshot_category_taken_by_user)
        assigned_to_other = bool(category_assignment and category_assignment.user_id != user.id)
        has_my_subcategories = any(a.user_id == user.id for a in sub_assignments.values()) or any(
            user.id in user_ids for user_ids in snapshot_subcategory_user_ids.get(raw_category['id'], {}).values()
        )
        has_other_subcategories = any(a.user_id != user.id for a in sub_assignments.values())
        has_my_items = any(a.user_id == user.id for sub_items in item_assignments_by_sub.values() for a in sub_items.values()) or any(
            user.id in user_ids
            for sub_items in snapshot_item_user_ids.get(raw_category['id'], {}).values()
            for user_ids in sub_items.values()
        )
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
        selected_whole_category = raw_category['id'] in selected_category_ids
        selected_subcategory_names = [
            sub['name']
            for sub in raw_category['subcategories']
            if sub['id'] in selected_subcategory_ids.get(raw_category['id'], set())
        ]
        can_take_category = (
            selected_whole_category
            and (not category_is_diagnostic)
            and (not has_diagnostic_subcategories)
            and category_assignment is None
            and not sub_assignments
            and not item_assignments_by_sub
        )

        owner_names = {a.user_full_name_snapshot for a in sub_assignments.values() if a.user_full_name_snapshot}
        owner_names.update(a.user_full_name_snapshot for sub_items in item_assignments_by_sub.values() for a in sub_items.values() if a.user_full_name_snapshot)
        assigned_to = None
        mixed_assignment = False
        if category_assignment:
            assigned_to = category_assignment.user_full_name_snapshot
        elif snapshot_category_taken_by_user:
            assigned_to = snapshot_category_owner_names.get(raw_category['id']) or user.full_name
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
            snapshot_sub_taken_by_user = user.id in snapshot_subcategory_user_ids.get(raw_category['id'], {}).get(raw_sub['id'], set())
            has_my_items_in_sub = any(a.user_id == user.id for a in sub_item_assignments.values()) or any(
                user.id in user_ids
                for user_ids in snapshot_item_user_ids.get(raw_category['id'], {}).get(raw_sub['id'], {}).values()
            )
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
                    assigned_to=(item_assignment.user_full_name_snapshot if item_assignment else snapshot_item_owner_names.get(raw_category['id'], {}).get(raw_sub['id'], {}).get(item['id'])),
                    assigned_to_current_user=bool((item_assignment and item_assignment.user_id == user.id) or user.id in snapshot_item_user_ids.get(raw_category['id'], {}).get(raw_sub['id'], {}).get(item['id'], set())),
                    can_take=diagnostic_sub and category_assignment is None and sub_assignments.get(raw_sub['id']) is None and item_assignment is None,
                    is_blocked_by_other=bool(item_assignment and item_assignment.user_id != user.id),
                    is_diagnostic=diagnostic_sub,
                ))

            sub_assignment = sub_assignments.get(raw_sub['id'])
            sub_assigned_to_current_user = bool((sub_assignment and sub_assignment.user_id == user.id) or snapshot_sub_taken_by_user)
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
            elif snapshot_sub_taken_by_user:
                sub_assigned_to = snapshot_subcategory_owner_names.get(raw_category['id'], {}).get(raw_sub['id']) or user.full_name
            elif snapshot_category_taken_by_user:
                sub_assigned_to = snapshot_category_owner_names.get(raw_category['id']) or user.full_name
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
                selected_whole_category=selected_whole_category,
                selected_subcategory_names=selected_subcategory_names,
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
        report_status=report.status,
        employee_started=employee_started,
        employee_finished=employee_finished,
        report_started=report_started,
        report_completed=report_completed,
    )


async def assign_selection_to_user(report_id: int, category_id: str, target_type: str, subcategory_id: str | None, item_id: str | None, db: AsyncSession, user: User) -> AssignSelectionResponse:
    if not user.location:
        raise HTTPException(status_code=403, detail='Сотруднику не назначена точка.')

    report = await db.get(Report, report_id)
    if not report or report.location != user.location:
        raise HTTPException(status_code=404, detail='Ревизия не найдена.')
    await _sync_report_status(report, db)
    if report.status == 'completed':
        raise HTTPException(status_code=409, detail='Ревизия по этой точке уже завершена на сегодня.')
    if not await _is_employee_started_report(report.id, user.id, db):
        raise HTTPException(status_code=409, detail='Сначала нажмите «Начать ревизию».')
    if await _is_employee_finished_report(report.id, user.id, db):
        raise HTTPException(status_code=409, detail='Вы уже завершили свою ревизию на сегодня. Продолжить работу нельзя.')

    cycle = await _get_or_create_selection_cycle(report.location, db)
    assignments = await _load_assignments(report.location, cycle.cycle_version, db)
    targets = await _load_selection_targets(report.location, cycle.cycle_version, db)
    inventory = await _get_inventory_for(report.location)
    completed_subcategory_ids = await _load_completed_subcategory_ids_for_cycle(
        report.location,
        cycle.cycle_version,
        inventory,
        db,
        before_report_date=report.report_date,
    )
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
            partial_sub_ids = selected_subcategory_ids.get(category_id, set())
            if partial_sub_ids:
                raise HTTPException(
                    status_code=400,
                    detail='Администратор выбрал в этой категории только отдельные подкатегории. Возьмите нужную подкатегорию ниже.',
                )
            raise HTTPException(status_code=400, detail='Эта категория не выбрана администратором для текущего цикла.')
        if any(sub['name'] == DEFAULT_SUBCATEGORY_NAME for sub in category['subcategories']):
            raise HTTPException(status_code=400, detail='Категории со служебными ветками «Без категории/Без подкатегории» нельзя брать целиком. Выберите обычную подкатегорию или конкретные товары.')
        remaining_subcategories = [
            sub for sub in category['subcategories']
            if sub['id'] not in completed_subcategory_ids.get(category_id, set()) and not _is_categoryless_subcategory(category, sub)
        ]
        if not remaining_subcategories:
            raise HTTPException(status_code=400, detail='В этой категории на текущий цикл не осталось новых подкатегорий.')
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
        await _upsert_report_target_snapshot(
            report_id=report.id,
            category_id=category_id,
            category_name=category['name'],
            subcategory_id=None,
            subcategory_name=None,
            target_type='category',
            target_id=category_id,
            target_name=category['name'],
            assigned_user_id_snapshot=user.id,
            assigned_user_name_snapshot=user.full_name,
            db=db,
        )
        await db.commit()
        _invalidate_runtime_inventory_cache(report.location)
        return AssignSelectionResponse(success=True, message='Категория закреплена за вами на сегодня.')

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
        if subcategory_id in completed_subcategory_ids.get(category_id, set()):
            raise HTTPException(status_code=400, detail='Эта подкатегория уже была пройдена в текущем 15-дневном цикле.')

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
        await _upsert_report_target_snapshot(
            report_id=report.id,
            category_id=category_id,
            category_name=category['name'],
            subcategory_id=subcategory_id,
            subcategory_name=subcategory['name'],
            target_type='subcategory',
            target_id=subcategory_id,
            target_name=subcategory['name'],
            assigned_user_id_snapshot=user.id,
            assigned_user_name_snapshot=user.full_name,
            db=db,
        )
        await db.commit()
        _invalidate_runtime_inventory_cache(report.location)
        return AssignSelectionResponse(success=True, message='Подкатегория закреплена за вами на сегодня.')

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
        await _upsert_report_target_snapshot(
            report_id=report.id,
            category_id=category_id,
            category_name=category['name'],
            subcategory_id=subcategory_id,
            subcategory_name=subcategory['name'],
            target_type='item',
            target_id=item_id,
            target_name=item['name'],
            assigned_user_id_snapshot=user.id,
            assigned_user_name_snapshot=user.full_name,
            db=db,
        )
        await db.commit()
        _invalidate_runtime_inventory_cache(report.location)
        return AssignSelectionResponse(success=True, message='Товар закреплён за вами на сегодня.')

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


async def _get_or_increment_attempt_count(
    *,
    report_id: int,
    target_type: str,
    target_id: str,
    checked_by_user_id: int,
    db: AsyncSession,
) -> int:
    progress = await db.scalar(
        select(VerifyAttemptProgress)
        .where(VerifyAttemptProgress.report_id == report_id)
        .where(VerifyAttemptProgress.target_type == target_type)
        .where(VerifyAttemptProgress.target_id == target_id)
        .where(VerifyAttemptProgress.checked_by_user_id == checked_by_user_id)
        .limit(1)
    )
    now = datetime.utcnow()
    if progress:
        progress.attempts_used += 1
        progress.updated_at = now
        return progress.attempts_used

    progress = VerifyAttemptProgress(
        report_id=report_id,
        target_type=target_type,
        target_id=target_id,
        checked_by_user_id=checked_by_user_id,
        attempts_used=1,
        created_at=now,
        updated_at=now,
    )
    db.add(progress)
    await db.flush()
    return progress.attempts_used


async def _clear_attempt_count(
    *,
    report_id: int,
    target_type: str,
    target_id: str,
    checked_by_user_id: int,
    db: AsyncSession,
) -> None:
    await db.execute(
        delete(VerifyAttemptProgress)
        .where(VerifyAttemptProgress.report_id == report_id)
        .where(VerifyAttemptProgress.target_type == target_type)
        .where(VerifyAttemptProgress.target_id == target_id)
        .where(VerifyAttemptProgress.checked_by_user_id == checked_by_user_id)
    )


async def verify_item_or_category(data: VerifyRequest, db: AsyncSession, checked_by_user: User) -> VerifyResponse:
    report = await db.get(Report, data.report_id)
    if not report:
        raise HTTPException(status_code=404, detail='Ревизия не найдена.')
    if checked_by_user.location != report.location:
        raise HTTPException(status_code=403, detail='Ревизия относится к другой точке.')
    await _sync_report_status(report, db)
    if report.status == 'completed':
        raise HTTPException(status_code=409, detail='Ревизия по этой точке уже завершена на сегодня.')
    if not await _is_employee_started_report(report.id, checked_by_user.id, db):
        raise HTTPException(status_code=409, detail='Сначала нажмите «Начать ревизию».')
    if await _is_employee_finished_report(report.id, checked_by_user.id, db):
        raise HTTPException(status_code=409, detail='Вы уже завершили свою ревизию на сегодня. Продолжить работу нельзя.')

    category_id, category_name, subcategory_id, subcategory_name, target_type, target_name, expected_qty = await _find_target(report.location, data.target_id)
    assignments = await _load_assignments(report.location, report.cycle_version, db)
    if not _user_can_verify_target(checked_by_user, report, category_id, subcategory_id, data.target_id, target_type, assignments):
        raise HTTPException(status_code=403, detail='Эта категория, подкатегория или товар не закреплены за вами.')

    attempt_number = await _get_or_increment_attempt_count(
        report_id=report.id,
        target_type=target_type,
        target_id=data.target_id,
        checked_by_user_id=checked_by_user.id,
        db=db,
    )

    is_correct = abs(data.quantity - expected_qty) < 1e-9
    if is_correct:
        await _upsert_report_target_snapshot(
            report_id=report.id,
            category_id=category_id,
            category_name=category_name,
            subcategory_id=subcategory_id,
            subcategory_name=subcategory_name,
            target_type=target_type,
            target_id=data.target_id,
            target_name=target_name,
            assigned_user_id_snapshot=checked_by_user.id,
            assigned_user_name_snapshot=checked_by_user.full_name,
            db=db,
        )
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
            attempts_used=attempt_number,
            checked_by_user_id=checked_by_user.id,
            checked_by_name_snapshot=checked_by_user.full_name,
            db=db,
        )
        await _clear_attempt_count(
            report_id=report.id,
            target_type=target_type,
            target_id=data.target_id,
            checked_by_user_id=checked_by_user.id,
            db=db,
        )
        await db.commit()
        _invalidate_runtime_inventory_cache(report.location)
        await _refresh_report_status(report, db)
        return VerifyResponse(is_correct=True, attempts_left=0, message='Верно!', expand_category=False)

    attempts_left = max(0, 3 - attempt_number)
    if attempts_left > 0:
        await db.commit()
        return VerifyResponse(is_correct=False, attempts_left=attempts_left, message=f'Неверно. Осталось {attempts_left} попытк(и).', expand_category=False)

    status_value = 'orange' if data.is_category else 'red'
    await _upsert_report_target_snapshot(
        report_id=report.id,
        category_id=category_id,
        category_name=category_name,
        subcategory_id=subcategory_id,
        subcategory_name=subcategory_name,
        target_type=target_type,
        target_id=data.target_id,
        target_name=target_name,
        assigned_user_id_snapshot=checked_by_user.id,
        assigned_user_name_snapshot=checked_by_user.full_name,
        db=db,
    )
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
        attempts_used=attempt_number,
        checked_by_user_id=checked_by_user.id,
        checked_by_name_snapshot=checked_by_user.full_name,
        db=db,
    )
    await _clear_attempt_count(
        report_id=report.id,
        target_type=target_type,
        target_id=data.target_id,
        checked_by_user_id=checked_by_user.id,
        db=db,
    )
    await db.commit()
    _invalidate_runtime_inventory_cache(report.location)
    await _refresh_report_status(report, db)
    return VerifyResponse(
        is_correct=False,
        attempts_left=0,
        message='Расхождение! Переходим к поштучной проверке...' if data.is_category else 'Расхождение зафиксировано.',
        expand_category=data.is_category,
    )


async def start_report(report_id: int, db: AsyncSession, user: User) -> StartReportResponse:
    report = await db.get(Report, report_id)
    if not report:
        raise HTTPException(status_code=404, detail='Ревизия не найдена.')
    if user.role != RoleEnum.EMPLOYEE.value or user.location != report.location:
        raise HTTPException(status_code=403, detail='Можно начать только свою ревизию по назначенной точке.')

    await _sync_report_status(report, db)
    if report.status == 'completed':
        return StartReportResponse(success=True, message='Ревизия по этой точке уже завершена на сегодня.')

    existing = await db.scalar(
        select(ReportEmployeeStart)
        .where(ReportEmployeeStart.report_id == report.id)
        .where(ReportEmployeeStart.user_id == user.id)
        .limit(1)
    )
    if existing:
        return StartReportResponse(success=True, message='Ревизия уже начата. Можно продолжать работу.')

    db.add(ReportEmployeeStart(
        report_id=report.id,
        user_id=user.id,
        user_full_name_snapshot=user.full_name,
        started_at=datetime.utcnow(),
    ))
    await db.flush()
    await _sync_report_status(report, db)
    await db.commit()
    _invalidate_runtime_inventory_cache(report.location)
    return StartReportResponse(success=True, message='Ревизия начата. Можно приступать к работе.')


async def finish_report(report_id: int, db: AsyncSession, user: User) -> tuple[bool, str]:
    report = await db.get(Report, report_id)
    if not report:
        return False, 'Ревизия не найдена.'
    if user.role != RoleEnum.EMPLOYEE.value or user.location != report.location:
        raise HTTPException(status_code=403, detail='Можно завершать только свою ревизию по назначенной точке.')

    await _sync_report_status(report, db)
    if report.status == 'completed':
        return True, 'Ревизия по этой точке уже завершена на сегодня.'
    if not await _is_employee_started_report(report.id, user.id, db):
        raise HTTPException(status_code=409, detail='Сначала нажмите «Начать ревизию».')

    existing = await db.scalar(
        select(ReportEmployeeCompletion)
        .where(ReportEmployeeCompletion.report_id == report.id)
        .where(ReportEmployeeCompletion.user_id == user.id)
        .limit(1)
    )
    if existing:
        return True, 'Вы уже завершили свою ревизию на сегодня.'

    db.add(ReportEmployeeCompletion(
        report_id=report.id,
        user_id=user.id,
        user_full_name_snapshot=user.full_name,
        finished_at=datetime.utcnow(),
    ))
    await db.flush()
    await _sync_report_status(report, db)
    await db.commit()
    _invalidate_runtime_inventory_cache(report.location)

    if report.status == 'completed':
        return True, 'Ревизия завершена.'
    return True, f'Ваша ревизия завершена.'


async def reopen_employee_report_access(report_id: int, employee_user_id: int, db: AsyncSession, current_user: User) -> ReopenEmployeeAccessResponse:
    report = await db.get(Report, report_id)
    if not report:
        raise HTTPException(status_code=404, detail='Ревизия не найдена.')

    await ensure_user_can_access_location(current_user, report.location, db)

    if (report.report_type or DAILY_REPORT_TYPE) != DAILY_REPORT_TYPE:
        raise HTTPException(status_code=400, detail='Вернуть доступ можно только для обычной дневной ревизии.')

    if report.report_date != get_moscow_today():
        raise HTTPException(status_code=400, detail='Вернуть доступ можно только для текущей ревизии за сегодня.')

    employee = await db.get(User, employee_user_id)
    if not employee or employee.role != RoleEnum.EMPLOYEE.value:
        raise HTTPException(status_code=404, detail='Сотрудник не найден.')

    employee_location = _normalize_location(employee.location or '') if employee.location else ''
    if not employee_location or employee_location != report.location:
        raise HTTPException(status_code=403, detail='Нельзя вернуть в ревизию сотрудника из другой точки.')

    completion = await db.scalar(
        select(ReportEmployeeCompletion)
        .where(ReportEmployeeCompletion.report_id == report.id)
        .where(ReportEmployeeCompletion.user_id == employee.id)
        .limit(1)
    )

    if completion is None:
        await _sync_report_status(report, db)
        await db.commit()
        return ReopenEmployeeAccessResponse(success=True, message=f'У сотрудника {employee.full_name} уже открыт доступ к ревизии.')

    await db.delete(completion)
    await db.flush()
    await _sync_report_status(report, db)
    await db.commit()
    _invalidate_runtime_inventory_cache(report.location)
    return ReopenEmployeeAccessResponse(success=True, message=f'Сотруднику {employee.full_name} снова открыт доступ к ревизии.')


async def get_reports_history(location: str, db: AsyncSession) -> ReportHistoryResponse:
    normalized = _normalize_location(location)
    reports = (await db.scalars(select(Report).where(Report.location == normalized).order_by(Report.date_created.desc(), Report.id.desc()))).all()
    for report in reports:
        await _sync_report_status(report, db)
    await db.commit()

    report_numbers = _build_report_numbers(reports)

    history_items: list[ReportHistoryItem] = []
    for report in reports:
        report_type = report.report_type or DAILY_REPORT_TYPE
        report_number = report_numbers.get(report.id) if report_type != FINAL_REPORT_TYPE else None
        if report_type == FINAL_REPORT_TYPE:
            label = (
                f"Цикл {report.cycle_version} · Итоговая · "
                f"{_format_moscow_datetime(report.date_created)} — {_report_status_label(report.status)}"
            )
        else:
            label = (
                f"Цикл {report.cycle_version} · №{report_number or '-'} · "
                f"{_format_moscow_datetime(report.date_created)} — {_report_status_label(report.status)}"
            )
        history_items.append(
            ReportHistoryItem(
                report_id=report.id,
                report_number=report_number,
                report_type=report_type,
                date=_format_moscow_datetime(report.date_created),
                status=report.status,
                label=label,
            )
        )

    return ReportHistoryResponse(location=normalized, reports=history_items)


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


def _owner_label(owner_names: set[str]) -> str | None:
    owners = sorted({name for name in owner_names if name}, key=str.lower)
    if not owners:
        return None
    if len(owners) == 1:
        return owners[0]
    return 'Несколько сотрудников'


def _category_snapshot_assignment_label(category_id: str, report_snapshots: list[ReportTargetSnapshot]) -> str | None:
    category_level = {
        row.assigned_user_name_snapshot
        for row in report_snapshots
        if row.category_id == category_id and row.target_type == 'category' and row.assigned_user_name_snapshot
    }
    if category_level:
        return _owner_label(category_level)
    owners = {
        row.assigned_user_name_snapshot
        for row in report_snapshots
        if row.category_id == category_id and row.assigned_user_name_snapshot
    }
    return _owner_label(owners)


def _subcategory_snapshot_assignment_label(category_id: str, subcategory_id: str, report_snapshots: list[ReportTargetSnapshot]) -> str | None:
    category_level = {
        row.assigned_user_name_snapshot
        for row in report_snapshots
        if row.category_id == category_id and row.target_type == 'category' and row.assigned_user_name_snapshot
    }
    if category_level:
        return _owner_label(category_level)

    subcategory_level = {
        row.assigned_user_name_snapshot
        for row in report_snapshots
        if row.category_id == category_id and row.subcategory_id == subcategory_id and row.target_type == 'subcategory' and row.assigned_user_name_snapshot
    }
    if subcategory_level:
        return _owner_label(subcategory_level)

    item_level = {
        row.assigned_user_name_snapshot
        for row in report_snapshots
        if row.category_id == category_id and row.subcategory_id == subcategory_id and row.target_type == 'item' and row.assigned_user_name_snapshot
    }
    return _owner_label(item_level)


def _subcategory_taken_in_report(category_id: str, subcategory_id: str, report_snapshots: list[ReportTargetSnapshot]) -> bool:
    return any(
        row.category_id == category_id and (
            row.target_type == 'category'
            or (row.subcategory_id == subcategory_id and row.target_type in {'subcategory', 'item'})
        )
        for row in report_snapshots
    )


def _build_report_numbers(reports: list[Report]) -> dict[int, int]:
    ordered = sorted((item for item in reports if (item.report_type or DAILY_REPORT_TYPE) != FINAL_REPORT_TYPE), key=lambda item: (item.cycle_version, item.date_created, item.id))
    counters: dict[int, int] = defaultdict(int)
    numbers: dict[int, int] = {}

    for report in ordered:
        cycle_key = int(report.cycle_version or 0)
        counters[cycle_key] += 1
        numbers[report.id] = counters[cycle_key]

    return numbers


async def _get_report_number(report: Report, db: AsyncSession) -> int:
    if (report.report_type or DAILY_REPORT_TYPE) == FINAL_REPORT_TYPE:
        return 0

    result = await db.scalar(
        select(func.count())
        .select_from(Report)
        .where(
            Report.location == report.location,
            Report.cycle_version == report.cycle_version,
            Report.report_type == DAILY_REPORT_TYPE,
            or_(
                Report.date_created < report.date_created,
                and_(Report.date_created == report.date_created, Report.id <= report.id),
            ),
        )
    )
    return int(result or 0)


async def _load_discrepancy_financials(location: str, results: list[CheckResult]) -> dict[str, dict[str, float | None]]:
    if not ms_client.enabled:
        return {}

    unique_item_ids = sorted({
        row.target_id
        for row in results
        if row.target_type == 'item' and row.status == 'red' and row.target_id
    })
    if not unique_item_ids:
        return {}

    semaphore = asyncio.Semaphore(getattr(ms_client, 'max_concurrent_requests', 4))

    async def fetch(item_id: str) -> tuple[str, dict[str, float | None]]:
        async with semaphore:
            try:
                values = await ms_client.get_item_financials(location, item_id)
            except Exception:
                return item_id, {'cost_price': None, 'retail_price': None}
            return item_id, values

    loaded = await asyncio.gather(*(fetch(item_id) for item_id in unique_item_ids))
    return {item_id: values for item_id, values in loaded}


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
        return AdminReport(report_id=None, report_number=None, report_type=DAILY_REPORT_TYPE, date='-', location=normalized, status='-', categories=[], total_plus=0.0, total_minus=0.0, can_manage_employee_completion=False, employees=[])

    await _sync_report_status(report, db)
    await db.commit()

    targets = await _load_selection_targets(normalized, report.cycle_version, db)
    inventory = await _get_inventory_for(normalized)

    report_type = report.report_type or DAILY_REPORT_TYPE
    if report_type == FINAL_REPORT_TYPE:
        cycle_reports = (
            await db.scalars(
                select(Report).where(
                    Report.location == normalized,
                    Report.cycle_version == report.cycle_version,
                    Report.report_type == DAILY_REPORT_TYPE,
                ).order_by(Report.date_created.asc(), Report.id.asc())
            )
        ).all()
        cycle_report_ids = [row.id for row in cycle_reports]
        report_snapshots = await _load_report_target_snapshots_for_report_ids(cycle_report_ids, db)
        raw_results = [
            row for row in await _load_results_for_report_ids(cycle_report_ids, db)
            if row.category_name != DEFAULT_CATEGORY_NAME and (row.subcategory_name is None or row.subcategory_name != DEFAULT_SUBCATEGORY_NAME)
        ]
        results_by_target_key: dict[tuple[str, str], CheckResult] = {}
        for row in raw_results:
            results_by_target_key[(row.target_type, row.target_id)] = row
        results = list(results_by_target_key.values())
    else:
        assignments = await _load_assignments(report.location, report.cycle_version, db)
        report_snapshots = await _bootstrap_report_target_snapshots(report, assignments, [], db)
        results = [
            row for row in await _load_results(report.id, db)
            if row.category_name != DEFAULT_CATEGORY_NAME and (row.subcategory_name is None or row.subcategory_name != DEFAULT_SUBCATEGORY_NAME)
        ]

    discrepancy_financials = await _load_discrepancy_financials(normalized, results)
    participant_user_ids = await _get_report_participant_user_ids(report.id, db) if report_type == DAILY_REPORT_TYPE else set()
    if participant_user_ids and report_type == DAILY_REPORT_TYPE:
        report_snapshots = [
            row for row in report_snapshots
            if row.assigned_user_id_snapshot is None or row.assigned_user_id_snapshot in participant_user_ids
        ]
    historical_category_ids, historical_subcategory_ids, historical_item_ids = _report_history_target_maps(report_snapshots, results)
    active_employees = await _active_employee_users_for_location(report.location, db)
    active_employee_by_id = {employee.id: employee for employee in active_employees}
    report_employees = [active_employee_by_id[user_id] for user_id in sorted(participant_user_ids) if user_id in active_employee_by_id]
    completions = await _load_report_employee_completions(report.id, db)
    starts = await _load_report_employee_starts(report.id, db)
    can_manage_employee_completion = (report.report_type or DAILY_REPORT_TYPE) == DAILY_REPORT_TYPE and report.report_date == get_moscow_today()

    rows_by_category_target: dict[str, dict[str, CheckResult]] = defaultdict(dict)
    for row in results:
        rows_by_category_target[row.category_id][row.target_id] = row

    completion_by_user_id = {completion.user_id: completion for completion in completions}
    start_by_user_id = {start.user_id: start for start in starts}
    active_employee_by_name = {employee.full_name: employee for employee in active_employees}
    grouped_problem_items: dict[str, list[DiscrepancyItem]] = defaultdict(list)
    employee_bucket: dict[str, EmployeeReportSummary] = {}

    def ensure_employee_bucket(full_name: str | None, user_id: int | None = None) -> EmployeeReportSummary | None:
        if not full_name:
            return None
        summary = employee_bucket.get(full_name)
        active_employee = active_employee_by_name.get(full_name)
        resolved_user_id = user_id or (active_employee.id if active_employee else None)
        completion = completion_by_user_id.get(resolved_user_id) if resolved_user_id else None
        start = start_by_user_id.get(resolved_user_id) if resolved_user_id else None
        if summary is None:
            summary = EmployeeReportSummary(
                user_id=resolved_user_id,
                full_name=full_name,
                categories=[],
                completed_categories=0,
                discrepancy_items=0,
                started_current_report=start is not None,
                started_at=_format_completion_datetime(start.started_at) if start else None,
                finished_current_report=completion is not None,
                finished_at=_format_completion_datetime(completion.finished_at) if completion else None,
                can_reopen_access=bool(can_manage_employee_completion and completion is not None and resolved_user_id),
            )
            employee_bucket[full_name] = summary
            return summary

        if summary.user_id is None and resolved_user_id is not None:
            summary.user_id = resolved_user_id
        if start is not None:
            summary.started_current_report = True
            summary.started_at = _format_completion_datetime(start.started_at)
        if completion is not None:
            summary.finished_current_report = True
            summary.finished_at = _format_completion_datetime(completion.finished_at)
        summary.can_reopen_access = bool(can_manage_employee_completion and summary.finished_current_report and summary.user_id)
        return summary

    for employee in report_employees:
        ensure_employee_bucket(employee.full_name, employee.id)

    for row in results:
        if row.checked_by_name_snapshot:
            bucket = ensure_employee_bucket(row.checked_by_name_snapshot, row.checked_by_user_id)
            if bucket and row.category_name not in bucket.categories:
                bucket.categories.append(row.category_name)

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

            if row.checked_by_name_snapshot:
                bucket = ensure_employee_bucket(row.checked_by_name_snapshot, row.checked_by_user_id)
                if bucket:
                    bucket.discrepancy_items += 1
                    bucket.total_cost = float(round(float(bucket.total_cost or 0.0) + float(cost_total or 0.0), 2))
                    bucket.total_retail = float(round(float(bucket.total_retail or 0.0) + float(retail_total or 0.0), 2))
                    bucket.total_lost_profit = float(round(float(bucket.total_lost_profit or 0.0) + float(lost_profit or 0.0), 2))

            grouped_problem_items[row.category_name].append(
                DiscrepancyItem(
                    name=row.target_name,
                    expected=float(row.expected_qty),
                    actual=float(row.actual_qty or 0),
                    diff=float(row.diff or 0),
                    checked_by=row.checked_by_name_snapshot,
                    subcategory_name=row.subcategory_name,
                    cost_price=cost_price,
                    retail_price=retail_price,
                    cost_total=cost_total,
                    retail_total=retail_total,
                    lost_profit=lost_profit,
                )
            )

    categories: list[CategoryResult] = []
    selected_category_ids, selected_subcategory_ids = _selection_target_maps(targets)
    target_category_names = sorted({target.category_name for target in targets if target.target_type == 'category'})
    target_subcategory_labels = sorted({
        f"{target.category_name} → {target.subcategory_name}"
        for target in targets
        if target.target_type == 'subcategory' and target.subcategory_name
    })
    inventory = _filter_inventory_by_targets(
        inventory,
        selected_category_ids,
        selected_subcategory_ids,
        retained_category_ids=historical_category_ids,
        retained_subcategory_ids=historical_subcategory_ids,
        retained_item_ids=historical_item_ids,
    )
    for raw_category in inventory['categories']:
        result_map = rows_by_category_target.get(raw_category['id'], {})
        is_completed, status = (True, StatusEnum.GREEN) if _category_is_complete(raw_category, result_map) else (False, StatusEnum.GREY)
        if not is_completed:
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

        selected_sub_names = sorted([
            sub['name']
            for sub in raw_category['subcategories']
            if sub['id'] in selected_subcategory_ids.get(raw_category['id'], set())
        ])
        completed_subcategories: list[CompletedSubcategoryInfo] = []
        in_progress_subcategories: list[InProgressSubcategoryInfo] = []
        for raw_sub in raw_category['subcategories']:
            if _is_categoryless_subcategory(raw_category, raw_sub):
                continue

            sub_completed, sub_status = _subcategory_is_complete(raw_sub, result_map)
            sub_taken_in_report = _subcategory_taken_in_report(raw_category['id'], raw_sub['id'], report_snapshots)

            if sub_completed and sub_status == StatusEnum.GREEN:
                sub_row = result_map.get(raw_sub['id'])
                checked_by = sub_row.checked_by_name_snapshot if sub_row else None
                if not checked_by:
                    item_rows = [result_map.get(item['id']) for item in raw_sub['items']]
                    item_rows = [row for row in item_rows if row and row.checked_by_name_snapshot]
                    if item_rows:
                        owners = {row.checked_by_name_snapshot for row in item_rows if row.checked_by_name_snapshot}
                        checked_by = _owner_label(owners)
                completed_subcategories.append(CompletedSubcategoryInfo(
                    name=raw_sub['name'],
                    checked_by=checked_by,
                    status=sub_status,
                ))
                continue

            if not sub_taken_in_report:
                continue

            in_progress_subcategories.append(InProgressSubcategoryInfo(
                name=raw_sub['name'],
                assigned_to=_subcategory_snapshot_assignment_label(raw_category['id'], raw_sub['id'], report_snapshots),
            ))

        completed_subcategories.sort(key=lambda item: item.name.lower())
        in_progress_subcategories.sort(key=lambda item: item.name.lower())
        remaining_subcategories = [item.name for item in in_progress_subcategories]

        categories.append(CategoryResult(
            name=raw_category['name'],
            status=status,
            assigned_to=_category_snapshot_assignment_label(raw_category['id'], report_snapshots),
            selected_on_cycle=raw_category['id'] in selected_category_ids,
            selected_subcategories=selected_sub_names,
            remaining_subcategories=remaining_subcategories,
            in_progress_subcategories=in_progress_subcategories,
            completed_subcategories=completed_subcategories,
            problem_items=grouped_problem_items.get(raw_category['name'], []),
        ))

    for category in categories:
        owners = {item.checked_by for item in category.problem_items if item.checked_by}
        if len(owners) == 1:
            owner = next(iter(owners))
            owner_bucket = ensure_employee_bucket(owner)
            if owner_bucket and category.name not in owner_bucket.categories:
                owner_bucket.categories.append(category.name)

    for summary in employee_bucket.values():
        summary.categories = sorted(summary.categories, key=str.lower)
        summary.completed_categories = sum(1 for category in categories if category.assigned_to == summary.full_name and category.status in {StatusEnum.GREEN, StatusEnum.RED})
        summary.total_cost = float(round(float(summary.total_cost or 0.0), 2))
        summary.total_retail = float(round(float(summary.total_retail or 0.0), 2))
        summary.total_lost_profit = float(round(float(summary.total_lost_profit or 0.0), 2))

    total_plus = sum(max(float(item.diff), 0.0) for items in grouped_problem_items.values() for item in items)
    total_minus = abs(sum(min(float(item.diff), 0.0) for items in grouped_problem_items.values() for item in items))
    total_cost = sum(float(item.cost_total or 0.0) for items in grouped_problem_items.values() for item in items)
    total_retail = sum(float(item.retail_total or 0.0) for items in grouped_problem_items.values() for item in items)
    total_lost_profit = sum(float(item.lost_profit or 0.0) for items in grouped_problem_items.values() for item in items)

    report_number = await _get_report_number(report, db)

    return AdminReport(
        report_id=report.id,
        report_number=report_number or None,
        report_type=report_type,
        date=_format_moscow_datetime(report.date_created),
        location=report.location,
        status=_report_status_label(report.status),
        categories=categories,
        selected_categories=target_category_names,
        selected_subcategories=target_subcategory_labels,
        total_plus=float(total_plus),
        total_minus=float(total_minus),
        total_cost=float(round(total_cost, 2)),
        total_retail=float(round(total_retail, 2)),
        total_lost_profit=float(round(total_lost_profit, 2)),
        can_manage_employee_completion=can_manage_employee_completion,
        employees=sorted(employee_bucket.values(), key=lambda item: item.full_name.lower()),
    )


async def delete_report(report_id: int, db: AsyncSession, current_user: User | None = None) -> DeleteResponse:
    report = await db.get(Report, report_id)
    if not report:
        raise HTTPException(status_code=404, detail='Ревизия не найдена.')
    if current_user is not None:
        await ensure_user_can_access_location(current_user, report.location, db)
    location = report.location
    await db.delete(report)
    await db.commit()
    _invalidate_runtime_inventory_cache(location)
    return DeleteResponse(success=True, message='Ревизия удалена.')
