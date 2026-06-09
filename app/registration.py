from __future__ import annotations

import logging
import secrets
from datetime import date, datetime
from typing import Iterable

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.logic import (
    _assign_admin_location_access_by_ids,
    _ensure_email_is_unique,
    _normalize_location,
    _validate_email,
    hash_password,
)
from app.mailer import MailerConfigurationError, send_email_message
from app.models import LocationPoint, ReferralLink, RegistrationRequest, User
from app.notifications import notify_platform_admins
from app.schemas import (
    ReferralLinkActionResponse,
    ReferralLinkCreateRequest,
    ReferralLinkModel,
    ReferralLinksResponse,
    ReferralResolveResponse,
    ReferralUserModel,
    DeleteResponse,
    RegistrationActionResponse,
    RegistrationApplicationModel,
    RegistrationCreateRequest,
    RegistrationCreateResponse,
    RegistrationListResponse,
    RegistrationRejectRequest,
    RoleEnum,
)

logger = logging.getLogger(__name__)

REGISTRATION_STATUS_PENDING = 'pending'
REGISTRATION_STATUS_APPROVED = 'approved'
REGISTRATION_STATUS_REJECTED = 'rejected'


def _base_app_url() -> str:
    configured = str(getattr(settings, 'public_app_url', '') or '').strip().rstrip('/')
    return configured or 'https://uchetka-retail.ru'


def _referral_url(token: str) -> str:
    return f'{_base_app_url()}/r/{token}'


def _clean_text(value: str | None) -> str:
    return ' '.join(str(value or '').strip().split())


def _clean_optional_text(value: str | None) -> str | None:
    cleaned = _clean_text(value)
    return cleaned or None


def _normalize_username(value: str | None) -> str:
    username = str(value or '').strip()
    if not username:
        raise HTTPException(status_code=400, detail='Укажите логин.')
    return username


def _safe_setting_emails(value: str | None) -> list[str]:
    raw = str(value or '').replace(';', ',')
    seen: set[str] = set()
    result: list[str] = []
    for item in raw.split(','):
        email = item.strip().lower()
        if not email or email in seen:
            continue
        seen.add(email)
        result.append(email)
    return result


async def _superadmin_notification_emails(db: AsyncSession) -> list[str]:
    configured = _safe_setting_emails(getattr(settings, 'registration_notify_email', None))
    if configured:
        return configured

    rows = (
        await db.scalars(
            select(User.email)
            .where(
                User.role == RoleEnum.PLATFORM_ADMIN.value,
                User.is_active.is_(True),
                User.email.is_not(None),
            )
            .order_by(User.id.asc())
        )
    ).all()
    recipients = _safe_setting_emails(','.join(str(row or '') for row in rows))
    if recipients:
        return recipients
    return _safe_setting_emails(getattr(settings, 'smtp_from_email', None))


async def _send_many(to_emails: Iterable[str], *, subject: str, text: str) -> None:
    for email in to_emails:
        try:
            await send_email_message(to_email=email, subject=subject, text=text)
        except MailerConfigurationError as exc:
            logger.warning('SMTP не настроен для уведомления о регистрации: %s', exc)
            return
        except Exception:
            logger.exception('Не удалось отправить уведомление о регистрации на %s', email)


async def _notify_superadmins_about_registration(request_row: RegistrationRequest, db: AsyncSession) -> None:
    recipients = await _superadmin_notification_emails(db)
    if not recipients:
        logger.warning('Заявка на регистрацию #%s создана, но email админа не найден.', request_row.id)
        return

    referral_line = 'Реферальная ссылка: нет\n'
    if request_row.referred_by_user_id:
        owner = await db.get(User, request_row.referred_by_user_id)
        referral_line = f'Реферальная ссылка: {owner.full_name if owner else "пользователь удалён"}\n'

    await _send_many(
        recipients,
        subject='Новая заявка на регистрацию UCHETKA',
        text=(
            'В UCHETKA создана новая заявка на регистрацию.\n\n'
            f'Заявка: #{request_row.id}\n'
            f'ФИО: {request_row.full_name}\n'
            f'Организация: {request_row.organization_name}\n'
            f'Первая точка: {request_row.location_name}\n'
            f'Логин: {request_row.username}\n'
            f'Email: {request_row.email}\n'
            f'Телефон: {request_row.phone or "—"}\n'
            f'{referral_line}\n'
            'Чтобы разрешить доступ, войдите в сервис админом и откройте раздел «Заявки».\n'
        ),
    )


