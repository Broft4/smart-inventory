from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base

class Report(Base):
    """Главная таблица: Общий отчет по ревизии"""
    __tablename__ = "reports"

    id = Column(Integer, primary_key=True, index=True)
    location = Column(String, index=True) # Дмитров или Дубна
    date_created = Column(DateTime, default=datetime.utcnow)
    status = Column(String, default="в процессе") # Можно менять на "завершена"

    # Связь: у одного отчета может быть много проверенных категорий
    categories = relationship("CategoryResult", back_populates="report", cascade="all, delete-orphan")

class CategoryResult(Base):
    """Таблица: Результат проверки конкретной категории"""
    __tablename__ = "category_results"

    id = Column(Integer, primary_key=True, index=True)
    report_id = Column(Integer, ForeignKey("reports.id")) # Ссылка на главный отчет
    category_id = Column(String) # ID папки из МоегоСклада
    name = Column(String)        # Название (Напитки, Снеки)
    status = Column(String)      # green, orange, red
    attempts_used = Column(Integer, default=0)

    report = relationship("Report", back_populates="categories")
    discrepancies = relationship("DiscrepancyItem", back_populates="category", cascade="all, delete-orphan")

class DiscrepancyItem(Base):
    """Таблица: Конкретные товары с расхождениями"""
    __tablename__ = "discrepancies"

    id = Column(Integer, primary_key=True, index=True)
    category_result_id = Column(Integer, ForeignKey("category_results.id"))
    item_id = Column(String)     # ID товара из МоегоСклада
    name = Column(String)        # Название товара
    expected = Column(Float)     # Ожидаемый остаток
    actual = Column(Float)       # Фактический остаток
    diff = Column(Float)         # Разница

    category = relationship("CategoryResult", back_populates="discrepancies")