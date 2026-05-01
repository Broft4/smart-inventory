from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class LocationPoint(Base):
    __tablename__ = 'location_points'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    ms_token: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ms_store_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ms_store_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    admin_accesses: Mapped[list['AdminLocationAccess']] = relationship(back_populates='location_point', cascade='all, delete-orphan')


class User(Base):
    __tablename__ = 'users'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    birth_date: Mapped[date] = mapped_column(Date, nullable=False)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default='employee', nullable=False)
    location: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    check_results: Mapped[list['CheckResult']] = relationship(back_populates='checked_by_user')
    selection_assignments: Mapped[list['CategoryAssignment']] = relationship(back_populates='user')
    admin_location_accesses: Mapped[list['AdminLocationAccess']] = relationship(
        back_populates='admin_user',
        foreign_keys='AdminLocationAccess.admin_user_id',
        cascade='all, delete-orphan',
    )
    granted_location_accesses: Mapped[list['AdminLocationAccess']] = relationship(
        back_populates='granted_by_user',
        foreign_keys='AdminLocationAccess.granted_by_user_id',
    )


class AdminLocationAccess(Base):
    __tablename__ = 'admin_location_access'
    __table_args__ = (
        UniqueConstraint('admin_user_id', 'location_point_id', name='uq_admin_location_access_admin_location'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    admin_user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    location_point_id: Mapped[int] = mapped_column(ForeignKey('location_points.id', ondelete='CASCADE'), nullable=False, index=True)
    granted_by_user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    admin_user: Mapped[User] = relationship(back_populates='admin_location_accesses', foreign_keys=[admin_user_id])
    granted_by_user: Mapped[User | None] = relationship(back_populates='granted_location_accesses', foreign_keys=[granted_by_user_id])
    location_point: Mapped[LocationPoint] = relationship(back_populates='admin_accesses')


class SelectionCycle(Base):
    __tablename__ = 'selection_cycles'
    __table_args__ = (UniqueConstraint('location', name='uq_selection_cycles_location'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    location: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    cycle_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    started_at: Mapped[date] = mapped_column(Date, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class Report(Base):
    __tablename__ = 'reports'
    __table_args__ = (
        UniqueConstraint('location', 'report_date', name='uq_reports_location_report_date'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    location: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    cycle_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    report_type: Mapped[str] = mapped_column(String(20), default='daily', nullable=False)
    status: Mapped[str] = mapped_column(String(20), default='in_progress', nullable=False)
    date_created: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    results: Mapped[list['CheckResult']] = relationship(back_populates='report', cascade='all, delete-orphan')


class SelectionTarget(Base):
    __tablename__ = 'selection_targets'
    __table_args__ = (
        UniqueConstraint('location', 'cycle_version', 'target_type', 'target_id', name='uq_selection_targets_per_cycle'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    location: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    cycle_version: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    category_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    category_name: Mapped[str] = mapped_column(String(255), nullable=False)
    subcategory_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    subcategory_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_type: Mapped[str] = mapped_column(String(20), nullable=False)
    target_id: Mapped[str] = mapped_column(String(100), nullable=False)
    target_name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class SelectionTargetDay(Base):
    __tablename__ = 'selection_target_days'
    __table_args__ = (
        UniqueConstraint('location', 'cycle_version', 'target_date', 'target_type', 'target_id', name='uq_selection_target_days_per_date'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    location: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    cycle_version: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    target_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    category_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    category_name: Mapped[str] = mapped_column(String(255), nullable=False)
    subcategory_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    subcategory_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_type: Mapped[str] = mapped_column(String(20), nullable=False)
    target_id: Mapped[str] = mapped_column(String(100), nullable=False)
    target_name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)



class CategoryAssignment(Base):
    __tablename__ = 'category_assignments'
    __table_args__ = (
        UniqueConstraint('location', 'cycle_version', 'target_type', 'target_id', name='uq_selection_target_per_cycle'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    location: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    cycle_version: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    category_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    category_name: Mapped[str] = mapped_column(String(255), nullable=False)
    subcategory_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    subcategory_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_type: Mapped[str] = mapped_column(String(20), nullable=False)
    target_id: Mapped[str] = mapped_column(String(100), nullable=False)
    target_name: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True, index=True)
    user_full_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    user: Mapped[User | None] = relationship(back_populates='selection_assignments')


class VerifyAttemptProgress(Base):
    __tablename__ = 'verify_attempt_progress'
    __table_args__ = (
        UniqueConstraint('report_id', 'target_type', 'target_id', 'checked_by_user_id', name='uq_verify_attempt_progress_per_target_user'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    report_id: Mapped[int] = mapped_column(ForeignKey('reports.id', ondelete='CASCADE'), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(20), nullable=False)
    target_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    checked_by_user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    attempts_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class ReportTargetSnapshot(Base):
    __tablename__ = 'report_target_snapshots'
    __table_args__ = (
        UniqueConstraint('report_id', 'target_type', 'target_id', 'assigned_user_id_snapshot', name='uq_report_target_snapshot_per_user'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    report_id: Mapped[int] = mapped_column(ForeignKey('reports.id', ondelete='CASCADE'), nullable=False, index=True)
    category_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    category_name: Mapped[str] = mapped_column(String(255), nullable=False)
    subcategory_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    subcategory_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_type: Mapped[str] = mapped_column(String(20), nullable=False)
    target_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    target_name: Mapped[str] = mapped_column(String(255), nullable=False)
    assigned_user_id_snapshot: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    assigned_user_name_snapshot: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class ReportEmployeeCompletion(Base):
    __tablename__ = 'report_employee_completions'
    __table_args__ = (
        UniqueConstraint('report_id', 'user_id', name='uq_report_employee_completion_per_user'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    report_id: Mapped[int] = mapped_column(ForeignKey('reports.id', ondelete='CASCADE'), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    user_full_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class ReportEmployeeStart(Base):
    __tablename__ = 'report_employee_starts'
    __table_args__ = (
        UniqueConstraint('report_id', 'user_id', name='uq_report_employee_start_per_user'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    report_id: Mapped[int] = mapped_column(ForeignKey('reports.id', ondelete='CASCADE'), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    user_full_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)



class CheckResult(Base):
    __tablename__ = 'check_results'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    report_id: Mapped[int] = mapped_column(ForeignKey('reports.id'), nullable=False, index=True)
    category_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    category_name: Mapped[str] = mapped_column(String(255), nullable=False)
    subcategory_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    subcategory_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_type: Mapped[str] = mapped_column(String(20), nullable=False)
    target_id: Mapped[str] = mapped_column(String(100), nullable=False)
    target_name: Mapped[str] = mapped_column(String(255), nullable=False)
    expected_qty: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    actual_qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    diff: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    attempts_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    checked_by_user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    checked_by_name_snapshot: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    report: Mapped['Report'] = relationship(back_populates='results')
    checked_by_user: Mapped[User | None] = relationship(back_populates='check_results')


class PayrollSettingsVersion(Base):
    __tablename__ = 'payroll_settings_versions'
    __table_args__ = (
        UniqueConstraint('location_point_id', 'effective_from', name='uq_payroll_settings_location_effective_from'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    location_point_id: Mapped[int] = mapped_column(ForeignKey('location_points.id', ondelete='CASCADE'), nullable=False, index=True)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    exit_amount: Mapped[float] = mapped_column(Float, default=2000.0, nullable=False)
    bonus_threshold: Mapped[float] = mapped_column(Float, default=40000.0, nullable=False)
    bonus_amount: Mapped[float] = mapped_column(Float, default=500.0, nullable=False)
    other_rate_percent: Mapped[float] = mapped_column(Float, default=3.0, nullable=False)
    bonus_category_ids_json: Mapped[str] = mapped_column(Text, default='[]', nullable=False)
    manager_salary_brackets_json: Mapped[str] = mapped_column(
        Text,
        default='[{"threshold": 200000.0, "rate_percent": 25.0}, {"threshold": 125000.0, "rate_percent": 20.0}, {"threshold": 100000.0, "rate_percent": 15.0}, {"threshold": 50000.0, "rate_percent": 10.0}]',
        nullable=False,
    )
    responsible_admin_user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class PayrollCategoryRateVersion(Base):
    __tablename__ = 'payroll_category_rate_versions'
    __table_args__ = (
        UniqueConstraint('settings_version_id', 'category_id', name='uq_payroll_category_rate_per_version'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    settings_version_id: Mapped[int] = mapped_column(ForeignKey('payroll_settings_versions.id', ondelete='CASCADE'), nullable=False, index=True)
    category_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    category_name: Mapped[str] = mapped_column(String(255), nullable=False)
    rate_percent: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)


class WorkShift(Base):
    __tablename__ = 'work_shifts'
    __table_args__ = (
        UniqueConstraint('location_point_id', 'shift_date', 'employee_user_id', name='uq_work_shift_location_date_employee'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    location_point_id: Mapped[int] = mapped_column(ForeignKey('location_points.id', ondelete='CASCADE'), nullable=False, index=True)
    shift_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    employee_user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default='planned', nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ShiftPayrollSnapshot(Base):
    __tablename__ = 'shift_payroll_snapshots'
    __table_args__ = (
        UniqueConstraint('shift_id', name='uq_shift_payroll_snapshot_shift'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    shift_id: Mapped[int] = mapped_column(ForeignKey('work_shifts.id', ondelete='CASCADE'), nullable=False, index=True)
    location_point_id: Mapped[int] = mapped_column(ForeignKey('location_points.id', ondelete='CASCADE'), nullable=False, index=True)
    employee_user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    shift_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    settings_version_id: Mapped[int | None] = mapped_column(ForeignKey('payroll_settings_versions.id', ondelete='SET NULL'), nullable=True, index=True)
    split_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    share_ratio: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    exit_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    bonus_threshold: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    bonus_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    other_rate_percent: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    non_tobacco_net_sales_for_bonus: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    gross_sales_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    return_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    net_sales_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    cost_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    gross_profit_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    category_earnings_total: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    employee_expense_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    gross_salary_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    net_salary_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    is_auto_closed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    closed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class ShiftPayrollCategorySnapshot(Base):
    __tablename__ = 'shift_payroll_category_snapshots'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey('shift_payroll_snapshots.id', ondelete='CASCADE'), nullable=False, index=True)
    category_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    category_name: Mapped[str] = mapped_column(String(255), nullable=False)
    rate_percent: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    sales_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    return_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    net_sales_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    cost_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    earning_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    is_other_category: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ProductFinancialCache(Base):
    __tablename__ = 'product_financial_cache'
    __table_args__ = (
        UniqueConstraint('location_point_id', 'item_id', name='uq_product_financial_cache_location_item'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    location_point_id: Mapped[int] = mapped_column(ForeignKey('location_points.id', ondelete='CASCADE'), nullable=False, index=True)
    item_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    item_name: Mapped[str] = mapped_column(String(255), nullable=False)
    item_code: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    cost_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    retail_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_refreshed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)




class ProductCostOverride(Base):
    __tablename__ = 'product_cost_overrides'
    __table_args__ = (
        UniqueConstraint('location_point_id', 'item_id', name='uq_product_cost_overrides_location_item'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    location_point_id: Mapped[int] = mapped_column(ForeignKey('location_points.id', ondelete='CASCADE'), nullable=False, index=True)
    item_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    item_name: Mapped[str] = mapped_column(String(255), nullable=False)
    cost_price: Mapped[float] = mapped_column(Float, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    updated_by_user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class PayrollDailyMetricCache(Base):
    __tablename__ = 'payroll_daily_metric_cache'
    __table_args__ = (
        UniqueConstraint('location_point_id', 'metric_date', name='uq_payroll_daily_metric_cache_location_date'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    location_point_id: Mapped[int] = mapped_column(ForeignKey('location_points.id', ondelete='CASCADE'), nullable=False, index=True)
    metric_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    gross_sales_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    return_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    net_sales_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    cost_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    gross_profit_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    non_tobacco_net_sales_for_bonus: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    categories_json: Mapped[str] = mapped_column(Text, default='[]', nullable=False)
    source_refreshed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class ExpenseTemplate(Base):
    __tablename__ = 'expense_templates'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    location_point_id: Mapped[int] = mapped_column(ForeignKey('location_points.id', ondelete='CASCADE'), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    amount_type: Mapped[str] = mapped_column(String(20), default='dynamic', nullable=False)
    default_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    day_of_month: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    assign_to_employee_by_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class MonthlyExpenseEntry(Base):
    __tablename__ = 'monthly_expense_entries'
    __table_args__ = (
        UniqueConstraint('template_id', 'month_start', name='uq_monthly_expense_template_month'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    template_id: Mapped[int | None] = mapped_column(ForeignKey('expense_templates.id', ondelete='CASCADE'), nullable=True, index=True)
    location_point_id: Mapped[int] = mapped_column(ForeignKey('location_points.id', ondelete='CASCADE'), nullable=False, index=True)
    month_start: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    expense_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    distribution_mode: Mapped[str] = mapped_column(String(20), default='spread', nullable=False)
    custom_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    is_paid: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    assigned_employee_user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    apply_to_employee_salary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_by_user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class EmployeeBonusEntry(Base):
    __tablename__ = 'employee_bonus_entries'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    location_point_id: Mapped[int] = mapped_column(ForeignKey('location_points.id', ondelete='CASCADE'), nullable=False, index=True)
    month_start: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    bonus_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    employee_user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    updated_by_user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class PayrollAuditLog(Base):
    __tablename__ = 'payroll_audit_logs'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    location_point_id: Mapped[int | None] = mapped_column(ForeignKey('location_points.id', ondelete='SET NULL'), nullable=True, index=True)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    action_type: Mapped[str] = mapped_column(String(50), nullable=False)
    details_json: Mapped[str] = mapped_column(Text, nullable=False, default='{}')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)



class PayrollRecalcJob(Base):
    __tablename__ = 'payroll_recalc_jobs'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    location_point_id: Mapped[int] = mapped_column(ForeignKey('location_points.id', ondelete='CASCADE'), nullable=False, index=True)
    settings_version_id: Mapped[int | None] = mapped_column(ForeignKey('payroll_settings_versions.id', ondelete='SET NULL'), nullable=True, index=True)
    requested_by_user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    date_from: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    date_to: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default='queued', nullable=False, index=True)
    progress_current: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    progress_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    message: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[str] = mapped_column(Text, default='{}', nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
