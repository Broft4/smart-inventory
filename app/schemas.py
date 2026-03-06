from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class StatusEnum(str, Enum):
    GREY = "grey"
    GREEN = "green"
    ORANGE = "orange"
    RED = "red"


class ItemModel(BaseModel):
    id: str = Field(..., description="ID товара")
    name: str = Field(..., description="Название товара")
    uom: str = Field(default="шт", description="Единица измерения")
    status: StatusEnum = Field(default=StatusEnum.GREY)
    entered_quantity: float | None = Field(default=None)


class SubcategoryModel(BaseModel):
    id: str
    name: str
    status: StatusEnum = Field(default=StatusEnum.GREY)
    is_locked: bool = Field(default=True)
    is_completed: bool = Field(default=False)
    is_expanded: bool = Field(default=False)
    items: list[ItemModel] = Field(default_factory=list)
    entered_quantity: float | None = Field(default=None)


class CategoryModel(BaseModel):
    id: str
    name: str
    status: StatusEnum = Field(default=StatusEnum.GREY)
    subcategories: list[SubcategoryModel] = Field(default_factory=list)


class InventoryStructureResponse(BaseModel):
    location: str
    store_id: str
    report_id: int
    categories: list[CategoryModel]


class VerifyRequest(BaseModel):
    report_id: int
    target_id: str
    target_type: Literal["subcategory", "item"]
    quantity: float


class VerifyResponse(BaseModel):
    is_correct: bool
    attempts_left: int
    message: str
    expand_items: bool = False
    subcategory_completed: bool = False
    target_status: StatusEnum


class FinishReportRequest(BaseModel):
    report_id: int


class FinishReportResponse(BaseModel):
    success: bool
    message: str


class DiscrepancyItem(BaseModel):
    name: str
    expected: float
    actual: float
    diff: float


class CategoryResult(BaseModel):
    name: str
    status: StatusEnum
    problem_items: list[DiscrepancyItem] = Field(default_factory=list)


class AdminReport(BaseModel):
    date: str
    location: str
    categories: list[CategoryResult]
    total_plus: float
    total_minus: float
