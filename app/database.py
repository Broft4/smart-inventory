from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import event
from sqlalchemy.orm import declarative_base

from app.config import settings


Base = declarative_base()
engine_kwargs = {'echo': False}
if settings.database_url.startswith('sqlite'):
    engine_kwargs['connect_args'] = {'timeout': 30}
engine = create_async_engine(settings.database_url, **engine_kwargs)

if settings.database_url.startswith('sqlite'):
    @event.listens_for(engine.sync_engine, 'connect')
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute('PRAGMA journal_mode=WAL')
            cursor.execute('PRAGMA synchronous=NORMAL')
            cursor.execute('PRAGMA busy_timeout=30000')
            cursor.execute('PRAGMA foreign_keys=ON')
        finally:
            cursor.close()
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
