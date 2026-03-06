from contextlib import asynccontextmanager
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db

# Импортируем нашу базу данных и модели
from app.database import engine, Base
import app.models  # Важно импортировать файл с моделями, чтобы SQLAlchemy о них узнала

# Импортируем схемы и логику
from app.schemas import InventoryStructureResponse, VerifyRequest, VerifyResponse, AdminReport
from app.logic import get_inventory_data, verify_item_or_category, get_admin_report

# Описываем, что делать при старте сервера
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Подключаемся к базе и создаем таблицы, если их еще нет
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield  # Сервер работает
    # Здесь можно написать код для закрытия соединений при выключении сервера

# Передаем нашу функцию lifespan в приложение
app = FastAPI(title="Умная Ревизия", lifespan=lifespan)

# Подключаем папку со статикой (CSS/JS)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Подключаем папку с HTML-шаблонами
templates = Jinja2Templates(directory="templates")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Вот здесь мы теперь отдаем HTML-страницу!
@app.get("/")
async def root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/get-structure", response_model=InventoryStructureResponse)
async def get_structure(location: str = Query(..., description="Город (Дмитров или Дубна)")):
    data = await get_inventory_data(location)
    return data

@app.post("/verify", response_model=VerifyResponse)
async def verify_quantity(data: VerifyRequest, db: AsyncSession = Depends(get_db)):
    """
    Эндпоинт для проверки 3-х попыток.
    Теперь он получает сессию БД (db) и передает её в логику.
    """
    result = await verify_item_or_category(data, db)
    return result

# Не забудь добавить новые импорты в начале файла!
from app.schemas import InventoryStructureResponse, VerifyRequest, VerifyResponse, AdminReport
from app.logic import get_inventory_data, verify_item_or_category, get_admin_report

# --- РОУТЫ ДЛЯ АДМИНКИ ---

@app.get("/admin")
async def admin_page(request: Request):
    """Отдает HTML-страницу панели администратора"""
    return templates.TemplateResponse("admin.html", {"request": request})

@app.get("/api/report", response_model=AdminReport)
async def api_get_report(location: str = "Дубна", db: AsyncSession = Depends(get_db)):
    """Отдает данные отчета в формате JSON из базы данных"""
    report = await get_admin_report(location, db)
    return report