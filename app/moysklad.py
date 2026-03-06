import httpx
import logging
from app.config import settings

# Настроим простое логирование, чтобы видеть ошибки в терминале VS Code
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MoySkladClient:
    def __init__(self):
        self.base_url = settings.ms_api_base_url
        self.headers = {
            "Authorization": f"Bearer {settings.moysklad_token}",
            "Accept-Encoding": "gzip",
            "Content-Type": "application/json"
        }

    async def get(self, endpoint: str, params: dict = None) -> dict:
        """Базовый метод для GET-запросов к API МоегоСклада"""
        url = f"{self.base_url}/{endpoint}"
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(url, headers=self.headers, params=params)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                logger.error(f"Ошибка API МоегоСклада: {e.response.status_code} - {e.response.text}")
                raise

    async def get_stores_ids(self) -> dict:
        """
        Получает список складов из МоегоСклада и возвращает словарь:
        {'Дмитров': 'uuid-склада', 'Дубна': 'uuid-склада'}
        """
        logger.info("Запрашиваем список складов из МоегоСклада...")
        
        # Делаем запрос к эндпоинту складов
        data = await self.get("entity/store")
        
        stores_mapping = {}
        target_names = [settings.store_dmitrov.lower(), settings.store_dubna.lower()]

        # Парсим ответ
        for store in data.get("rows", []):
            store_name = store.get("name", "").strip().lower()
            if store_name in target_names:
                stores_mapping[store.get("name")] = store.get("id")

        logger.info(f"Найдены склады: {stores_mapping}")
        return stores_mapping
    
    async def get_all_pages(self, endpoint: str, params: dict = None) -> list:
        """
        Умная функция для обхода ограничения в 1000 строк.
        Она будет запрашивать данные, пока не скачает всё (через offset).
        """
        if params is None:
            params = {}
        
        params['limit'] = 1000
        params['offset'] = 0
        all_rows = []

        logger.info(f"Начинаем скачивание {endpoint}...")

        while True:
            data = await self.get(endpoint, params=params)
            rows = data.get("rows", [])
            all_rows.extend(rows)

            logger.info(f"Скачано {len(all_rows)} записей из {endpoint}...")

            # Если пришло меньше 1000, значит это была последняя страница
            if len(rows) < 1000:
                break
            
            # Иначе сдвигаем offset на 1000 для следующего запроса
            params['offset'] += 1000

        return all_rows

    async def get_categories_and_products(self):
        """
        Скачивает все группы товаров (папки) и сами товары.
        """
        # Скачиваем все группы товаров (папки)
        folders = await self.get_all_pages("entity/productfolder")
        
        # Скачиваем все товары
        products = await self.get_all_pages("entity/product")
        
        return folders, products

# Экземпляр клиента
ms_client = MoySkladClient()