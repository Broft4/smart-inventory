from __future__ import annotations

import asyncio
import csv
import io
import logging
from datetime import date

from contextlib import asynccontextmanager
from urllib.parse import quote
from pathlib import Path
from logging.handlers import RotatingFileHandler
from time import monotonic
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.database import engine, get_db
from app.logic import (
    assign_selection_to_user,
    authenticate_user,
    bootstrap_schema_and_admin,
    create_location_point,
    create_user,
    delete_location_point,
    delete_report,
    delete_user,
    ensure_default_admin,
    build_admin_report_excel,
    ensure_user_can_access_location,
    finish_report,
    get_admin_report,
    get_admin_period_report,
    reopen_employee_report_access,
    get_cycle_targets,
    get_inventory_data,
    get_user_accessible_locations,
    get_inventory_diagnostics_rows,
    get_me_response,
    get_reports_history,
    list_locations,
    prewarm_inventory_cache,
    list_moysklad_stores_by_token,
    list_users,
    save_cycle_targets,
    start_report,
    update_discrepancy_actual_qty,
    update_discrepancy_cost_override,
    update_location_point,
    update_user,
    user_to_schema,
    verify_item_or_category,
)
from app.models import Report, User
from app.moysklad import ms_client
from app.payroll import (
    EmployeeBonusCreateRequest,
    EmployeeBonusUpdateRequest,
    ExpenseTemplateCreateRequest,
    ExpenseTemplateUpdateRequest,
    ManualMonthlyExpenseCreateRequest,
    MonthlyExpenseEntryUpdateRequest,
    PayrollSettingsUpdateRequest,
    WorkShiftUpsertRequest,
    close_shift,
    bootstrap_payroll_schema,
    create_employee_bonus,
    create_expense_template,
    create_manual_monthly_expense,
    deactivate_expense_template,
    delete_employee_bonus,
    delete_expense_template,
    delete_monthly_expense_entry,
    delete_work_shift,
    export_employee_payroll_xlsx,
    get_employee_payroll_summary,
    get_location_payroll_setup,
    get_location_shift_setup,
    get_manager_payroll_summary,
    get_payroll_category_catalog,
    get_payroll_recalc_status,
    get_user_accessible_locations as get_payroll_accessible_locations,
    list_employee_bonuses,
    list_expense_templates,
    list_monthly_expenses,
    list_payroll_audit_logs,
    list_work_shift_day_summary,
    list_work_shifts,
    update_employee_bonus,
    update_expense_template,
    update_location_payroll_settings,
    update_monthly_expense_entry,
    upsert_work_shift,
    resume_pending_payroll_recalc_jobs,
)
from app.schemas import (
    AdminCycleTargetsResponse,
    AdminReport,
    AssignSelectionRequest,
    AssignSelectionResponse,
    CreateLocationRequest,
    CreateLocationResponse,
    DeleteResponse,
    FinishReportRequest,
    FinishReportResponse,
    InventoryStructureResponse,
    LocationListResponse,
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    MeResponse,
    ReopenEmployeeAccessRequest,
    ReopenEmployeeAccessResponse,
    ReportHistoryResponse,
    SaveCycleTargetsRequest,
    SaveCycleTargetsResponse,
    StartReportRequest,
    StartReportResponse,
    StoreListResponse,
    UpdateDiscrepancyCostOverrideRequest,
    UpdateDiscrepancyCostOverrideResponse,
    UpdateDiscrepancyRequest,
    UpdateDiscrepancyResponse,
    UpdateLocationRequest,
    UpdateLocationResponse,
    UserActionResponse,
    UserCreateRequest,
    UserListResponse,
    UserUpdateRequest,
    VerifyRequest,
    VerifyResponse,
)

BASE_DIR = Path(__file__).resolve().parents[1]


def configure_logging() -> None:
    level_name = (settings.app_log_level or 'INFO').upper()
    level = getattr(logging, level_name, logging.INFO)
    formatter = logging.Formatter('%(asctime)s %(levelname)s [%(name)s] %(message)s')

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    log_path = BASE_DIR / 'logs' / 'smart_inventory.log'
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3, encoding='utf-8')
        handlers.append(file_handler)
    except Exception:
        pass

    for handler in handlers:
        handler.setFormatter(formatter)

    logging.basicConfig(level=level, handlers=handlers, force=True)


configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(bootstrap_schema_and_admin)
        await conn.run_sync(bootstrap_payroll_schema)
    async with AsyncSession(bind=engine, expire_on_commit=False) as session:
        await ensure_default_admin(session)
    await resume_pending_payroll_recalc_jobs()
    try:
        yield
    finally:
        await ms_client.aclose()


app = FastAPI(title='Умная ревизия', lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret_key)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

app.mount('/static', StaticFiles(directory=BASE_DIR / 'static'), name='static')
templates = Jinja2Templates(directory=str(BASE_DIR / 'templates'))
templates.env.globals['asset_version'] = '20260501-payroll-bonuses-v1'


@app.middleware('http')
async def request_id_middleware(request: Request, call_next):
    request_id = uuid4().hex[:10]
    request.state.request_id = request_id
    try:
        response = await call_next(request)
    except Exception:
        logger.exception('[req:%s] %s %s unhandled server error', request_id, request.method, request.url.path)
        raise
    response.headers['X-Request-ID'] = request_id
    return response




def _request_id(request: Request) -> str:
    return getattr(request.state, 'request_id', '-')


def _duration_ms(started: float) -> float:
    return round((monotonic() - started) * 1000, 1)


def _spawn_prewarm(location: str | None) -> None:
    if not location:
        return

    async def runner() -> None:
        try:
            await prewarm_inventory_cache(location)
        except Exception:
            pass

    asyncio.create_task(runner())


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User | None:
    user_id = request.session.get('user_id')
    if not user_id:
        return None
    user = await db.get(User, int(user_id))
    if not user or not user.is_active:
        request.session.clear()
        return None
    return user


async def require_user(user: User | None = Depends(get_current_user)) -> User:
    if not user:
        raise HTTPException(status_code=401, detail='Требуется вход в систему.')
    return user


async def require_admin_or_superadmin(user: User = Depends(require_user)) -> User:
    if user.role not in {'admin', 'superadmin'}:
        raise HTTPException(status_code=403, detail='Доступ только для управляющего.')
    return user


async def require_superadmin(user: User = Depends(require_user)) -> User:
    if user.role != 'superadmin':
        raise HTTPException(status_code=403, detail='Доступ только для главного управляющего.')
    return user


@app.get('/login')
async def login_page(request: Request, user: User | None = Depends(get_current_user)):
    if user:
        return RedirectResponse(url='/admin' if user.role in {'admin', 'superadmin'} else '/', status_code=302)
    return templates.TemplateResponse(request, 'login.html', {})


@app.get('/')
async def inventory_page(request: Request, user: User | None = Depends(get_current_user)):
    if not user:
        return RedirectResponse(url='/login', status_code=302)
    if user.role in {'admin', 'superadmin'}:
        return RedirectResponse(url='/admin', status_code=302)
    _spawn_prewarm(user.location)
    return templates.TemplateResponse(
        request,
        'index.html',
        {'user': user, 'no_location_assigned': not bool(user.location)},
    )


