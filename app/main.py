from __future__ import annotations

import asyncio
import csv
import io
import logging
import re
from urllib.parse import quote

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
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
    create_user,
    delete_report,
    delete_user,
    ensure_default_admin,
    finish_report,
    get_admin_report,
    get_inventory_data,
    get_inventory_diagnostics_details,
    get_inventory_diagnostics_rows,
    get_me_response,
    prewarm_inventory_cache,
    get_reports_history,
    list_users,
    reset_selection_cycle,
    update_user,
    user_to_schema,
    verify_item_or_category,
)
from app.models import User
from app.schemas import (
    AdminReport,
    AssignSelectionRequest,
    AssignSelectionResponse,
    DeleteResponse,
    FinishReportRequest,
    FinishReportResponse,
    InventoryStructureResponse,
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    MeResponse,
    ReportHistoryResponse,
    ResetSelectionCycleResponse,
    UserActionResponse,
    UserCreateRequest,
    UserListResponse,
    UserUpdateRequest,
    VerifyRequest,
    VerifyResponse,
)

BASE_DIR = Path(__file__).resolve().parents[1]
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(bootstrap_schema_and_admin)
    async with AsyncSession(bind=engine, expire_on_commit=False) as session:
        await ensure_default_admin(session)

    warmup_task = asyncio.create_task(prewarm_inventory_cache(settings.store_dmitrov)) if settings.store_dmitrov else None
    second_warmup_task = asyncio.create_task(prewarm_inventory_cache(settings.store_dubna)) if settings.store_dubna and settings.store_dubna != settings.store_dmitrov else None
    try:
        yield
    finally:
        for task in (warmup_task, second_warmup_task):
            if task and not task.done():
                task.cancel()


app = FastAPI(title='Умная ревизия', lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret_key)
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

app.mount('/static', StaticFiles(directory=BASE_DIR / 'static'), name='static')
templates = Jinja2Templates(directory=str(BASE_DIR / 'templates'))


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


async def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != 'admin':
        raise HTTPException(status_code=403, detail='Доступ только для администратора.')
    return user


@app.get('/login')
async def login_page(request: Request, user: User | None = Depends(get_current_user)):
    if user:
        return RedirectResponse(url='/admin' if user.role == 'admin' else '/', status_code=302)
    return templates.TemplateResponse('login.html', {'request': request})


@app.get('/')
async def inventory_page(request: Request, user: User | None = Depends(get_current_user)):
    if not user:
        return RedirectResponse(url='/login', status_code=302)
    if user.role == 'admin':
        return RedirectResponse(url='/admin', status_code=302)
    return templates.TemplateResponse(
        'index.html',
        {'request': request, 'user': user, 'no_location_assigned': not bool(user.location)},
    )


@app.get('/admin')
async def admin_page(request: Request, admin: User = Depends(require_admin)):
    return templates.TemplateResponse('admin.html', {'request': request, 'user': admin})


