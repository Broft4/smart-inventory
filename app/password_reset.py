from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.logic import hash_password, verify_password
from app.mailer import MailerConfigurationError, send_password_reset_code
from app.models import PasswordResetRequest, User
from app.schemas import (
    PasswordResetCompleteRequest,
    PasswordResetCompleteResponse,
    PasswordResetRequestRequest,
    PasswordResetRequestResponse,
    PasswordResetVerifyRequest,
    PasswordResetVerifyResponse,
)

logger = logging.getLogger(__name__)

_RESET_CODE_SENT_MESSAGE = 'Код восстановления отправлен на указанную почту.'
_EMAIL_NOT_FOUND_MESSAGE = 'Почта не найдена в системе. Проверьте адрес или обратитесь к управляющему.'
_INVALID_CODE_MESSAGE = 'Неверный или устаревший код восстановления.'
_EMAIL_RE = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')


def normalize_email(value: str | None) -> str | None:
    raw = str(value or '').strip().lower()
    return raw or None


def validate_email_or_none(value: str | None) -> str | None:
    email = normalize_email(value)
    if email and not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail='Укажите корректный email.')
    return email


def _safe_now() -> datetime:
    return datetime.utcnow()


def _make_code() -> str:
    return f'{secrets.randbelow(1_000_000):06d}'


def _make_public_token() -> str:
    return secrets.token_urlsafe(32)


async def _find_user_for_reset(email: str, db: AsyncSession) -> User | None:
    normalized_email = normalize_email(email)
    if not normalized_email or not _EMAIL_RE.match(normalized_email):
        raise HTTPException(status_code=400, detail='Укажите корректный email.')

    rows = (
        await db.scalars(
            select(User)
            .where(func.lower(User.email) == normalized_email)
            .limit(2)
        )
    ).all()
    if not rows:
        raise HTTPException(status_code=404, detail=_EMAIL_NOT_FOUND_MESSAGE)
    if len(rows) > 1:
        raise HTTPException(
            status_code=409,
            detail='К этой почте привязано несколько сотрудников. Обратитесь к управляющему.',
        )
    user = rows[0]
    if not user.is_active or not user.email:
        raise HTTPException(status_code=404, detail=_EMAIL_NOT_FOUND_MESSAGE)
    return user


async def request_password_reset(payload: PasswordResetRequestRequest, db: AsyncSession) -> PasswordResetRequestResponse:
    dummy_request_id = _make_public_token()
    user = await _find_user_for_reset(payload.email, db)

    now = _safe_now()
    cooldown = max(0, int(settings.password_reset_resend_cooldown_seconds or 60))
    latest_request = await db.scalar(
        select(PasswordResetRequest)
        .where(
            PasswordResetRequest.user_id == user.id,
            PasswordResetRequest.used_at.is_(None),
            PasswordResetRequest.expires_at > now,
        )
        .order_by(PasswordResetRequest.created_at.desc())
        .limit(1)
    )
    if latest_request and cooldown and latest_request.last_sent_at and latest_request.last_sent_at > now - timedelta(seconds=cooldown):
        return PasswordResetRequestResponse(message='Код уже отправлен. Проверьте почту или попробуйте повторно позже.', request_id=latest_request.request_id)

    await db.execute(
        update(PasswordResetRequest)
        .where(PasswordResetRequest.user_id == user.id, PasswordResetRequest.used_at.is_(None))
        .values(used_at=now)
    )

    code = _make_code()
    ttl_minutes = max(1, int(settings.password_reset_code_ttl_minutes or 10))
    request_id = _make_public_token()
    reset_request = PasswordResetRequest(
        user_id=user.id,
        request_id=request_id,
        code_hash=hash_password(code),
        attempts=0,
        expires_at=now + timedelta(minutes=ttl_minutes),
        created_at=now,
        last_sent_at=now,
    )
    db.add(reset_request)
    await db.flush()

    try:
        await send_password_reset_code(user.email, code, ttl_minutes)
    except MailerConfigurationError as exc:
        await db.rollback()
        logger.warning('Восстановление пароля не отправлено: %s', exc)
        return PasswordResetRequestResponse(message='Не удалось отправить письмо. Обратитесь к администратору.', request_id=dummy_request_id)
    except Exception:
        await db.rollback()
        logger.exception('Не удалось отправить письмо восстановления пароля user_id=%s', user.id)
        return PasswordResetRequestResponse(message='Не удалось отправить письмо. Обратитесь к администратору.', request_id=dummy_request_id)

    await db.commit()
    return PasswordResetRequestResponse(message=_RESET_CODE_SENT_MESSAGE, request_id=request_id)


