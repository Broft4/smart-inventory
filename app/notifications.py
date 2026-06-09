from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AdminLocationAccess, AppNotification, User

MANAGEMENT_ROLES = {'platform_admin', 'superadmin', 'admin'}


def _clean_text(value: Any, *, limit: int | None = None) -> str:
    text = ' '.join(str(value or '').strip().split())
    if limit is not None and len(text) > limit:
        return text[: max(limit - 1, 0)].rstrip() + '…'
    return text


def _safe_json(payload: dict[str, Any] | None) -> str | None:
    if not payload:
        return None
    try:
        return json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        return None


async def _active_users_by_ids(db: AsyncSession, user_ids: Iterable[int]) -> list[User]:
    unique_ids = sorted({int(user_id) for user_id in user_ids if user_id})
    if not unique_ids:
        return []
    return (
        await db.scalars(
            select(User)
            .where(User.id.in_(unique_ids), User.is_active.is_(True))
            .order_by(User.id.asc())
        )
    ).all()


async def _platform_admins(db: AsyncSession) -> list[User]:
    return (
        await db.scalars(
            select(User)
            .where(User.role == 'platform_admin', User.is_active.is_(True))
            .order_by(User.id.asc())
        )
    ).all()


async def _location_managers(
    db: AsyncSession,
    location_point_id: int,
    *,
    exclude_user_id: int | None = None,
) -> list[User]:
    user_ids: set[int] = set()

    platform_admin_ids = (
        await db.scalars(
            select(User.id)
            .where(User.role == 'platform_admin', User.is_active.is_(True))
        )
    ).all()
    user_ids.update(int(user_id) for user_id in platform_admin_ids if user_id)

    access_user_ids = (
        await db.scalars(
            select(AdminLocationAccess.admin_user_id)
            .join(User, User.id == AdminLocationAccess.admin_user_id)
            .where(
                AdminLocationAccess.location_point_id == location_point_id,
                User.role.in_(['superadmin', 'admin']),
                User.is_active.is_(True),
            )
        )
    ).all()
    user_ids.update(int(user_id) for user_id in access_user_ids if user_id)

    if exclude_user_id:
        user_ids.discard(int(exclude_user_id))

    return await _active_users_by_ids(db, user_ids)


async def create_notifications(
    db: AsyncSession,
    recipients: Iterable[User],
    *,
    notification_type: str,
    title: str,
    message: str,
    url: str | None = None,
    payload: dict[str, Any] | None = None,
) -> int:
    seen: set[int] = set()
    count = 0
    for user in recipients:
        if not user or not user.id or user.id in seen:
            continue
        seen.add(int(user.id))
        db.add(AppNotification(
            user_id=int(user.id),
            notification_type=_clean_text(notification_type, limit=60) or 'system',
            title=_clean_text(title, limit=255) or 'Уведомление',
            message=_clean_text(message) or 'Новое событие в сервисе.',
            url=_clean_text(url, limit=255) or None,
            payload_json=_safe_json(payload),
            is_read=False,
            created_at=datetime.utcnow(),
        ))
        count += 1
    return count


async def notify_platform_admins(
    db: AsyncSession,
    *,
    notification_type: str,
    title: str,
    message: str,
    url: str | None = None,
    payload: dict[str, Any] | None = None,
    commit: bool = False,
) -> int:
    count = await create_notifications(
        db,
        await _platform_admins(db),
        notification_type=notification_type,
        title=title,
        message=message,
        url=url,
        payload=payload,
    )
    if commit and count:
        await db.commit()
    return count


async def notify_location_managers(
    db: AsyncSession,
    *,
    location_point_id: int,
    actor_user_id: int | None = None,
    notification_type: str,
    title: str,
    message: str,
    url: str | None = None,
    payload: dict[str, Any] | None = None,
) -> int:
    recipients = await _location_managers(db, location_point_id, exclude_user_id=actor_user_id)
    return await create_notifications(
        db,
        recipients,
        notification_type=notification_type,
        title=title,
        message=message,
        url=url,
        payload=payload,
    )


async def list_unread_notifications(db: AsyncSession, user: User, *, limit: int = 50) -> dict[str, Any]:
    limit = min(max(int(limit or 50), 1), 100)
    rows = (
        await db.scalars(
            select(AppNotification)
            .where(AppNotification.user_id == user.id, AppNotification.is_read.is_(False))
            .order_by(AppNotification.created_at.desc(), AppNotification.id.desc())
            .limit(limit)
        )
    ).all()
    total = await db.scalar(
        select(func.count(AppNotification.id))
        .where(AppNotification.user_id == user.id, AppNotification.is_read.is_(False))
    )
    return {
        'unread_count': int(total or 0),
        'items': [
            {
                'id': row.id,
                'type': row.notification_type,
                'title': row.title,
                'message': row.message,
                'url': row.url,
                'created_at': row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ],
    }


async def mark_unread_notifications_read(db: AsyncSession, user: User) -> dict[str, Any]:
    now = datetime.utcnow()
    result = await db.execute(
        update(AppNotification)
        .where(AppNotification.user_id == user.id, AppNotification.is_read.is_(False))
        .values(is_read=True, read_at=now)
    )
    await db.commit()
    return {'success': True, 'cleared': int(result.rowcount or 0)}