async def _notify_applicant(request_row: RegistrationRequest, *, approved: bool, reason: str | None = None) -> None:
    subject = 'Доступ к UCHETKA одобрен' if approved else 'Заявка на доступ к UCHETKA отклонена'
    if approved:
        text = (
            'Ваша заявка на доступ к UCHETKA одобрена.\n\n'
            f'Логин: {request_row.username}\n'
            'Пароль: тот, который вы указали при регистрации.\n\n'
            'Теперь можно войти в сервис и подключить свою точку к МойСклад в разделе «Точки».\n'
        )
    else:
        text = (
            'Ваша заявка на доступ к UCHETKA отклонена.\n'
            f'Причина: {reason or "не указана"}\n'
        )
    await _send_many([request_row.email], subject=subject, text=text)


async def _ensure_username_available(username: str, db: AsyncSession, *, exclude_request_id: int | None = None) -> None:
    existing_user_id = await db.scalar(select(User.id).where(User.username == username).limit(1))
    if existing_user_id:
        raise HTTPException(status_code=400, detail='Пользователь с таким логином уже существует.')

    pending_query = select(RegistrationRequest.id).where(
        RegistrationRequest.username == username,
        RegistrationRequest.status == REGISTRATION_STATUS_PENDING,
    )
    if exclude_request_id is not None:
        pending_query = pending_query.where(RegistrationRequest.id != exclude_request_id)
    pending_id = await db.scalar(pending_query.limit(1))
    if pending_id:
        raise HTTPException(status_code=400, detail='Заявка с таким логином уже ожидает подтверждения.')


async def _ensure_registration_email_available(email: str, db: AsyncSession, *, exclude_request_id: int | None = None) -> None:
    await _ensure_email_is_unique(email, db)
    pending_query = select(RegistrationRequest.id).where(
        func.lower(RegistrationRequest.email) == email.lower(),
        RegistrationRequest.status == REGISTRATION_STATUS_PENDING,
    )
    if exclude_request_id is not None:
        pending_query = pending_query.where(RegistrationRequest.id != exclude_request_id)
    pending_id = await db.scalar(pending_query.limit(1))
    if pending_id:
        raise HTTPException(status_code=400, detail='Заявка с таким email уже ожидает подтверждения.')


def _to_model(row: RegistrationRequest, referred_by_user_name: str | None = None) -> RegistrationApplicationModel:
    model = RegistrationApplicationModel.model_validate(row)
    model.referred_by_user_name = referred_by_user_name
    return model


async def _resolve_referral_token(token: str | None, db: AsyncSession) -> ReferralLink | None:
    normalized = str(token or '').strip()
    if not normalized:
        return None
    link = await db.scalar(
        select(ReferralLink)
        .where(ReferralLink.token == normalized, ReferralLink.is_active.is_(True))
        .limit(1)
    )
    if not link:
        raise HTTPException(status_code=400, detail='Реферальная ссылка не найдена или уже отключена.')
    return link


