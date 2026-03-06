from contextlib import asynccontextmanager
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from pydantic import BaseModel

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


# --- НАША НОВАЯ 3-УРОВНЕВАЯ БАЗА ---
MOCK_DB = [
    {
        "id": "cat_1",
        "name": "🥤 Напитки",
        "subcategories": [
            {
                "id": "sub_1",
                "name": "Энергетики",
                "items": [
                    {"id": "item_1", "name": "Red Bull 0.25", "uom": "шт", "expected": 10},
                    {"id": "item_2", "name": "Adrenaline 0.5", "uom": "шт", "expected": 5}
                ]
            },
            {
                "id": "sub_2",
                "name": "Газировка",
                "items": [
                    {"id": "item_3", "name": "Добрый Кола 1л", "uom": "шт", "expected": 12}
                ]
            }
        ]
    },
    {
        "id": "cat_2",
        "name": "🍫 Снеки",
        "subcategories": [
            {
                "id": "sub_3",
                "name": "Чипсы",
                "items": [
                    {"id": "item_4", "name": "Lays Сыр 90г", "uom": "шт", "expected": 8},
                    {"id": "item_5", "name": "Lays Краб 90г", "uom": "шт", "expected": 7}
                ]
            }
        ]
    }
]

class VerifyRequest(BaseModel):
    target_id: str
    target_type: str  # Теперь тут будет "subcategory" или "item"
    quantity: float
    attempt_number: int

@app.get("/get-structure")
async def get_structure(location: str):
    # Отдаем нашу новую 3-уровневую структуру
    return {"location": location, "categories": MOCK_DB}

@app.post("/verify")
async def verify_count(req: VerifyRequest):
    expected_qty = 0
    found = False

    # Ищем наш товар или подкатегорию в базе
    for cat in MOCK_DB:
        for sub in cat["subcategories"]:
            if req.target_type == "subcategory" and sub["id"] == req.target_id:
                # Если проверяем подкатегорию, считаем сумму всех товаров внутри нее
                expected_qty = sum(item["expected"] for item in sub["items"])
                found = True
                break
            elif req.target_type == "item":
                for item in sub["items"]:
                    if item["id"] == req.target_id:
                        expected_qty = item["expected"]
                        found = True
                        break

    if not found:
        return {"is_correct": False, "message": "Товар не найден", "expand_category": False}

    is_correct = (req.quantity == expected_qty)

    if is_correct:
        return {"is_correct": True, "message": "✓ Сошлось!"}
    else:
        attempts_left = 3 - req.attempt_number
        if attempts_left > 0:
            return {
                "is_correct": False, 
                "message": f"Неверно. Осталось попыток: {attempts_left}", 
                "expand_category": False
            }
        else:
            return {
                "is_correct": False, 
                "message": "Не сошлось.", 
                "expand_category": True if req.target_type == "subcategory" else False,
                "attempts_left": 0
            }

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