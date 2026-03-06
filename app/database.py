from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base

# Путь к нашему файлу базы данных (он появится прямо в папке проекта)
SQLALCHEMY_DATABASE_URL = "sqlite+aiosqlite:///./inventory.db"

# Создаем асинхронный "движок" для общения с БД. 
# echo=True будет выводить SQL-запросы в терминал (очень полезно для отладки!)
engine = create_async_engine(SQLALCHEMY_DATABASE_URL, echo=True)

# Фабрика сессий (каждый раз, когда нам нужно записать данные, мы берем сессию отсюда)
async_session_maker = async_sessionmaker(engine, expire_on_commit=False)

# Базовый класс, от которого мы будем наследовать все наши таблицы
Base = declarative_base()

# Эта функция понадобится нам позже, чтобы передавать БД прямо в эндпоинты FastAPI
async def get_db():
    async with async_session_maker() as session:
        yield session