async def create_registration_request(payload: RegistrationCreateRequest, db: AsyncSession) -> RegistrationCreateResponse:
    password = payload.password or ''
    if password != payload.password_confirm:
        raise HTTPException(status_code=400, detail='Пароли не совпадают.')
    if len(password) < 6:
        raise HTTPException(status_code=400, detail='Пароль должен быть не короче 6 символов.')

    full_name = _clean_text(payload.full_name)
    organization_name = _clean_text(payload.organization_name)
    location_name = _normalize_location(payload.location_name)
    username = _normalize_username(payload.username)
    email = _validate_email(payload.email)
    if not email:
        raise HTTPException(status_code=400, detail='Укажите email.')

    await _ensure_username_available(username, db)
    await _ensure_registration_email_available(email, db)
    referral_link = await _resolve_referral_token(payload.referral_token, db)

    row = RegistrationRequest(
        full_name=full_name,
        organization_name=organization_name,
        location_name=location_name,
        username=username,
        email=email,
        phone=_clean_optional_text(payload.phone),
        password_hash=hash_password(password),
        comment=_clean_optional_text(payload.comment),
        status=REGISTRATION_STATUS_PENDING,
        created_at=datetime.utcnow(),
        referral_token=referral_link.token if referral_link else None,
        referral_link_id=referral_link.id if referral_link else None,
        referred_by_user_id=referral_link.owner_user_id if referral_link else None,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    await notify_platform_admins(
        db,
        notification_type='registration_request',
        title='Новая заявка на регистрацию',
        message=f'{row.full_name} отправил заявку для организации «{row.organization_name}» и точки «{row.location_name}».',
        url='/admin',
        payload={'request_id': row.id, 'username': row.username, 'location': row.location_name},
        commit=True,
    )
    await _notify_superadmins_about_registration(row, db)
    return RegistrationCreateResponse(
        message='Заявка отправлена. Доступ появится после подтверждения админом.',
        request_id=row.id,
    )


async def _referrer_names_for_requests(rows: list[RegistrationRequest], db: AsyncSession) -> dict[int, str]:
    referrer_ids = sorted({int(row.referred_by_user_id) for row in rows if row.referred_by_user_id})
    if not referrer_ids:
        return {}
    users = (await db.scalars(select(User).where(User.id.in_(referrer_ids)))).all()
    return {int(user.id): user.full_name for user in users}


async def list_registration_requests(db: AsyncSession, *, status: str | None = None) -> RegistrationListResponse:
    normalized_status = (status or '').strip().lower()
    query = select(RegistrationRequest)
    if normalized_status and normalized_status != 'all':
        query = query.where(RegistrationRequest.status == normalized_status)
    rows = (
        await db.scalars(
            query.order_by(
                RegistrationRequest.status.asc(),
                RegistrationRequest.created_at.desc(),
                RegistrationRequest.id.desc(),
            )
        )
    ).all()
    names = await _referrer_names_for_requests(list(rows), db)
    return RegistrationListResponse(requests=[_to_model(row, names.get(int(row.referred_by_user_id or 0))) for row in rows])


async def _make_unique_location_name(base_name: str, organization_name: str, db: AsyncSession) -> str:
    base = _normalize_location(base_name)
    candidates = [base]
    org = _normalize_location(organization_name)
    if org and org != base:
        candidates.append(f'{org} — {base}')

    for candidate in candidates:
        exists = await db.scalar(select(LocationPoint.id).where(LocationPoint.name == candidate).limit(1))
        if not exists:
            return candidate

    prefix = candidates[-1]
    for index in range(2, 1000):
        candidate = f'{prefix} #{index}'
        exists = await db.scalar(select(LocationPoint.id).where(LocationPoint.name == candidate).limit(1))
        if not exists:
            return candidate
    raise HTTPException(status_code=400, detail='Не удалось подобрать уникальное название точки.')


async def approve_registration_request(request_id: int, db: AsyncSession, current_user: User) -> RegistrationActionResponse:
    row = await db.get(RegistrationRequest, request_id)
    if not row:
        raise HTTPException(status_code=404, detail='Заявка не найдена.')
    if row.status != REGISTRATION_STATUS_PENDING:
        raise HTTPException(status_code=400, detail='Эта заявка уже обработана.')

    await _ensure_username_available(row.username, db, exclude_request_id=row.id)
    await _ensure_registration_email_available(row.email, db, exclude_request_id=row.id)

    location_name = await _make_unique_location_name(row.location_name, row.organization_name, db)
    location = LocationPoint(
        name=location_name,
        ms_token=None,
        ms_store_id=None,
        ms_store_name=None,
        created_at=datetime.utcnow(),
    )
    db.add(location)
    await db.flush()

    user = User(
        full_name=row.full_name,
        birth_date=date(1990, 1, 1),
        username=row.username,
        email=row.email,
        password_hash=row.password_hash,
        role=RoleEnum.SUPERADMIN.value,
        location=None,
        is_active=True,
        created_at=datetime.utcnow(),
    )
    db.add(user)
    await db.flush()

    await _assign_admin_location_access_by_ids(user.id, [location.id], db, granted_by_user_id=current_user.id)

    now = datetime.utcnow()
    row.status = REGISTRATION_STATUS_APPROVED
    row.decided_at = now
    row.approved_by_user_id = current_user.id
    row.created_user_id = user.id
    row.created_location_point_id = location.id
    await db.commit()
    await db.refresh(row)

    await _notify_applicant(row, approved=True)
    return RegistrationActionResponse(success=True, message='Заявка одобрена. Главный управляющий и первая точка созданы.', request=_to_model(row))


async def reject_registration_request(request_id: int, payload: RegistrationRejectRequest, db: AsyncSession, current_user: User) -> RegistrationActionResponse:
    row = await db.get(RegistrationRequest, request_id)
    if not row:
        raise HTTPException(status_code=404, detail='Заявка не найдена.')
    if row.status != REGISTRATION_STATUS_PENDING:
        raise HTTPException(status_code=400, detail='Эта заявка уже обработана.')

    reason = _clean_optional_text(payload.reason)
    now = datetime.utcnow()
    row.status = REGISTRATION_STATUS_REJECTED
    row.rejection_reason = reason
    row.decided_at = now
    row.approved_by_user_id = current_user.id
    await db.commit()
    await db.refresh(row)

    await _notify_applicant(row, approved=False, reason=reason)
    return RegistrationActionResponse(success=True, message='Заявка отклонена.', request=_to_model(row))



def _user_to_referral_user_model(user: User) -> ReferralUserModel:
    return ReferralUserModel(
        id=user.id,
        full_name=user.full_name,
        username=user.username,
        role=RoleEnum(user.role),
        location=user.location,
    )


async def _build_referral_link_model(link: ReferralLink, db: AsyncSession) -> ReferralLinkModel:
    owner = await db.get(User, link.owner_user_id)
    if not owner:
        owner = User(
            id=0,
            full_name='Пользователь удалён',
            birth_date=date(1990, 1, 1),
            username='deleted',
            password_hash='',
            role=RoleEnum.EMPLOYEE.value,
            location=None,
            is_active=False,
        )
    requests = (
        await db.scalars(
            select(RegistrationRequest)
            .where(RegistrationRequest.referral_link_id == link.id)
            .order_by(RegistrationRequest.created_at.desc(), RegistrationRequest.id.desc())
        )
    ).all()
    names = await _referrer_names_for_requests(list(requests), db)
    return ReferralLinkModel(
        id=link.id,
        token=link.token,
        url=_referral_url(link.token),
        owner=_user_to_referral_user_model(owner),
        is_active=link.is_active,
        created_at=link.created_at,
        registrations=[_to_model(row, names.get(int(row.referred_by_user_id or 0))) for row in requests],
    )


async def create_referral_link(payload: ReferralLinkCreateRequest, db: AsyncSession, current_user: User) -> ReferralLinkActionResponse:
    owner = await db.get(User, payload.user_id)
    if not owner:
        raise HTTPException(status_code=404, detail='Пользователь не найден.')
    existing = await db.scalar(
        select(ReferralLink)
        .where(ReferralLink.owner_user_id == owner.id, ReferralLink.is_active.is_(True))
        .order_by(ReferralLink.created_at.desc(), ReferralLink.id.desc())
        .limit(1)
    )
    if existing:
        return ReferralLinkActionResponse(
            message='У пользователя уже есть активная реферальная ссылка.',
            link=await _build_referral_link_model(existing, db),
        )

    for _ in range(10):
        token = secrets.token_urlsafe(24)
        duplicate = await db.scalar(select(ReferralLink.id).where(ReferralLink.token == token).limit(1))
        if duplicate:
            continue
        link = ReferralLink(
            token=token,
            owner_user_id=owner.id,
            created_by_user_id=current_user.id,
            is_active=True,
            created_at=datetime.utcnow(),
        )
        db.add(link)
        await db.commit()
        await db.refresh(link)
        return ReferralLinkActionResponse(
            message='Реферальная ссылка создана.',
            link=await _build_referral_link_model(link, db),
        )
    raise HTTPException(status_code=500, detail='Не удалось сгенерировать уникальную ссылку.')


async def list_referral_links(db: AsyncSession) -> ReferralLinksResponse:
    links = (
        await db.scalars(
            select(ReferralLink)
            .where(ReferralLink.is_active.is_(True))
            .order_by(ReferralLink.created_at.desc(), ReferralLink.id.desc())
        )
    ).all()
    return ReferralLinksResponse(links=[await _build_referral_link_model(link, db) for link in links])


async def delete_referral_link(link_id: int, db: AsyncSession) -> DeleteResponse:
    link = await db.get(ReferralLink, link_id)
    if not link:
        raise HTTPException(status_code=404, detail='Реферальная ссылка не найдена.')

    affected_requests = await db.scalar(
        select(func.count(RegistrationRequest.id)).where(RegistrationRequest.referral_link_id == link.id)
    ) or 0

    await db.execute(
        update(RegistrationRequest)
        .where(RegistrationRequest.referral_link_id == link.id)
        .values(
            referral_token=None,
            referral_link_id=None,
            referred_by_user_id=None,
        )
    )
    await db.delete(link)
    await db.commit()

    suffix = ''
    if int(affected_requests):
        suffix = f' Привязка снята с заявок: {int(affected_requests)}.'
    return DeleteResponse(success=True, message=f'Реферальная ссылка удалена.{suffix}')


async def resolve_referral_token(token: str, db: AsyncSession) -> ReferralResolveResponse:
    normalized = str(token or '').strip()
    if not normalized:
        return ReferralResolveResponse(found=False)
    link = await db.scalar(
        select(ReferralLink)
        .where(ReferralLink.token == normalized, ReferralLink.is_active.is_(True))
        .limit(1)
    )
    if not link:
        return ReferralResolveResponse(found=False)
    owner = await db.get(User, link.owner_user_id)
    return ReferralResolveResponse(found=True, referrer_name=owner.full_name if owner else 'пользователь удалён')
