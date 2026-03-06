from pydantic import BaseModel, Field
from typing import List
from enum import Enum

class StatusEnum(str, Enum):
    GREY = "grey"       # Не проверено
    GREEN = "green"     # Сошлось
    ORANGE = "orange"   # Идет проверка внутри категории
    RED = "red"         # Расхождение зафиксировано (после 3 попыток)

class ItemModel(BaseModel):
    id: str
    name: str
    uom: str = "шт"
    status: StatusEnum = StatusEnum.GREY

class CategoryModel(BaseModel):
    id: str
    name: str
    status: StatusEnum = StatusEnum.GREY
    items: List[ItemModel] = []

class InventoryStructureResponse(BaseModel):
    location: str
    store_id: str
    categories: List[CategoryModel]

# Схемы для проверки попыток
class VerifyRequest(BaseModel):
    target_id: str
    is_category: bool
    quantity: float
    attempt_number: int

class VerifyResponse(BaseModel):
    is_correct: bool
    attempts_left: int
    message: str
    expand_category: bool = False

    # ... твои старые схемы ...

class DiscrepancyItem(BaseModel):
    """Модель для проблемного товара"""
    name: str
    expected: float  # Сколько должно быть
    actual: float    # Сколько по факту
    diff: float      # Разница (плюс или минус)

class CategoryResult(BaseModel):
    """Результат по одной категории"""
    name: str
    status: StatusEnum
    problem_items: List[DiscrepancyItem] = []

class AdminReport(BaseModel):
    """Общий отчет по инвентаризации"""
    date: str
    location: str
    categories: List[CategoryResult]
    total_plus: float   # Сумма излишков
    total_minus: float  # Сумма недостач