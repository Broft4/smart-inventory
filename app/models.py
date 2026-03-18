from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
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