async def verify_password_reset_code(payload: PasswordResetVerifyRequest, db: AsyncSession) -> PasswordResetVerifyResponse:
    now = _safe_now()
    reset_request = await db.scalar(
        select(PasswordResetRequest)
        .where(PasswordResetRequest.request_id == payload.request_id.strip(), PasswordResetRequest.used_at.is_(None))
        .limit(1)
    )
    if not reset_request or reset_request.expires_at <= now:
        raise HTTPException(status_code=400, detail=_INVALID_CODE_MESSAGE)

    max_attempts = max(1, int(settings.password_reset_max_attempts or 5))
    if reset_request.attempts >= max_attempts:
        reset_request.used_at = now
        await db.commit()
        raise HTTPException(status_code=400, detail=_INVALID_CODE_MESSAGE)

    code = re.sub(r'\D+', '', payload.code or '')
    if not verify_password(code, reset_request.code_hash):
        reset_request.attempts += 1
        if reset_request.attempts >= max_attempts:
            reset_request.used_at = now
        await db.commit()
        raise HTTPException(status_code=400, detail=_INVALID_CODE_MESSAGE)

    reset_token = _make_public_token()
    reset_request.verified_at = now
    reset_request.reset_token_hash = hash_password(reset_token)
    await db.commit()
    return PasswordResetVerifyResponse(message='Код подтверждён.', reset_token=reset_token)


async def complete_password_reset(payload: PasswordResetCompleteRequest, db: AsyncSession) -> PasswordResetCompleteResponse:
    password = payload.password or ''
    password_confirm = payload.password_confirm or ''
    if password != password_confirm:
        raise HTTPException(status_code=400, detail='Пароли не совпадают.')
    if len(password) < 6:
        raise HTTPException(status_code=400, detail='Пароль должен быть не короче 6 символов.')

    now = _safe_now()
    token = payload.reset_token.strip()
    candidates = (
        await db.scalars(
            select(PasswordResetRequest)
            .where(
                PasswordResetRequest.reset_token_hash.is_not(None),
                PasswordResetRequest.used_at.is_(None),
                PasswordResetRequest.expires_at > now,
            )
            .order_by(PasswordResetRequest.verified_at.desc())
            .limit(200)
        )
    ).all()

    matched_request: PasswordResetRequest | None = None
    for candidate in candidates:
        if candidate.reset_token_hash and verify_password(token, candidate.reset_token_hash):
            matched_request = candidate
            break

    if not matched_request:
        raise HTTPException(status_code=400, detail='Ссылка восстановления устарела. Запросите новый код.')

    user = await db.get(User, matched_request.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=400, detail='Не удалось сменить пароль.')

    user.password_hash = hash_password(password)
    matched_request.used_at = now
    await db.execute(
        update(PasswordResetRequest)
        .where(
            PasswordResetRequest.user_id == user.id,
            PasswordResetRequest.id != matched_request.id,
            PasswordResetRequest.used_at.is_(None),
        )
        .values(used_at=now)
    )
    await db.commit()
    return PasswordResetCompleteResponse(message='Пароль изменён. Теперь можно войти с новым паролем.')