@app.get('/admin')
async def admin_page(request: Request, user: User | None = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if not user:
        return RedirectResponse(url='/login', status_code=302)
    accessible_locations = await get_user_accessible_locations(user, db)
    if accessible_locations:
        _spawn_prewarm(accessible_locations[0])
    return templates.TemplateResponse(
        request,
        'admin.html',
        {'user': user},
    )

@app.get('/payroll')
async def payroll_page(request: Request, user: User | None = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if not user:
        return RedirectResponse(url='/login', status_code=302)
    if user.role == 'employee' and request.query_params.get('view') == 'shifts':
        return RedirectResponse(url='/shifts', status_code=302)
    accessible_locations = await get_payroll_accessible_locations(user, db)
    location = accessible_locations[0] if accessible_locations else (user.location if user.location else None)
    return templates.TemplateResponse(
        request,
        'payroll.html',
        {
            'user': user,
            'default_location': location,
            'accessible_locations': accessible_locations,
            'today_iso': date.today().isoformat(),
            'current_year': date.today().year,
            'current_month': f"{date.today().month:02d}",
        },
    )


@app.get('/shifts')
async def shifts_page(request: Request, user: User | None = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if not user:
        return RedirectResponse(url='/login', status_code=302)
    accessible_locations = await get_payroll_accessible_locations(user, db)
    location = accessible_locations[0] if accessible_locations else (user.location if user.location else None)
    return templates.TemplateResponse(
        request,
        'shifts.html',
        {
            'user': user,
            'default_location': location,
            'accessible_locations': accessible_locations,
            'today_iso': date.today().isoformat(),
            'current_year': date.today().year,
            'current_month': f"{date.today().month:02d}",
        },
    )


@app.post('/api/login', response_model=LoginResponse)
async def api_login(payload: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    user = await authenticate_user(payload.username.strip(), payload.password, db)
    if not user:
        raise HTTPException(status_code=401, detail='Неверный логин или пароль.')
    request.session['user_id'] = user.id
    _spawn_prewarm(user.location)
    return LoginResponse(
        success=True,
        message='Вход выполнен.',
        user=user_to_schema(user),
        redirect_to='/admin' if user.role in {'admin', 'superadmin'} else '/',
    )


@app.post('/api/logout', response_model=LogoutResponse)
async def api_logout(request: Request):
    request.session.clear()
    return LogoutResponse()


@app.get('/logout')
async def logout_page(request: Request):
    request.session.clear()
    return RedirectResponse(url='/login', status_code=302)


@app.get('/api/me', response_model=MeResponse)
async def api_me(user: User | None = Depends(get_current_user)):
    return await get_me_response(user)


@app.get('/api/locations', response_model=LocationListResponse)
async def api_list_locations(admin: User = Depends(require_admin_or_superadmin), db: AsyncSession = Depends(get_db)):
    response = await list_locations(db, admin)
    if response.locations:
        _spawn_prewarm(response.locations[0].name)
    return response


@app.post('/api/locations/stores', response_model=StoreListResponse)
async def api_list_location_stores(payload: dict, admin: User = Depends(require_superadmin)):
    token = str(payload.get('ms_token') or '').strip()
    if not token:
        raise HTTPException(status_code=400, detail='Нужно передать токен МойСклад.')
    return await list_moysklad_stores_by_token(token)


@app.post('/api/locations', response_model=CreateLocationResponse)
async def api_create_location(payload: CreateLocationRequest, admin: User = Depends(require_superadmin), db: AsyncSession = Depends(get_db)):
    return await create_location_point(payload, db)


@app.patch('/api/locations/{location_id}', response_model=UpdateLocationResponse)
async def api_update_location(location_id: int, payload: UpdateLocationRequest, admin: User = Depends(require_superadmin), db: AsyncSession = Depends(get_db)):
    return await update_location_point(location_id, payload, db)


@app.delete('/api/locations/{location_id}', response_model=DeleteResponse)
async def api_delete_location(location_id: int, admin: User = Depends(require_superadmin), db: AsyncSession = Depends(get_db)):
    return await delete_location_point(location_id, db)


@app.get('/api/cycle-targets', response_model=AdminCycleTargetsResponse)
async def api_get_cycle_targets(location: str, target_date: date | None = None, admin: User = Depends(require_admin_or_superadmin), db: AsyncSession = Depends(get_db)):
    await ensure_user_can_access_location(admin, location, db)
    return await get_cycle_targets(location, db, target_date=target_date)


@app.post('/api/cycle-targets', response_model=SaveCycleTargetsResponse)
async def api_save_cycle_targets(payload: SaveCycleTargetsRequest, admin: User = Depends(require_admin_or_superadmin), db: AsyncSession = Depends(get_db)):
    await ensure_user_can_access_location(admin, payload.location, db)
    return await save_cycle_targets(payload, db)


@app.get('/api/users', response_model=UserListResponse)
async def api_list_users(admin: User = Depends(require_admin_or_superadmin), db: AsyncSession = Depends(get_db)):
    return await list_users(db, admin)


@app.post('/api/users', response_model=UserActionResponse)
async def api_create_user(payload: UserCreateRequest, admin: User = Depends(require_admin_or_superadmin), db: AsyncSession = Depends(get_db)):
    return await create_user(payload, db, current_user=admin)


@app.put('/api/users/{user_id}', response_model=UserActionResponse)
async def api_update_user(user_id: int, payload: UserUpdateRequest, admin: User = Depends(require_admin_or_superadmin), db: AsyncSession = Depends(get_db)):
    return await update_user(user_id, payload, db, current_user=admin)


@app.delete('/api/users/{user_id}', response_model=DeleteResponse)
async def api_delete_user(user_id: int, admin: User = Depends(require_admin_or_superadmin), db: AsyncSession = Depends(get_db)):
    return await delete_user(user_id, db, current_user=admin)


@app.get('/get-structure', response_model=InventoryStructureResponse)
async def get_inventory_structure(request: Request, user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    if user.role != 'employee' or not user.location:
        raise HTTPException(status_code=403, detail='Сотруднику не назначена точка.')

    request_id = _request_id(request)
    started = monotonic()
    logger.info('[req:%s] GET /get-structure start user_id=%s location=%s', request_id, user.id, user.location)
    try:
        response = await get_inventory_data(user.location, db, user)
        logger.info(
            '[req:%s] GET /get-structure ok user_id=%s location=%s report_id=%s categories=%s duration_ms=%s',
            request_id,
            user.id,
            user.location,
            response.report_id,
            len(response.categories),
            _duration_ms(started),
        )
        return response
    except HTTPException as exc:
        logger.warning(
            '[req:%s] GET /get-structure http_error user_id=%s location=%s status=%s detail=%s duration_ms=%s',
            request_id,
            user.id,
            user.location,
            exc.status_code,
            exc.detail,
            _duration_ms(started),
        )
        raise
    except Exception:
        logger.exception(
            '[req:%s] GET /get-structure crash user_id=%s location=%s duration_ms=%s',
            request_id,
            user.id,
            user.location,
            _duration_ms(started),
        )
        raise


@app.post('/assign-selection', response_model=AssignSelectionResponse)
async def api_assign_selection(payload: AssignSelectionRequest, user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    if user.role != 'employee':
        raise HTTPException(status_code=403, detail='Выбор доступен только сотруднику.')
    return await assign_selection_to_user(payload.report_id, payload.category_id, payload.target_type, payload.subcategory_id, payload.item_id, db, user)


@app.post('/verify', response_model=VerifyResponse)
async def verify_count(request: Request, req: VerifyRequest, user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    request_id = _request_id(request)
    started = monotonic()
    logger.info(
        '[req:%s] POST /verify start user_id=%s location=%s report_id=%s target_id=%s is_category=%s quantity=%s',
        request_id,
        user.id,
        user.location,
        req.report_id,
        req.target_id,
        req.is_category,
        req.quantity,
    )
    try:
        response = await verify_item_or_category(req, db, checked_by_user=user)
        logger.info(
            '[req:%s] POST /verify ok user_id=%s report_id=%s target_id=%s is_correct=%s attempts_left=%s expand_category=%s duration_ms=%s',
            request_id,
            user.id,
            req.report_id,
            req.target_id,
            response.is_correct,
            response.attempts_left,
            response.expand_category,
            _duration_ms(started),
        )
        return response
    except HTTPException as exc:
        logger.warning(
            '[req:%s] POST /verify http_error user_id=%s location=%s report_id=%s target_id=%s status=%s detail=%s duration_ms=%s',
            request_id,
            user.id,
            user.location,
            req.report_id,
            req.target_id,
            exc.status_code,
            exc.detail,
            _duration_ms(started),
        )
        raise
    except Exception:
        logger.exception(
            '[req:%s] POST /verify crash user_id=%s location=%s report_id=%s target_id=%s duration_ms=%s',
            request_id,
            user.id,
            user.location,
            req.report_id,
            req.target_id,
            _duration_ms(started),
        )
        raise


@app.post('/finish-report', response_model=FinishReportResponse)
async def complete_report(req: FinishReportRequest, user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    success, message = await finish_report(req.report_id, db, user)
    return FinishReportResponse(success=success, message=message)


@app.post('/start-report', response_model=StartReportResponse)
async def begin_report(req: StartReportRequest, user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    return await start_report(req.report_id, db, user)


@app.get('/api/reports', response_model=ReportHistoryResponse)
async def api_get_reports(location: str | None = None, user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    target_location = location or user.location
    if not target_location:
        raise HTTPException(status_code=400, detail='Точка не указана.')

    if user.role == 'employee':
        if target_location != user.location:
            raise HTTPException(status_code=403, detail='Нельзя смотреть чужую точку.')
    else:
        await ensure_user_can_access_location(user, target_location, db)

    return await get_reports_history(target_location, db)


@app.get('/api/report-period', response_model=AdminReport)
async def api_get_period_report(request: Request, location: str | None = None, date_from: date | None = None, date_to: date | None = None, admin: User = Depends(require_admin_or_superadmin), db: AsyncSession = Depends(get_db)):
    if not location:
        raise HTTPException(status_code=400, detail='Нужно указать точку.')
    if date_from is None or date_to is None:
        raise HTTPException(status_code=400, detail='Нужно указать даты периода.')
    await ensure_user_can_access_location(admin, location, db)

    request_id = _request_id(request)
    started = monotonic()
    logger.info('[req:%s] GET /api/report-period start user_id=%s location=%s date_from=%s date_to=%s', request_id, admin.id, location, date_from, date_to)
    try:
        response = await get_admin_period_report(location, date_from, date_to, db)
        logger.info(
            '[req:%s] GET /api/report-period ok user_id=%s location=%s date_from=%s date_to=%s categories=%s employees=%s duration_ms=%s',
            request_id,
            admin.id,
            location,
            date_from,
            date_to,
            len(response.categories),
            len(response.employees),
            _duration_ms(started),
        )
        return response
    except HTTPException as exc:
        logger.warning(
            '[req:%s] GET /api/report-period http_error user_id=%s location=%s date_from=%s date_to=%s status=%s detail=%s duration_ms=%s',
            request_id,
            admin.id,
            location,
            date_from,
            date_to,
            exc.status_code,
            exc.detail,
            _duration_ms(started),
        )
        raise
    except Exception:
        logger.exception(
            '[req:%s] GET /api/report-period crash user_id=%s location=%s date_from=%s date_to=%s duration_ms=%s',
            request_id,
            admin.id,
            location,
            date_from,
            date_to,
            _duration_ms(started),
        )
        raise


@app.get('/api/report', response_model=AdminReport)
async def api_get_report(request: Request, location: str | None = None, report_id: int | None = None, admin: User = Depends(require_admin_or_superadmin), db: AsyncSession = Depends(get_db)):
    if not location:
        raise HTTPException(status_code=400, detail='Нужно указать точку.')
    await ensure_user_can_access_location(admin, location, db)

    request_id = _request_id(request)
    started = monotonic()
    logger.info('[req:%s] GET /api/report start user_id=%s location=%s report_id=%s', request_id, admin.id, location, report_id)
    try:
        response = await get_admin_report(location, db, report_id)
        logger.info(
            '[req:%s] GET /api/report ok user_id=%s location=%s report_id=%s categories=%s employees=%s duration_ms=%s',
            request_id,
            admin.id,
            location,
            response.report_id,
            len(response.categories),
            len(response.employees),
            _duration_ms(started),
        )
        return response
    except HTTPException as exc:
        logger.warning(
            '[req:%s] GET /api/report http_error user_id=%s location=%s report_id=%s status=%s detail=%s duration_ms=%s',
            request_id,
            admin.id,
            location,
            report_id,
            exc.status_code,
            exc.detail,
            _duration_ms(started),
        )
        raise
    except Exception:
        logger.exception(
            '[req:%s] GET /api/report crash user_id=%s location=%s report_id=%s duration_ms=%s',
            request_id,
            admin.id,
            location,
            report_id,
            _duration_ms(started),
        )
        raise


@app.delete('/api/report/{report_id}', response_model=DeleteResponse)
async def api_delete_report(report_id: int, admin: User = Depends(require_admin_or_superadmin), db: AsyncSession = Depends(get_db)):
    return await delete_report(report_id, db, current_user=admin)


@app.get('/api/report/{report_id}/export-xlsx')
async def api_export_report_xlsx(report_id: int, admin: User = Depends(require_admin_or_superadmin), db: AsyncSession = Depends(get_db)):
    report_row = await db.get(Report, report_id)
    if not report_row:
        raise HTTPException(status_code=404, detail='Ревизия не найдена.')

    await ensure_user_can_access_location(admin, report_row.location, db)

    report = await get_admin_report(report_row.location, db, report_id=report_id)
    filename, payload = build_admin_report_excel(report)

    fallback_filename = f'report_{report_id}.xlsx'
    quoted_filename = quote(filename)

    return StreamingResponse(
        iter([payload]),
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={
            'Content-Disposition': f"attachment; filename=\"{fallback_filename}\"; filename*=UTF-8''{quoted_filename}",
            'X-Export-Report-Id': str(report_id),
        },
    )


@app.get('/api/report-period/export-xlsx')
async def api_export_period_report_xlsx(location: str | None = None, date_from: date | None = None, date_to: date | None = None, admin: User = Depends(require_admin_or_superadmin), db: AsyncSession = Depends(get_db)):
    if not location:
        raise HTTPException(status_code=400, detail='Нужно указать точку.')
    if date_from is None or date_to is None:
        raise HTTPException(status_code=400, detail='Нужно указать даты периода.')

    await ensure_user_can_access_location(admin, location, db)

    report = await get_admin_period_report(location, date_from, date_to, db)
    filename, payload = build_admin_report_excel(report)

    fallback_filename = 'period_report.xlsx'
    quoted_filename = quote(filename)

    return StreamingResponse(
        iter([payload]),
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={
            'Content-Disposition': f"attachment; filename=\"{fallback_filename}\"; filename*=UTF-8''{quoted_filename}",
            'X-Export-Period-From': date_from.isoformat(),
            'X-Export-Period-To': date_to.isoformat(),
        },
    )


@app.patch('/api/report/discrepancy/{check_result_id}', response_model=UpdateDiscrepancyResponse)
async def api_update_discrepancy(
    check_result_id: int,
    payload: UpdateDiscrepancyRequest,
    admin: User = Depends(require_admin_or_superadmin),
    db: AsyncSession = Depends(get_db),
):
    return await update_discrepancy_actual_qty(check_result_id, payload, db, current_user=admin)


@app.put('/api/report/discrepancy-cost/{check_result_id}', response_model=UpdateDiscrepancyCostOverrideResponse)
async def api_update_discrepancy_cost_override(
    check_result_id: int,
    payload: UpdateDiscrepancyCostOverrideRequest,
    admin: User = Depends(require_admin_or_superadmin),
    db: AsyncSession = Depends(get_db),
):
    return await update_discrepancy_cost_override(check_result_id, payload, db, current_user=admin)


@app.post('/api/report/{report_id}/reopen-employee-access', response_model=ReopenEmployeeAccessResponse)
async def api_reopen_employee_access(report_id: int, payload: ReopenEmployeeAccessRequest, admin: User = Depends(require_admin_or_superadmin), db: AsyncSession = Depends(get_db)):
    return await reopen_employee_report_access(report_id, payload.employee_user_id, db, admin)



@app.get('/api/payroll/access')
async def api_payroll_access(user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    return {'locations': await get_payroll_accessible_locations(user, db)}


@app.get('/api/payroll/categories')
async def api_payroll_categories(location: str, user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    return await get_payroll_category_catalog(location, db, user)


@app.get('/api/payroll/settings')
async def api_payroll_settings(location: str, effective_from: date | None = None, user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    return await get_location_payroll_setup(location, db, user, effective_from=effective_from)


@app.get('/api/payroll/shifts/setup')
async def api_payroll_shifts_setup(location: str, user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    return await get_location_shift_setup(location, db, user)


@app.put('/api/payroll/settings')
async def api_payroll_settings_update(payload: PayrollSettingsUpdateRequest, user: User = Depends(require_admin_or_superadmin), db: AsyncSession = Depends(get_db)):
    return await update_location_payroll_settings(payload, db, user)




@app.get('/api/payroll/recalc-status')
async def api_payroll_recalc_status(location: str, job_id: int | None = None, user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    return await get_payroll_recalc_status(location, db, user, job_id=job_id)

@app.get('/api/payroll/shifts')
async def api_payroll_shifts(location: str, date_from: date, date_to: date, employee_user_id: int | None = None, user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    return await list_work_shifts(location, date_from, date_to, db, user, employee_user_id=employee_user_id)


@app.get('/api/payroll/shifts/day-summary')
async def api_payroll_shift_day_summary(location: str, date_from: date, date_to: date, employee_user_id: int | None = None, user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    return await list_work_shift_day_summary(location, date_from, date_to, db, user, employee_user_id=employee_user_id)


@app.post('/api/payroll/shifts')
async def api_payroll_shifts_upsert(payload: WorkShiftUpsertRequest, user: User = Depends(require_admin_or_superadmin), db: AsyncSession = Depends(get_db)):
    return await upsert_work_shift(payload, db, user)


@app.delete('/api/payroll/shifts/{shift_id}')
async def api_payroll_shift_delete(shift_id: int, user: User = Depends(require_admin_or_superadmin), db: AsyncSession = Depends(get_db)):
    return await delete_work_shift(shift_id, db, user)


@app.post('/api/payroll/shifts/{shift_id}/close')
async def api_payroll_shift_close(shift_id: int, user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    return await close_shift(shift_id, db, actor_user=user, auto=False)


@app.get('/api/payroll/employee-summary')
async def api_payroll_employee_summary(location: str, date_from: date, date_to: date, employee_user_id: int | None = None, user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    return await get_employee_payroll_summary(location, date_from, date_to, db, user, employee_user_id=employee_user_id)


@app.get('/api/payroll/manager-summary')
async def api_payroll_manager_summary(location: str, date_from: date, date_to: date, user: User = Depends(require_admin_or_superadmin), db: AsyncSession = Depends(get_db)):
    return await get_manager_payroll_summary(location, date_from, date_to, db, user)


@app.get('/api/payroll/expense-templates')
async def api_payroll_expense_templates(location: str, user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    return await list_expense_templates(location, db, user)


@app.post('/api/payroll/expense-templates')
async def api_payroll_expense_template_create(payload: ExpenseTemplateCreateRequest, user: User = Depends(require_admin_or_superadmin), db: AsyncSession = Depends(get_db)):
    return await create_expense_template(payload, db, user)


@app.put('/api/payroll/expense-templates/{template_id}')
async def api_payroll_expense_template_update(template_id: int, payload: ExpenseTemplateUpdateRequest, user: User = Depends(require_admin_or_superadmin), db: AsyncSession = Depends(get_db)):
    return await update_expense_template(template_id, payload, db, user)


@app.post('/api/payroll/expense-templates/{template_id}/toggle-active')
async def api_payroll_expense_template_toggle_active(template_id: int, user: User = Depends(require_admin_or_superadmin), db: AsyncSession = Depends(get_db)):
    return await deactivate_expense_template(template_id, db, user)


@app.delete('/api/payroll/expense-templates/{template_id}')
async def api_payroll_expense_template_delete(template_id: int, user: User = Depends(require_admin_or_superadmin), db: AsyncSession = Depends(get_db)):
    return await delete_expense_template(template_id, db, user)


@app.get('/api/payroll/expenses')
async def api_payroll_expenses(location: str, month: date, user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    return await list_monthly_expenses(location, month, db, user)


@app.put('/api/payroll/expenses/{entry_id}')
async def api_payroll_expense_update(entry_id: int, payload: MonthlyExpenseEntryUpdateRequest, user: User = Depends(require_admin_or_superadmin), db: AsyncSession = Depends(get_db)):
    return await update_monthly_expense_entry(entry_id, payload, db, user)


@app.delete('/api/payroll/expenses/{entry_id}')
async def api_payroll_expense_delete(entry_id: int, user: User = Depends(require_admin_or_superadmin), db: AsyncSession = Depends(get_db)):
    return await delete_monthly_expense_entry(entry_id, db, user)


@app.post('/api/payroll/expenses/manual')
async def api_payroll_manual_expense_create(payload: ManualMonthlyExpenseCreateRequest, user: User = Depends(require_admin_or_superadmin), db: AsyncSession = Depends(get_db)):
    return await create_manual_monthly_expense(payload, db, user)



@app.get('/api/payroll/employee-bonuses')
async def api_payroll_employee_bonuses(location: str, month: date, user: User = Depends(require_admin_or_superadmin), db: AsyncSession = Depends(get_db)):
    return await list_employee_bonuses(location, month, db, user)


@app.post('/api/payroll/employee-bonuses')
async def api_payroll_employee_bonus_create(payload: EmployeeBonusCreateRequest, user: User = Depends(require_admin_or_superadmin), db: AsyncSession = Depends(get_db)):
    return await create_employee_bonus(payload, db, user)


@app.put('/api/payroll/employee-bonuses/{entry_id}')
async def api_payroll_employee_bonus_update(entry_id: int, payload: EmployeeBonusUpdateRequest, user: User = Depends(require_admin_or_superadmin), db: AsyncSession = Depends(get_db)):
    return await update_employee_bonus(entry_id, payload, db, user)


@app.delete('/api/payroll/employee-bonuses/{entry_id}')
async def api_payroll_employee_bonus_delete(entry_id: int, user: User = Depends(require_admin_or_superadmin), db: AsyncSession = Depends(get_db)):
    return await delete_employee_bonus(entry_id, db, user)


@app.get('/api/payroll/audit')
async def api_payroll_audit(location: str | None = None, limit: int = 200, user: User = Depends(require_admin_or_superadmin), db: AsyncSession = Depends(get_db)):
    return await list_payroll_audit_logs(location, db, user, limit=limit)


@app.get('/api/payroll/export-xlsx')
async def api_payroll_export_xlsx(location: str, date_from: date, date_to: date, employee_user_id: int | None = None, user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    filename, payload = await export_employee_payroll_xlsx(location, date_from, date_to, db, user, employee_user_id=employee_user_id)
    quoted = quote(filename)
    return StreamingResponse(
        io.BytesIO(payload),
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={
            'Content-Disposition': f"attachment; filename*=UTF-8''{quoted}",
            'X-Export-Filename': filename,
        },
    )


@app.get('/api/inventory-diagnostics')
async def api_inventory_diagnostics(location: str, admin: User = Depends(require_admin_or_superadmin), db: AsyncSession = Depends(get_db)):
    await ensure_user_can_access_location(admin, location, db)
    return await get_inventory_diagnostics_rows(location)


@app.get('/api/inventory-diagnostics/export')
async def api_export_inventory_diagnostics(location: str, admin: User = Depends(require_admin_or_superadmin), db: AsyncSession = Depends(get_db)):
    await ensure_user_can_access_location(admin, location, db)
    rows = await get_inventory_diagnostics_rows(location)

    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            'location',
            'issue_type',
            'category_name',
            'subcategory_name',
            'item_id',
            'item_name',
            'expected_qty',
            'reason',
            'folder_path',
            'folder_source',
            'assortment_lookup',
        ],
        extrasaction='ignore',
    )
    writer.writeheader()
    writer.writerows(rows)

    safe_location = ''.join(ch if ch.isalnum() or ch in ('-', '_') else '_' for ch in location)
    filename = f'inventory_diagnostics_{safe_location or "location"}.csv'
    payload = output.getvalue().encode('utf-8-sig')
    return StreamingResponse(
        iter([payload]),
        media_type='text/csv; charset=utf-8',
        headers={
            'Content-Disposition': f'attachment; filename="{filename}"',
            'X-Inventory-Diagnostics-Count': str(len(rows)),
        },
    )
