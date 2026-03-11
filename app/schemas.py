from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class StatusEnum(str, Enum):
    GREY = 'grey'
    GREEN = 'green'
    ORANGE = 'orange'
    RED = 'red'


class RoleEnum(str, Enum):
    ADMIN = 'admin'
    EMPLOYEE = 'employee'


class UserInfo(BaseModel):
    id: int
    full_name: str
    birth_date: date
    username: str
    role: RoleEnum
    location: Optional[str] = None
    is_active: bool


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    success: bool
    message: str
    user: Optional[UserInfo] = None
    redirect_to: str


class LogoutResponse(BaseModel):
    success: bool = True
    message: str = 'Вы вышли из системы.'


class MeResponse(BaseModel):
    authenticated: bool
    user: Optional[UserInfo] = None


class UserCreateRequest(BaseModel):
    full_name: str = Field(..., min_length=3, max_length=255)
    birth_date: date
    username: str = Field(..., min_length=3, max_length=100)
    password: str
    role: RoleEnum = RoleEnum.EMPLOYEE
    location: Optional[str] = None
    is_active: bool = True


class UserUpdateRequest(BaseModel):
    full_name: str = Field(..., min_length=3, max_length=255)
    birth_date: date
    username: str = Field(..., min_length=3, max_length=100)
    password: Optional[str] = None
    role: RoleEnum = RoleEnum.EMPLOYEE
    location: Optional[str] = None
    is_active: bool = True


class UserResponse(BaseModel):
    id: int
    full_name: str
    birth_date: date
    username: str
    role: RoleEnum
    location: Optional[str] = None
    is_active: bool

    model_config = {'from_attributes': True}


class UserListResponse(BaseModel):
    users: list[UserResponse]


class UserActionResponse(BaseModel):
    success: bool
    message: str
    user: Optional[UserResponse] = None


class DeleteResponse(BaseModel):
    success: bool
    message: str


class ItemModel(BaseModel):
    id: str
    name: str
    uom: str = 'шт'
    status: StatusEnum = StatusEnum.GREY
    is_final: bool = False
    assigned_to: Optional[str] = None
    assigned_to_current_user: bool = False
    can_take: bool = False
    is_blocked_by_other: bool = False
    is_diagnostic: bool = False


class SubcategoryModel(BaseModel):
    id: str
    name: str
    is_locked: bool = False
    is_completed: bool = False
    is_expanded: bool = False
    status: StatusEnum = StatusEnum.GREY
    items: list[ItemModel] = Field(default_factory=list)
    assigned_to: Optional[str] = None
    assigned_to_current_user: bool = False
    can_take: bool = False
    is_blocked_by_other: bool = False
    taken_as_part_of_category: bool = False
    is_diagnostic: bool = False
    has_my_items: bool = False
    has_other_items: bool = False


class CategoryModel(BaseModel):
    id: str
    name: str
    is_available: bool = True
    is_completed: bool = False
    is_open: bool = False
    subcategories: list[SubcategoryModel] = Field(default_factory=list)
    assigned_to: Optional[str] = None
    assigned_to_current_user: bool = False
    can_take: bool = False
    is_blocked_by_other: bool = False
    has_my_subcategories: bool = False
    has_other_subcategories: bool = False
    mixed_assignment: bool = False
    is_diagnostic: bool = False
    has_my_items: bool = False
    has_other_items: bool = False


class InventoryStructureResponse(BaseModel):
    report_id: int
    location: str
    report_date: str
    categories: list[CategoryModel]
    cycle_version: int
    cycle_started_at: str
    cycle_days_left: int


class AssignSelectionRequest(BaseModel):
    report_id: int
    category_id: str
    target_type: str = 'category'
    subcategory_id: Optional[str] = None
    item_id: Optional[str] = None


class AssignSelectionResponse(BaseModel):
    success: bool
    message: str


class ResetSelectionCycleResponse(BaseModel):
    success: bool
    message: str
    cycle_version: int
    cycle_started_at: str


class VerifyRequest(BaseModel):
    report_id: int
    target_id: str
    is_category: bool
    quantity: float
    attempt_number: int


class VerifyResponse(BaseModel):
    is_correct: bool
    attempts_left: int
    message: str
    expand_category: bool = False


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
    checked_by: Optional[str] = None


class CategoryResult(BaseModel):
    name: str
    status: StatusEnum
    assigned_to: Optional[str] = None
    problem_items: list[DiscrepancyItem] = Field(default_factory=list)


class EmployeeReportSummary(BaseModel):
    full_name: str
    categories: list[str] = Field(default_factory=list)
    completed_categories: int = 0
    discrepancy_items: int = 0


class AdminReport(BaseModel):
    report_id: Optional[int] = None
    date: str
    location: str
    status: str
    categories: list[CategoryResult]
    total_plus: float
    total_minus: float
    employees: list[EmployeeReportSummary] = Field(default_factory=list)


class ReportHistoryItem(BaseModel):
    report_id: int
    date: str
    status: str
    label: str


class ReportHistoryResponse(BaseModel):
    location: str
    reports: list[ReportHistoryItem]
