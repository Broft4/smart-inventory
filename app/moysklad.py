import logging

import httpx

from app.config import settings


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MoySkladClient:
    def __init__(self):
        self.base_url = settings.ms_api_base_url.rstrip("/")

    @property
    def headers(self) -> dict[str, str]:
        if not settings.moysklad_token:
            raise RuntimeError("MOYSKLAD_TOKEN не задан. Для мок-режима этот клиент не используется.")
        return {
            "Authorization": f"Bearer {settings.moysklad_token}",
            "Accept-Encoding": "gzip",
            "Content-Type": "application/json",
        }

    async def get(self, endpoint: str, params: dict | None = None) -> dict:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url, headers=self.headers, params=params)
            response.raise_for_status()
            return response.json()

    async def get_all_pages(self, endpoint: str, params: dict | None = None) -> list[dict]:
        params = dict(params or {})
        params["limit"] = 1000
        params["offset"] = 0
        all_rows: list[dict] = []

        while True:
            data = await self.get(endpoint, params=params)
            rows = data.get("rows", [])
            all_rows.extend(rows)
            logger.info("Получено %s записей из %s", len(all_rows), endpoint)
            if len(rows) < 1000:
                break
            params["offset"] += 1000

        return all_rows

    async def get_stores_ids(self) -> dict[str, str]:
        data = await self.get("entity/store")
        stores_mapping: dict[str, str] = {}
        target_names = {settings.store_dmitrov.lower(), settings.store_dubna.lower()}
        for store in data.get("rows", []):
            store_name = store.get("name", "").strip()
            if store_name.lower() in target_names:
                stores_mapping[store_name] = store.get("id")
        return stores_mapping


ms_client = MoySkladClient()
