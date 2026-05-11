from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.payroll as payroll
from app.models import (
    EmployeeBonusEntry,
    LocationPoint,
    PayrollCategoryRateVersion,
    PayrollSettingsVersion,
    ShiftPayrollSnapshot,
    User,
    WorkShift,
)
from app.payroll import (
    EmployeeBonusCreateRequest,
    WorkShiftUpsertRequest,
    create_employee_bonus,
    get_location_payroll_setup,
    list_employee_bonuses,
    list_work_shifts,
    upsert_work_shift,
)


async def _create_location(session: AsyncSession, name: str) -> LocationPoint:
    point = LocationPoint(name=name)
    session.add(point)
    await session.flush()
    return point


async def _create_user(
    session: AsyncSession,
    *,
    full_name: str,
    username: str,
    role: str,
    location: str | None = None,
) -> User:
    user = User(
        full_name=full_name,
        birth_date=date(1990, 1, 1),
        username=username,
        password_hash="test-hash",
        role=role,
        location=location,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_two_locations(session: AsyncSession) -> dict[str, object]:
    dmitrov = await _create_location(session, "Дмитров")
    dubna = await _create_location(session, "Дубна")
    admin = await _create_user(session, full_name="Главный управляющий", username="super", role="superadmin")
    dmitrov_employee = await _create_user(
        session,
        full_name="Сотрудник Дмитрова",
        username="dmitrov_employee",
        role="employee",
        location=dmitrov.name,
    )
    dubna_employee = await _create_user(
        session,
        full_name="Сотрудник Дубны",
        username="dubna_employee",
        role="employee",
        location=dubna.name,
    )
    await session.commit()
    return {
        "admin": admin,
        "dmitrov": dmitrov,
        "dubna": dubna,
        "dmitrov_employee": dmitrov_employee,
        "dubna_employee": dubna_employee,
    }


async def _create_basic_payroll_settings(session: AsyncSession, point: LocationPoint) -> PayrollSettingsVersion:
    settings = PayrollSettingsVersion(
        location_point_id=point.id,
        effective_from=date(2026, 1, 1),
        exit_amount=1000.0,
        bonus_threshold=500.0,
        bonus_amount=100.0,
        other_rate_percent=10.0,
        bonus_category_ids_json='[]',
        manager_salary_brackets_json='[]',
    )
    session.add(settings)
    await session.flush()
    session.add(
        PayrollCategoryRateVersion(
            settings_version_id=settings.id,
            category_id="cat-accessories",
            category_name="Аксессуары",
            rate_percent=10.0,
        )
    )
    await session.commit()
    return settings


@pytest.mark.asyncio
async def test_payroll_setup_returns_only_selected_location_employees(db_session: AsyncSession):
    data = await _seed_two_locations(db_session)

    setup = await get_location_payroll_setup("Дубна", db_session, data["admin"])

    employee_names = {item["full_name"] for item in setup["employees"]}
    assert employee_names == {"Сотрудник Дубны"}
    assert "Сотрудник Дмитрова" not in employee_names


@pytest.mark.asyncio
async def test_employee_bonus_rejects_employee_from_other_location_without_saving(db_session: AsyncSession):
    data = await _seed_two_locations(db_session)

    with pytest.raises(HTTPException) as exc_info:
        await create_employee_bonus(
            EmployeeBonusCreateRequest(
                location="Дубна",
                month_start=date(2026, 5, 1),
                employee_user_id=data["dmitrov_employee"].id,
                amount=1000.0,
                bonus_date=date(2026, 5, 10),
                comment="Не должна сохраниться",
            ),
            db_session,
            data["admin"],
        )

    assert exc_info.value.status_code == 400
    count = await db_session.scalar(select(func.count()).select_from(EmployeeBonusEntry))
    assert count == 0


@pytest.mark.asyncio
async def test_employee_bonus_list_is_scoped_to_selected_location(db_session: AsyncSession):
    data = await _seed_two_locations(db_session)
    await create_employee_bonus(
        EmployeeBonusCreateRequest(
            location="Дмитров",
            month_start=date(2026, 5, 1),
            employee_user_id=data["dmitrov_employee"].id,
            amount=500.0,
            bonus_date=date(2026, 5, 7),
            comment="Дмитровская премия",
        ),
        db_session,
        data["admin"],
    )
    await create_employee_bonus(
        EmployeeBonusCreateRequest(
            location="Дубна",
            month_start=date(2026, 5, 1),
            employee_user_id=data["dubna_employee"].id,
            amount=700.0,
            bonus_date=date(2026, 5, 8),
            comment="Дубненская премия",
        ),
        db_session,
        data["admin"],
    )

    payload = await list_employee_bonuses("Дубна", date(2026, 5, 1), db_session, data["admin"])

    assert payload["location"] == "Дубна"
    assert len(payload["entries"]) == 1
    assert payload["entries"][0]["employee_name"] == "Сотрудник Дубны"
    assert payload["entries"][0]["comment"] == "Дубненская премия"


@pytest.mark.asyncio
async def test_past_shift_is_auto_closed_when_saved(monkeypatch, db_session: AsyncSession):
    data = await _seed_two_locations(db_session)
    await _create_basic_payroll_settings(db_session, data["dubna"])
    today = date(2026, 5, 11)
    shift_date = today - timedelta(days=1)

    monkeypatch.setattr(payroll, "get_moscow_today", lambda: today)

    async def fake_metrics(point, date_from, date_to, db=None, *, force_refresh=False):
        return {
            shift_date: {
                "categories": [
                    {
                        "category_id": "cat-accessories",
                        "category_name": "Аксессуары",
                        "sales_amount": 1000.0,
                        "return_amount": 0.0,
                        "net_sales_amount": 1000.0,
                        "cost_amount": 400.0,
                    }
                ],
                "gross_sales_amount": 1000.0,
                "return_amount": 0.0,
                "net_sales_amount": 1000.0,
                "cost_amount": 400.0,
                "gross_profit_amount": 600.0,
                "non_tobacco_net_sales_for_bonus": 1000.0,
            }
        }

    monkeypatch.setattr(payroll, "_load_point_sales_metrics", fake_metrics)

    await upsert_work_shift(
        WorkShiftUpsertRequest(
            location="Дубна",
            shift_date=shift_date,
            employee_user_id=data["dubna_employee"].id,
        ),
        db_session,
        data["admin"],
    )

    shift = await db_session.scalar(select(WorkShift).where(WorkShift.shift_date == shift_date))
    assert shift is not None
    assert shift.status == "closed"
    assert shift.closed_by_user_id is None
    snapshot = await db_session.scalar(select(ShiftPayrollSnapshot).where(ShiftPayrollSnapshot.shift_id == shift.id))
    assert snapshot is not None
    assert snapshot.is_auto_closed is True
    assert snapshot.gross_salary_amount == 1200.0  # выход 1000 + премия 100 + 10% от 1000


@pytest.mark.asyncio
async def test_shift_calendar_listing_auto_closes_past_open_shift(monkeypatch, db_session: AsyncSession):
    data = await _seed_two_locations(db_session)
    await _create_basic_payroll_settings(db_session, data["dubna"])
    today = date(2026, 5, 11)
    shift_date = today - timedelta(days=2)

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

    shift = WorkShift(
        location_point_id=data["dubna"].id,
        shift_date=shift_date,
        employee_user_id=data["dubna_employee"].id,
        status="planned",
        created_by_user_id=data["admin"].id,
        updated_at=datetime.utcnow(),
    )
    db_session.add(shift)
    await db_session.commit()

    payload = await list_work_shifts("Дубна", shift_date, today, db_session, data["admin"])

    await db_session.refresh(shift)
    assert shift.status == "closed"
    assert payload["days"][0]["shifts"][0]["is_closed"] is True
    snapshot_count = await db_session.scalar(select(func.count()).select_from(ShiftPayrollSnapshot))
    assert snapshot_count == 1
