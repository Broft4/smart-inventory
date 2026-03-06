from app.schemas import InventoryStructureResponse, CategoryModel, ItemModel
from app.schemas import VerifyRequest, VerifyResponse
from datetime import datetime
from app.schemas import InventoryStructureResponse, CategoryModel, ItemModel, VerifyRequest, VerifyResponse, AdminReport, CategoryResult, DiscrepancyItem
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select # Добавляем select для поиска по базе
from app.models import CategoryResult as DB_CategoryResult
from app.schemas import CategoryResult as Schema_CategoryResult, AdminReport, DiscrepancyItem


async def get_inventory_data(location_name: str) -> InventoryStructureResponse:

    """
    Пока нет токена, эта функция возвращает заглушку (Mock-данные).
    Позже мы заменим этот код на реальные запросы к ms_client.
    """
    
    # Имитируем получение ID склада
    fake_store_id = "mock-uuid-12345" if location_name.lower() == "дмитров" else "mock-uuid-67890"

    # Имитируем сборку дерева категорий и товаров
    mock_categories = [
        CategoryModel(
            id="folder-1",
            name="Напитки",
            items=[
                ItemModel(id="item-1", name="Кока-кола 0.5", uom="шт"),
                ItemModel(id="item-2", name="Сок Яблочный 1л", uom="шт")
            ]
        ),
        CategoryModel(
            id="folder-2",
            name="Снеки",
            items=[
                ItemModel(id="item-3", name="Чипсы Lays", uom="шт"),
                ItemModel(id="item-4", name="Сухарики", uom="шт")
            ]
        )
    ]

    return InventoryStructureResponse(
        location=location_name.capitalize(),
        store_id=fake_store_id,
        categories=mock_categories
    )


async def verify_item_or_category(data: VerifyRequest, db: AsyncSession) -> VerifyResponse:
    # Заглушка: правильным ответом для тестов будет число 10
    correct_qty = 10 
    
    if data.quantity == correct_qty:
        # УСПЕХ: Записываем в базу зеленую карточку
        # ИСПОЛЬЗУЕМ DB_CategoryResult вместо CategoryResult!
        new_result = DB_CategoryResult(
            category_id=data.target_id,
            name=f"ID: {data.target_id}", 
            status="green",
            attempts_used=data.attempt_number
        )
        db.add(new_result)
        await db.commit() # Сохраняем изменения в файле inventory.db
        
        return VerifyResponse(
            is_correct=True, attempts_left=0, message="Верно!", expand_category=False
        )
    else:
        attempts_left = 3 - data.attempt_number
        
        if attempts_left > 0:
            msg = f"Неверно. Осталось {attempts_left} попытк(и)."
            return VerifyResponse(
                is_correct=False, attempts_left=attempts_left, message=msg, expand_category=False
            )
        else:
            # ПРОВАЛ: Попытки кончились.
            # ИСПОЛЬЗУЕМ DB_CategoryResult вместо CategoryResult!
            status = "orange" if data.is_category else "red"
            new_result = DB_CategoryResult(
                category_id=data.target_id,
                name=f"ID: {data.target_id}",
                status=status,
                attempts_used=data.attempt_number
            )
            db.add(new_result)
            await db.commit() 
            
            if data.is_category:
                msg = "Расхождение! Переходим к поштучной проверке..."
                expand = True
            else:
                msg = "Расхождение зафиксировано в системе."
                expand = False 
                
            return VerifyResponse(
                is_correct=False, attempts_left=0, message=msg, expand_category=expand
            )
        

# ... тут твои функции get_inventory_data и verify_item_or_category ...

async def get_admin_report(location: str, db: AsyncSession) -> AdminReport:
    """
    Возвращает фиктивный отчет для страницы администратора.
    В будущем здесь будет запрос к базе данных, где хранятся результаты.
    """
    # Имитируем текущую дату и время
    current_date = datetime.now().strftime("%d.%m.%Y %H:%M")
    
    return AdminReport(
        date=current_date,
        location=location,
        categories=[
            CategoryResult(
                name="Напитки",
                status="red", # Статус красный, значит есть расхождения
                problem_items=[
                    DiscrepancyItem(name="Кока-кола 0.5", expected=10, actual=8, diff=-2.0)
                ]
            ),
            CategoryResult(
                name="Снеки",
                status="green", # Тут всё сошлось идеально
                problem_items=[]
            )
        ],
        total_plus=0.0,
        total_minus=2.0 # Итого не хватает 2 штук
    )