from __future__ import annotations

import asyncio
import csv
import io
import logging

from contextlib import asynccontextmanager
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
    ensure_user_can_access_location,
    finish_report,
    get_admin_report,
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
    update_location_point,
    update_user,
    user_to_schema,
    verify_item_or_category,
)
from app.models import User
from app.moysklad import ms_client
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
    async with AsyncSession(bind=engine, expire_on_commit=False) as session:
        await ensure_default_admin(session)
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
templates.env.globals['asset_version'] = '20260321-admin-detail-yellow-success-fix'


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
        raise HTTPException(status_code=403, detail='Доступ только для администратора.')
    return user


async def require_superadmin(user: User = Depends(require_user)) -> User:
    if user.role != 'superadmin':
        raise HTTPException(status_code=403, detail='Доступ только для главного администратора.')
    return user


@app.get('/login')
async def login_page(request: Request, user: User | None = Depends(get_current_user)):
    if user:
        return RedirectResponse(url='/admin' if user.role in {'admin', 'superadmin'} else '/', status_code=302)
    return templates.TemplateResponse('login.html', {'request': request})


@app.get('/')
async def inventory_page(request: Request, user: User | None = Depends(get_current_user)):
    if not user:
        return RedirectResponse(url='/login', status_code=302)
    if user.role in {'admin', 'superadmin'}:
        return RedirectResponse(url='/admin', status_code=302)
    _spawn_prewarm(user.location)
    return templates.TemplateResponse(
        'index.html',
        {'request': request, 'user': user, 'no_location_assigned': not bool(user.location)},
    )


@app.get('/admin')
async def admin_page(request: Request, user: User | None = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if not user:
        return RedirectResponse(url='/login', status_code=302)
    if user.role not in {'admin', 'superadmin'}:
        return RedirectResponse(url='/', status_code=302)
    accessible_locations = await get_user_accessible_locations(user, db)
    if accessible_locations:
        _spawn_prewarm(accessible_locations[0])
    return templates.TemplateResponse('admin.html', {'request': request, 'user': user})


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
async def api_get_cycle_targets(location: str, admin: User = Depends(require_admin_or_superadmin), db: AsyncSession = Depends(get_db)):
    await ensure_user_can_access_location(admin, location, db)
    return await get_cycle_targets(location, db)


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


@app.post('/api/report/{report_id}/reopen-employee-access', response_model=ReopenEmployeeAccessResponse)
async def api_reopen_employee_access(report_id: int, payload: ReopenEmployeeAccessRequest, admin: User = Depends(require_admin_or_superadmin), db: AsyncSession = Depends(get_db)):
    return await reopen_employee_report_access(report_id, payload.employee_user_id, db, admin)


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
