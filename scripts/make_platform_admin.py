from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import User

PLATFORM_ADMIN_ROLE = 'platform_admin'


async def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        print('Использование: python scripts/make_platform_admin.py <username>')
        return 2

    username = sys.argv[1].strip()
    async with AsyncSessionLocal() as db:
        user = await db.scalar(select(User).where(User.username == username).limit(1))
        if not user:
            print(f'Пользователь с логином {username!r} не найден.')
            return 1
        old_role = user.role
        user.role = PLATFORM_ADMIN_ROLE
        user.location = None
        user.is_active = True
        await db.commit()
        print(f'Готово: {user.full_name} ({user.username}) {old_role!r} -> {PLATFORM_ADMIN_ROLE!r}.')
        return 0


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
