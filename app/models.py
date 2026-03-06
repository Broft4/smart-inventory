from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    location: Mapped[str] = mapped_column(String, index=True)
    store_id: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="in_progress", index=True)
    date_created: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    results: Mapped[list["CheckResult"]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )


class CheckResult(Base):
    __tablename__ = "check_results"
    __table_args__ = (
        UniqueConstraint("report_id", "target_id", name="uq_report_target"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id"), index=True)

    category_id: Mapped[str] = mapped_column(String, index=True)
    category_name: Mapped[str] = mapped_column(String)
    subcategory_id: Mapped[str] = mapped_column(String, index=True)
    subcategory_name: Mapped[str] = mapped_column(String)

    target_type: Mapped[str] = mapped_column(String, index=True)  # subcategory | item
    target_id: Mapped[str] = mapped_column(String, index=True)
    target_name: Mapped[str] = mapped_column(String)

    expected_qty: Mapped[float] = mapped_column(Float)
    actual_qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    diff: Mapped[float | None] = mapped_column(Float, nullable=True)
    attempts_used: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, default="grey", index=True)

    report: Mapped[Report] = relationship(back_populates="results")
