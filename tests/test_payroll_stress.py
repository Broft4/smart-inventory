from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.payroll as payroll
from app.models import (
    EmployeeBonusEntry,
    LocationPoint,
    PayrollSettingsVersion,
    ShiftPayrollSnapshot,
    User,
    WorkShift,
)
from app.payroll import EmployeeBonusCreateRequest, create_employee_bonus, list_work_shifts

pytestmark = [
    pytest.mark.stress,
    pytest.mark.skipif(os.getenv("RUN_STRESS") != "1", reason="Нагрузочные тесты запускаются вручную: RUN_STRESS=1 pytest -m stress"),
]


async def _seed_load_dataset(session: AsyncSession, employee_count: int = 30) -> tuple[User, LocationPoint, list[User]]:
    point = LocationPoint(name="Дубна")
    session.add(point)
    await session.flush()
    admin = User(
        full_name="Главный управляющий",
        birth_date=date(1990, 1, 1),
        username="stress_admin",
        password_hash="test-hash",
        role="superadmin",
        location=None,
        is_active=True,
    )
    session.add(admin)
    employees = []
    for index in range(employee_count):
        employee = User(
            full_name=f"Сотрудник нагрузки {index:02d}",
            birth_date=date(1990, 1, 1),
            username=f"stress_employee_{index:02d}",
            password_hash="test-hash",
            role="employee",
            location=point.name,
            is_active=True,
        )
        session.add(employee)
        employees.append(employee)
    await session.flush()
    session.add(
        PayrollSettingsVersion(
            location_point_id=point.id,
            effective_from=date(2026, 1, 1),
            exit_amount=1000.0,
            bonus_threshold=100000.0,
            bonus_amount=0.0,
            other_rate_percent=0.0,
            bonus_category_ids_json='[]',
            manager_salary_brackets_json='[]',
        )
    )
    await session.commit()
    return admin, point, employees


async def test_concurrent_bonus_writes_and_shift_autoclose_load(
    monkeypatch,
    db_session_factory: async_sessionmaker[AsyncSession],
):
    async with db_session_factory() as session:
        admin, point, employees = await _seed_load_dataset(session)
        admin_id = admin.id
        point_id = point.id
        employee_ids = [employee.id for employee in employees]

    today = date(2026, 5, 11)
    monkeypatch.setattr(payroll, "get_moscow_today", lambda: today)

    async def fake_metrics(point, date_from, date_to, db=None, *, force_refresh=False):
        days = {}
        current = date_from
        while current <= date_to:
            days[current] = {
                "categories": [],
                "gross_sales_amount": 0.0,
                "return_amount": 0.0,
                "net_sales_amount": 0.0,
                "cost_amount": 0.0,
                "gross_profit_amount": 0.0,
                "non_tobacco_net_sales_for_bonus": 0.0,
            }
            current += timedelta(days=1)
        return days

    monkeypatch.setattr(payroll, "_load_point_sales_metrics", fake_metrics)

    async def write_bonus(employee_id: int, amount: float) -> None:
        async with db_session_factory() as session:
            admin = await session.get(User, admin_id)
            await create_employee_bonus(
                EmployeeBonusCreateRequest(
                    location="Дубна",
                    month_start=date(2026, 5, 1),
                    employee_user_id=employee_id,
                    amount=amount,
                    bonus_date=date(2026, 5, 5),
                    comment="stress",
                ),
                session,
                admin,
            )

    await asyncio.gather(*[write_bonus(employee_id, 100 + index) for index, employee_id in enumerate(employee_ids)])

    async with db_session_factory() as session:
        session.add_all(
            WorkShift(
                location_point_id=point_id,
                shift_date=today - timedelta(days=(index % 5) + 1),
                employee_user_id=employee_id,
                status="planned",
                created_by_user_id=admin_id,
                updated_at=datetime.utcnow(),
            )
            for index, employee_id in enumerate(employee_ids)
        )
        await session.commit()

    async def list_shifts_once() -> None:
        async with db_session_factory() as session:
            admin = await session.get(User, admin_id)
            await list_work_shifts("Дубна", today - timedelta(days=7), today, session, admin)

    # Несколько параллельных чтений должны привести к закрытию прошедших смен без потери записей.
    await asyncio.gather(*[list_shifts_once() for _ in range(4)])

    async with db_session_factory() as session:
        bonus_count = await session.scalar(select(func.count()).select_from(EmployeeBonusEntry))
        closed_shift_count = await session.scalar(
            select(func.count()).select_from(WorkShift).where(WorkShift.status == "closed")
        )
        snapshot_count = await session.scalar(select(func.count()).select_from(ShiftPayrollSnapshot))

    assert bonus_count == len(employee_ids)
    assert closed_shift_count == len(employee_ids)
    assert snapshot_count == len(employee_ids)
