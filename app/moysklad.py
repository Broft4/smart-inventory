from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from time import monotonic
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import settings


logger = logging.getLogger(__name__)


DEFAULT_CATEGORY_NAME = 'Без категории'
DEFAULT_SUBCATEGORY_NAME = 'Без подкатегории'


@dataclass(slots=True)
class CacheEntry:
    value: dict[str, Any]
    expires_at: float


class MoySkladClient:
    def __init__(self) -> None:
        self.base_url = settings.ms_api_base_url.rstrip('/')
        self.timeout = settings.ms_request_timeout_seconds
        self.retry_attempts = max(1, settings.ms_retry_attempts)
        self.inventory_cache_ttl = max(15, settings.ms_inventory_cache_ttl_seconds)
        self._inventory_cache: dict[str, CacheEntry] = {}
        self._inventory_locks: dict[str, asyncio.Lock] = {}
        self._catalog_cache: CacheEntry | None = None
        self._catalog_lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return bool(settings.moysklad_token)

    @property
    def headers(self) -> dict[str, str]:
        if not self.enabled:
            raise RuntimeError('MOYSKLAD_TOKEN не задан. Клиент МоегоСклада недоступен.')
        return {
            'Authorization': f'Bearer {settings.moysklad_token}',
            'Accept-Encoding': 'gzip',
            'Content-Type': 'application/json',
        }

    def _cache_alive(self, entry: CacheEntry | None) -> bool:
        return bool(entry and entry.expires_at > monotonic())

    def _extract_id_from_href(self, href: str | None) -> str | None:
        if not href:
            return None
        path = urlparse(href).path.rstrip('/')
        return path.split('/')[-1] if path else None

    def _get_retry_delay(self, response: httpx.Response, attempt: int) -> float:
        retry_ms = response.headers.get('X-Lognex-Retry-TimeInterval') or response.headers.get('X-Lognex-Retry-After')
        if retry_ms:
            try:
                return max(float(retry_ms) / 1000.0, 0.5)
            except ValueError:
                pass
        return min(0.5 * attempt, 5.0)

    async def get(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        limits = httpx.Limits(max_connections=3, max_keepalive_connections=1)

        async with httpx.AsyncClient(timeout=self.timeout, limits=limits) as client:
            last_error: Exception | None = None
            for attempt in range(1, self.retry_attempts + 1):
                response = await client.get(url, headers=self.headers, params=params)
                if response.status_code != 429:
                    response.raise_for_status()
                    return response.json()

                last_error = httpx.HTTPStatusError('429 Too Many Requests', request=response.request, response=response)
                delay = self._get_retry_delay(response, attempt)
                logger.warning('МойСклад вернул 429 для %s. Повтор через %.2f сек. Попытка %s/%s.', url, delay, attempt, self.retry_attempts)
                await asyncio.sleep(delay)

            if last_error:
                raise last_error
            raise RuntimeError('Не удалось выполнить запрос к МойСклад.')

    async def get_all_pages(self, endpoint: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        params = dict(params or {})
        params['limit'] = 1000
        params['offset'] = 0
        all_rows: list[dict[str, Any]] = []

        while True:
            data = await self.get(endpoint, params=params)
            rows = data.get('rows', [])
            all_rows.extend(rows)
            logger.info('Получено %s записей из %s', len(all_rows), endpoint)
            if len(rows) < 1000:
                break
            params['offset'] += 1000

        return all_rows

    async def get_stores_ids(self) -> dict[str, str]:
        data = await self.get('entity/store')
        stores_mapping: dict[str, str] = {}
        target_names = {settings.store_dmitrov.lower(), settings.store_dubna.lower()}
        for store in data.get('rows', []):
            store_name = store.get('name', '').strip()
            if store_name.lower() in target_names:
                stores_mapping[store_name] = store.get('id')
        return stores_mapping

    async def _resolve_store(self, location: str) -> tuple[str, str]:
        normalized = location.strip().title()
        if normalized.lower() == settings.store_dmitrov.lower():
            if settings.store_dmitrov_id:
                return normalized, settings.store_dmitrov_id
        elif normalized.lower() == settings.store_dubna.lower():
            if settings.store_dubna_id:
                return normalized, settings.store_dubna_id
        else:
            raise ValueError(f'Неизвестная точка: {location}')

        stores = await self.get_stores_ids()
        for name, store_id in stores.items():
            if name.lower() == normalized.lower():
                return normalized, store_id
        raise ValueError(f'Для точки {location} не найден склад в МойСклад.')

    async def _get_catalog_bundle(self) -> dict[str, Any]:
        if self._cache_alive(self._catalog_cache):
            return self._catalog_cache.value

        async with self._catalog_lock:
            if self._cache_alive(self._catalog_cache):
                return self._catalog_cache.value

            folders = await self.get_all_pages('entity/productfolder')
            assortment = await self.get_all_pages('entity/assortment', params={'filter': 'archived=false'})

            folder_by_id: dict[str, dict[str, Any]] = {}
            for folder in folders:
                folder_by_id[folder.get('id')] = folder

            assortment_by_id: dict[str, dict[str, Any]] = {}
            assortment_by_href: dict[str, dict[str, Any]] = {}
            assortment_by_code: dict[str, dict[str, Any]] = {}
            for row in assortment:
                assortment_id = row.get('id')
                if assortment_id:
                    assortment_by_id[assortment_id] = row
                meta = row.get('meta') or {}
                href = meta.get('href')
                if href:
                    assortment_by_href[href] = row
                code = (row.get('code') or '').strip()
                if code:
                    assortment_by_code[code] = row

            bundle = {
                'folder_by_id': folder_by_id,
                'assortment_by_id': assortment_by_id,
                'assortment_by_href': assortment_by_href,
                'assortment_by_code': assortment_by_code,
            }
            self._catalog_cache = CacheEntry(value=bundle, expires_at=monotonic() + self.inventory_cache_ttl)
            return bundle

    def _resolve_folder_chain(self, folder_id: str | None, folder_by_id: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
        if not folder_id:
            return []

        chain: list[dict[str, str]] = []
        current = folder_by_id.get(folder_id)
        visited: set[str] = set()
        while current:
            current_id = current.get('id')
            if not current_id or current_id in visited:
                break
            visited.add(current_id)
            chain.append({'id': current_id, 'name': current.get('name') or 'Без названия'})

            parent = current.get('productFolder') or {}
            parent_meta = parent.get('meta') or {}
            parent_id = parent.get('id') or parent_meta.get('id') or self._extract_id_from_href(parent_meta.get('href'))
            current = folder_by_id.get(parent_id) if parent_id else None

        chain.reverse()
        return chain

    def _lookup_assortment(self, stock_row: dict[str, Any], catalog: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        assortment_meta = (stock_row.get('assortment') or {}).get('meta') or {}
        assortment_id = assortment_meta.get('id') or self._extract_id_from_href(assortment_meta.get('href'))
        if assortment_id:
            assortment = catalog['assortment_by_id'].get(assortment_id)
            if assortment:
                return assortment, 'assortment.id'
        if assortment_meta.get('href'):
            assortment = catalog['assortment_by_href'].get(assortment_meta['href'])
            if assortment:
                return assortment, 'assortment.href'
        code = (stock_row.get('code') or '').strip()
        if code:
            assortment = catalog['assortment_by_code'].get(code)
            if assortment:
                return assortment, 'stock_row.code'
        return None, None

    def _extract_folder_id(self, stock_row: dict[str, Any], catalog: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
        diagnostics: dict[str, Any] = {
            'folder_source': None,
            'assortment_lookup': None,
            'assortment_found': False,
            'reason': None,
        }

        for key in ('productFolder', 'folder'):
            candidate = stock_row.get(key) or {}
            if candidate:
                meta = candidate.get('meta') or {}
                folder_id = candidate.get('id') or meta.get('id') or self._extract_id_from_href(meta.get('href'))
                diagnostics['folder_source'] = f'stock_row.{key}'
                if folder_id:
                    return folder_id, diagnostics

        assortment, lookup_source = self._lookup_assortment(stock_row, catalog)
        diagnostics['assortment_lookup'] = lookup_source or 'не найдено'
        diagnostics['assortment_found'] = bool(assortment)
        if not assortment:
            return None, diagnostics

        folder = assortment.get('productFolder') or {}
        meta = folder.get('meta') or {}
        folder_id = folder.get('id') or meta.get('id') or self._extract_id_from_href(meta.get('href'))
        diagnostics['folder_source'] = 'assortment.productFolder'
        return folder_id, diagnostics

    def _build_item_diagnostics(
        self,
        stock_row: dict[str, Any],
        diagnostics: dict[str, Any],
        folder_id: str | None,
        folder_chain: list[dict[str, str]],
    ) -> dict[str, Any]:
        folder_source = diagnostics.get('folder_source') or '-'
        assortment_lookup = diagnostics.get('assortment_lookup') or '-'
        item_name = (stock_row.get('name') or stock_row.get('code') or 'Товар').strip()

        if folder_chain and len(folder_chain) > 1:
            return {
                'folder_chain': folder_chain,
                'folder_source': folder_source,
                'assortment_lookup': assortment_lookup,
                'reason': 'Категория и подкатегория определились корректно.',
            }

        if folder_chain and len(folder_chain) == 1:
            category_name = folder_chain[0].get('name') or 'верхняя папка'
            return {
                'folder_chain': folder_chain,
                'folder_source': folder_source,
                'assortment_lookup': assortment_lookup,
                'reason': f'У товара «{item_name}» определилась только верхняя папка «{category_name}». Вложенная папка не найдена, поэтому товар попал в «{DEFAULT_SUBCATEGORY_NAME}».',
            }

        if folder_id and not folder_chain:
            return {
                'folder_chain': [],
                'folder_source': folder_source,
                'assortment_lookup': assortment_lookup,
                'reason': 'У товара указана папка, но она не нашлась в справочнике папок МойСклад. Проверьте, существует ли папка и не удалена ли она.',
            }

        if diagnostics.get('assortment_found') and not folder_id:
            return {
                'folder_chain': [],
                'folder_source': folder_source,
                'assortment_lookup': assortment_lookup,
                'reason': 'Карточка ассортимента найдена, но у неё не заполнена папка товара. Поэтому категория не определилась.',
            }

        if assortment_lookup not in {'-', 'не найдено'}:
            return {
                'folder_chain': [],
                'folder_source': folder_source,
                'assortment_lookup': assortment_lookup,
                'reason': 'Попытка найти карточку ассортимента была, но по ссылке/ID/коду не удалось получить папку товара. Проверьте карточку ассортимента и её папку.',
            }

        return {
            'folder_chain': [],
            'folder_source': folder_source,
            'assortment_lookup': assortment_lookup,
            'reason': 'У остатка нет папки и не удалось определить карточку ассортимента, поэтому товар попал в «Без категории».',
        }

    def _extract_item_identity(self, location: str, stock_row: dict[str, Any]) -> tuple[str, str]:
        assortment_meta = (stock_row.get('assortment') or {}).get('meta') or {}
        assortment_id = assortment_meta.get('id') or self._extract_id_from_href(assortment_meta.get('href'))
        code = (stock_row.get('code') or '').strip()
        item_name = (stock_row.get('name') or code or 'Без названия').strip()
        item_id = assortment_id or code or f"{location.lower()}-{item_name.lower().replace(' ', '-') }"
        return item_id, item_name

    def _extract_expected_qty(self, stock_row: dict[str, Any]) -> float:
        value = stock_row.get('stock', 0)
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    async def _build_inventory(self, location: str) -> dict[str, Any]:
        normalized, store_id = await self._resolve_store(location)
        catalog = await self._get_catalog_bundle()
        store_href = f'{self.base_url}/entity/store/{store_id}'
        stock_rows = await self.get_all_pages(
            'report/stock/all',
            params={'filter': f'stockMode=all;quantityMode=all;store={store_href}'},
        )

        categories_map: dict[str, dict[str, Any]] = {}

        for stock_row in stock_rows:
            item_id, item_name = self._extract_item_identity(normalized, stock_row)
            expected_qty = self._extract_expected_qty(stock_row)
            folder_id, diagnostics = self._extract_folder_id(stock_row, catalog)
            folder_chain = self._resolve_folder_chain(folder_id, catalog['folder_by_id'])
            item_diagnostics = self._build_item_diagnostics(stock_row, diagnostics, folder_id, folder_chain)

            if folder_chain:
                category_info = folder_chain[0]
                sub_chain = folder_chain[1:]
                category_id = f"cat-{normalized.lower()}-{category_info['id']}"
                category_name = category_info['name']
            else:
                category_id = f"cat-{normalized.lower()}-root"
                category_name = DEFAULT_CATEGORY_NAME
                sub_chain = []

            if sub_chain:
                subcategory_name = ' / '.join(part['name'] for part in sub_chain)
                subcategory_suffix = '-'.join(part['id'] for part in sub_chain)
                subcategory_id = f'{category_id}-sub-{subcategory_suffix}'
            else:
                subcategory_name = DEFAULT_SUBCATEGORY_NAME
                subcategory_id = f'{category_id}-sub-root'

            category_bucket = categories_map.setdefault(
                category_id,
                {'id': category_id, 'name': category_name, 'subcategories': {}},
            )
            subcategory_bucket = category_bucket['subcategories'].setdefault(
                subcategory_id,
                {'id': subcategory_id, 'name': subcategory_name, 'items': []},
            )
            subcategory_bucket['items'].append({
                'id': item_id,
                'name': item_name,
                'expected_qty': expected_qty,
                'diagnostics': item_diagnostics,
            })

        categories: list[dict[str, Any]] = []
        for category in sorted(categories_map.values(), key=lambda item: item['name'].lower()):
            subcategories = []
            for subcategory in sorted(category['subcategories'].values(), key=lambda item: item['name'].lower()):
                unique_items: dict[str, dict[str, Any]] = {}
                for item in subcategory['items']:
                    unique_items[item['id']] = item
                subcategory['items'] = sorted(unique_items.values(), key=lambda item: item['name'].lower())
                subcategories.append(subcategory)
            categories.append({'id': category['id'], 'name': category['name'], 'subcategories': subcategories})

        return {'location': normalized, 'categories': categories}

    async def get_inventory(self, location: str) -> dict[str, Any]:
        normalized = location.strip().title()
        cached = self._inventory_cache.get(normalized)
        if self._cache_alive(cached):
            return cached.value

        lock = self._inventory_locks.setdefault(normalized, asyncio.Lock())
        async with lock:
            cached = self._inventory_cache.get(normalized)
            if self._cache_alive(cached):
                return cached.value

            inventory = await self._build_inventory(normalized)
            self._inventory_cache[normalized] = CacheEntry(value=inventory, expires_at=monotonic() + self.inventory_cache_ttl)
            return inventory

    async def prewarm_inventory(self, location: str) -> None:
        if not self.enabled or not location:
            return
        await self.get_inventory(location)


ms_client = MoySkladClient()
