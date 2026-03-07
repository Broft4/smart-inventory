from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


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


class Report(Base):
    __tablename__ = 'reports'
    __table_args__ = (
        UniqueConstraint('location', 'report_date', name='uq_reports_location_report_date'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    location: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default='in_progress', nullable=False)
    date_created: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    results: Mapped[list['CheckResult']] = relationship(
        back_populates='report', cascade='all, delete-orphan'
    )


class CheckResult(Base):
    __tablename__ = 'check_results'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    report_id: Mapped[int] = mapped_column(ForeignKey('reports.id'), nullable=False, index=True)
    category_name: Mapped[str] = mapped_column(String(255), nullable=False)
    subcategory_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_type: Mapped[str] = mapped_column(String(20), nullable=False)  # subcategory/item
    target_id: Mapped[str] = mapped_column(String(100), nullable=False)
    target_name: Mapped[str] = mapped_column(String(255), nullable=False)
    expected_qty: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    actual_qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    diff: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    attempts_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    checked_by_user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    report: Mapped['Report'] = relationship(back_populates='results')
    checked_by_user: Mapped[User | None] = relationship(back_populates='check_results')