@app.post('/api/login', response_model=LoginResponse)
async def api_login(payload: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    user = await authenticate_user(payload.username.strip(), payload.password, db)
    if not user:
        raise HTTPException(status_code=401, detail='Неверный логин или пароль.')
    request.session['user_id'] = user.id

    if user.role == 'employee' and user.location:
        async def _warm_employee_inventory(location: str, username: str) -> None:
            try:
                await prewarm_inventory_cache(location)
            except Exception:
                logger.exception('Не удалось прогреть каталог для точки %s при входе пользователя %s.', location, username)

        asyncio.create_task(_warm_employee_inventory(user.location, user.username))

    return LoginResponse(
        success=True,
        message='Вход выполнен.',
        user=user_to_schema(user),
        redirect_to='/admin' if user.role == 'admin' else '/',
    )


@app.post('/api/logout', response_model=LogoutResponse)
async def api_logout(request: Request):
    request.session.clear()
    return LogoutResponse()


@app.get('/api/me', response_model=MeResponse)
async def api_me(user: User | None = Depends(get_current_user)):
    return await get_me_response(user)


@app.get('/api/users', response_model=UserListResponse)
async def api_list_users(admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return await list_users(db)


@app.post('/api/users', response_model=UserActionResponse)
async def api_create_user(payload: UserCreateRequest, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return await create_user(payload, db)


@app.put('/api/users/{user_id}', response_model=UserActionResponse)
async def api_update_user(user_id: int, payload: UserUpdateRequest, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return await update_user(user_id, payload, db, current_admin_id=admin.id)


@app.delete('/api/users/{user_id}', response_model=DeleteResponse)
async def api_delete_user(user_id: int, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return await delete_user(user_id, db, current_admin_id=admin.id)


@app.get('/get-structure', response_model=InventoryStructureResponse)
async def get_inventory_structure(user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    if user.role != 'employee' or not user.location:
        raise HTTPException(status_code=403, detail='Сотруднику не назначена точка.')
    return await get_inventory_data(user.location, db, user)


@app.post('/assign-selection', response_model=AssignSelectionResponse)
async def api_assign_selection(payload: AssignSelectionRequest, user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    if user.role != 'employee':
        raise HTTPException(status_code=403, detail='Выбор доступен только сотруднику.')
    return await assign_selection_to_user(payload.report_id, payload.category_id, payload.target_type, payload.subcategory_id, payload.item_id, db, user)


@app.post('/verify', response_model=VerifyResponse)
async def verify_count(req: VerifyRequest, user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    return await verify_item_or_category(req, db, checked_by_user=user)


@app.post('/finish-report', response_model=FinishReportResponse)
async def complete_report(req: FinishReportRequest, user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    success, message = await finish_report(req.report_id, db)
    return FinishReportResponse(success=success, message=message)


@app.get('/api/reports', response_model=ReportHistoryResponse)
async def api_get_reports(location: str | None = None, user: User = Depends(require_user), db: AsyncSession = Depends(get_db)):
    target_location = location or user.location
    if user.role != 'admin' and target_location != user.location:
        raise HTTPException(status_code=403, detail='Нельзя смотреть чужую точку.')
    if not target_location:
        raise HTTPException(status_code=400, detail='Точка не указана.')
    return await get_reports_history(target_location, db)


@app.get('/api/report', response_model=AdminReport)
async def api_get_report(location: str | None = None, report_id: int | None = None, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    if not location:
        raise HTTPException(status_code=400, detail='Нужно указать точку.')
    return await get_admin_report(location, db, report_id)


@app.delete('/api/report/{report_id}', response_model=DeleteResponse)
async def api_delete_report(report_id: int, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return await delete_report(report_id, db)


@app.get('/api/inventory-diagnostics')
async def api_inventory_diagnostics(location: str, admin: User = Depends(require_admin)):
    return {'location': location, 'rows': await get_inventory_diagnostics_details(location)}


@app.get('/api/inventory-diagnostics/export')
async def api_export_inventory_diagnostics(location: str, admin: User = Depends(require_admin)):
    rows = await get_inventory_diagnostics_rows(location)

    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=['location', 'issue_type', 'category_name', 'subcategory_name', 'item_id', 'item_name', 'expected_qty', 'reason', 'folder_path', 'folder_source', 'assortment_lookup'],
    )
    writer.writeheader()
    writer.writerows(rows)

    safe_location = re.sub(r'[^A-Za-z0-9._-]+', '_', location).strip('_') or 'location'
    filename = f'inventory_diagnostics_{safe_location}.csv'
    encoded_filename = quote(f'inventory_diagnostics_{location}.csv')
    payload = output.getvalue().encode('utf-8-sig')
    return StreamingResponse(
        iter([payload]),
        media_type='text/csv; charset=utf-8',
        headers={
            'Content-Disposition': f"attachment; filename=\"{filename}\"; filename*=UTF-8''{encoded_filename}",
            'X-Inventory-Diagnostics-Count': str(len(rows)),
        },
    )
