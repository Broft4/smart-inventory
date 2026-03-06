from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Base, engine, get_db
from app.logic import (
    delete_report,
    finish_report,
    get_admin_report,
    get_inventory_data,
    get_reports_history,
    verify_item_or_category,
)
from app.schemas import (
    AdminReport,
    FinishReportRequest,
    FinishReportResponse,
    InventoryStructureResponse,
    ReportHistoryResponse,
    VerifyRequest,
    VerifyResponse,
)
import app.models  # noqa: F401


BASE_DIR = Path(__file__).resolve().parents[1]

REQUIRED_REPORT_COLUMNS = {"id", "location", "store_id", "status", "date_created"}
LEGACY_TABLES = {"category_results", "discrepancies"}


def _bootstrap_schema(sync_conn):
    inspector = inspect(sync_conn)
    table_names = set(inspector.get_table_names())

    needs_reset = False

    if "reports" in table_names:
        report_columns = {column["name"] for column in inspector.get_columns("reports")}
        if not REQUIRED_REPORT_COLUMNS.issubset(report_columns):
            needs_reset = True

    if LEGACY_TABLES & table_names:
        needs_reset = True

    if "reports" in table_names and "check_results" not in table_names:
        needs_reset = True

    if needs_reset:
        sync_conn.execute(text("DROP TABLE IF EXISTS discrepancies"))
        sync_conn.execute(text("DROP TABLE IF EXISTS category_results"))
        sync_conn.execute(text("DROP TABLE IF EXISTS check_results"))
        sync_conn.execute(text("DROP TABLE IF EXISTS reports"))

    Base.metadata.create_all(sync_conn)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(_bootstrap_schema)
    yield


app = FastAPI(title="Умная ревизия", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.get("/")
async def root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/admin")
async def admin_page(request: Request):
    return templates.TemplateResponse("admin.html", {"request": request})


@app.get("/get-structure", response_model=InventoryStructureResponse)
async def get_inventory_structure(location: str, db: AsyncSession = Depends(get_db)):
    return await get_inventory_data(location, db)


@app.post("/verify", response_model=VerifyResponse)
async def verify_count(req: VerifyRequest, db: AsyncSession = Depends(get_db)):
    return await verify_item_or_category(req, db)


@app.post("/finish-report", response_model=FinishReportResponse)
async def complete_report(req: FinishReportRequest, db: AsyncSession = Depends(get_db)):
    success, message = await finish_report(req.report_id, db)
    return FinishReportResponse(success=success, message=message)


@app.delete("/api/report/{report_id}", response_model=FinishReportResponse)
async def api_delete_report(
    report_id: int,
    location: str = "Дубна",
    db: AsyncSession = Depends(get_db),
):
    success, message = await delete_report(report_id, location, db)
    return FinishReportResponse(success=success, message=message)


@app.get("/api/reports", response_model=ReportHistoryResponse)
async def api_get_reports(location: str = "Дубна", db: AsyncSession = Depends(get_db)):
    return await get_reports_history(location, db)


@app.get("/api/report", response_model=AdminReport)
async def api_get_report(
    location: str = "Дубна",
    report_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    return await get_admin_report(location, db, report_id)
