from __future__ import annotations

import logging
from email.message import EmailMessage

import aiosmtplib

from app.config import settings

logger = logging.getLogger(__name__)


class MailerConfigurationError(RuntimeError):
    pass


def smtp_is_configured() -> bool:
    return bool(settings.smtp_host and settings.smtp_from_email)


async def send_email_message(*, to_email: str, subject: str, text: str) -> None:
    if not smtp_is_configured():
        raise MailerConfigurationError('SMTP не настроен: заполните SMTP_HOST и SMTP_FROM_EMAIL.')

    message = EmailMessage()
    from_name = (settings.smtp_from_name or 'UCHETKA').strip() or 'UCHETKA'
    message['From'] = f'{from_name} <{settings.smtp_from_email}>'
    message['To'] = to_email
    message['Subject'] = subject
    message.set_content(text)

    username = settings.smtp_username or None
    password = settings.smtp_password or None
    use_auth = bool(username and password)

    await aiosmtplib.send(
        message,
        hostname=settings.smtp_host,
        port=int(settings.smtp_port or 587),
        username=username if use_auth else None,
        password=password if use_auth else None,
        start_tls=bool(settings.smtp_starttls),
    )


async def send_password_reset_code(email_to: str, code: str, ttl_minutes: int) -> None:
    ttl = max(1, int(ttl_minutes or 10))
    await send_email_message(
        to_email=email_to,
        subject='Код восстановления пароля UCHETKA',
        text=(
            f'Ваш код восстановления пароля: {code}\n\n'
            f'Код действует {ttl} минут.\n'
            'Если вы не запрашивали восстановление пароля, просто проигнорируйте это письмо.'
        ),
    )
