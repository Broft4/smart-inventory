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
    value: Any
    expires_at: float


class MoySkladClient:
    def __init__(self) -> None:
        self.base_url = settings.ms_api_base_url.rstrip('/')

        configured_timeout = float(settings.ms_request_timeout_seconds or 20)
        self.request_timeout = max(5.0, configured_timeout)

        configured_retries = int(settings.ms_retry_attempts or 2)
        self.retry_attempts = max(1, configured_retries)

        configured_inventory_ttl = int(settings.ms_inventory_cache_ttl_seconds or 300)
        self.inventory_cache_ttl = max(30, configured_inventory_ttl)

        self.folder_cache_ttl = max(1800, self.inventory_cache_ttl)
        self.assortment_item_cache_ttl = max(900, self.inventory_cache_ttl)

        self._inventory_cache: dict[str, CacheEntry] = {}
        self._inventory_locks: dict[str, asyncio.Lock] = {}

        self._folders_cache: CacheEntry | None = None
        self._folders_lock = asyncio.Lock()

        self._assortment_cache: dict[str, CacheEntry] = {}
        self._assortment_locks: dict[str, asyncio.Lock] = {}

        self._stores_cache: CacheEntry | None = None
        self._stores_lock = asyncio.Lock()

        self._product_cache: dict[str, CacheEntry] = {}
        self._product_locks: dict[str, asyncio.Lock] = {}

        self._financials_by_location_cache: dict[str, CacheEntry] = {}

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

    def _build_timeout(self) -> httpx.Timeout:
        connect_timeout = min(10.0, self.request_timeout)
        return httpx.Timeout(
            connect=connect_timeout,
            read=self.request_timeout,
            write=self.request_timeout,
            pool=connect_timeout,
        )

    def _cache_alive(self, entry: CacheEntry | None) -> bool:
        return bool(entry and entry.expires_at > monotonic())

    def _extract_id_from_href(self, href: str | None) -> str | None:
        if not href:
            return None
        path = urlparse(href).path.rstrip('/')
        if not path:
            return None
        return path.split('/')[-1]

    def _get_retry_delay(self, response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get('X-Lognex-Retry-TimeInterval') or response.headers.get('X-Lognex-Retry-After')
        if retry_after:
            try:
                return max(float(retry_after) / 1000.0, 0.5)
            except ValueError:
                pass
        return min(1.5 * attempt, 6.0)

    def _get_exception_retry_delay(self, attempt: int) -> float:
        return min(1.5 * attempt, 6.0)

    async def _request_json(
        self,
        url_or_endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        absolute: bool = False,
    ) -> dict[str, Any]:
        url = url_or_endpoint if absolute else f"{self.base_url}/{url_or_endpoint.lstrip('/')}"
        limits = httpx.Limits(max_connections=4, max_keepalive_connections=2)
        timeout = self._build_timeout()

        last_error: Exception | None = None

        async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
            for attempt in range(1, self.retry_attempts + 1):
                try:
                    response = await client.get(url, headers=self.headers, params=params)

                    if response.status_code == 429:
                        delay = self._get_retry_delay(response, attempt)
                        logger.warning(
                            'МойСклад вернул 429 для %s. Попытка %s/%s. Повтор через %.2f сек.',
                            url,
                            attempt,
                            self.retry_attempts,
                            delay,
                        )
                        if attempt < self.retry_attempts:
                            await asyncio.sleep(delay)
                            continue
                        response.raise_for_status()

                    response.raise_for_status()
                    return response.json()

                except httpx.ReadTimeout as exc:
                    last_error = exc
                    delay = self._get_exception_retry_delay(attempt)
                    logger.warning(
                        'Таймаут чтения при запросе к МойСклад: %s. Попытка %s/%s. Повтор через %.2f сек.',
                        url,
                        attempt,
                        self.retry_attempts,
                        delay,
                    )
                    if attempt < self.retry_attempts:
                        await asyncio.sleep(delay)
                        continue
                    raise

                except httpx.ConnectTimeout as exc:
                    last_error = exc
                    delay = self._get_exception_retry_delay(attempt)
                    logger.warning(
                        'Таймаут подключения к МойСклад: %s. Попытка %s/%s. Повтор через %.2f сек.',
                        url,
                        attempt,
                        self.retry_attempts,
                        delay,
                    )
                    if attempt < self.retry_attempts:
                        await asyncio.sleep(delay)
                        continue
                    raise

                except httpx.HTTPStatusError as exc:
                    last_error = exc
                    status_code = exc.response.status_code if exc.response is not None else 'unknown'
                    logger.exception(
                        'HTTP-ошибка МойСклад %s для %s на попытке %s/%s.',
                        status_code,
                        url,
                        attempt,
                        self.retry_attempts,
                    )
                    raise

                except httpx.RequestError as exc:
                    last_error = exc
                    delay = self._get_exception_retry_delay(attempt)
                    logger.warning(
                        'Сетевая ошибка при запросе к МойСклад: %s. Попытка %s/%s. Повтор через %.2f сек.',
                        url,
                        attempt,
                        self.retry_attempts,
                        delay,
                    )
                    if attempt < self.retry_attempts:
                        await asyncio.sleep(delay)
                        continue
                    raise

        if last_error:
            raise last_error
        raise RuntimeError('Не удалось выполнить запрос к МойСклад.')

    async def get(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request_json(endpoint, params=params, absolute=False)

    async def get_absolute(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request_json(url, params=params, absolute=True)

    async def get_all_pages(self, endpoint: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        params = dict(params or {})
        params['limit'] = 1000
        params['offset'] = 0
        all_rows: list[dict[str, Any]] = []

        while True:
            data = await self.get(endpoint, params=params)
            rows = data.get('rows', [])
            all_rows.extend(rows)
            if len(rows) < 1000:
                break
            params['offset'] += 1000

        logger.info('Получено %s записей из %s', len(all_rows), endpoint)
        return all_rows

    async def get_stores_ids(self) -> dict[str, str]:
        if self._cache_alive(self._stores_cache):
            return self._stores_cache.value

        async with self._stores_lock:
            if self._cache_alive(self._stores_cache):
                return self._stores_cache.value

            data = await self.get('entity/store')
            stores_mapping: dict[str, str] = {}
            target_names = {
                (settings.store_dmitrov or '').strip().lower(),
                (settings.store_dubna or '').strip().lower(),
            }

            for store in data.get('rows', []):
                store_name = (store.get('name') or '').strip()
                if store_name.lower() in target_names:
                    store_id = store.get('id')
                    if store_id:
                        stores_mapping[store_name] = store_id

            self._stores_cache = CacheEntry(
                value=stores_mapping,
                expires_at=monotonic() + 3600,
            )
            return stores_mapping

    async def _resolve_store(self, location: str) -> tuple[str, str]:
        normalized = location.strip().title()

        if normalized.lower() == (settings.store_dmitrov or '').lower():
            if settings.store_dmitrov_id:
                return normalized, settings.store_dmitrov_id
        elif normalized.lower() == (settings.store_dubna or '').lower():
            if settings.store_dubna_id:
                return normalized, settings.store_dubna_id
        else:
            raise ValueError(f'Неизвестная точка: {location}')

        stores = await self.get_stores_ids()
        for name, store_id in stores.items():
            if name.lower() == normalized.lower():
                return normalized, store_id

        raise ValueError(f'Для точки {location} не найден склад в МойСклад.')

    async def _get_folder_map(self) -> dict[str, dict[str, Any]]:
        if self._cache_alive(self._folders_cache):
            return self._folders_cache.value

        async with self._folders_lock:
            if self._cache_alive(self._folders_cache):
                return self._folders_cache.value

            rows = await self.get_all_pages('entity/productfolder')
            folder_by_id: dict[str, dict[str, Any]] = {}

            for folder in rows:
                folder_id = folder.get('id')
                if folder_id:
                    folder_by_id[folder_id] = folder

            self._folders_cache = CacheEntry(
                value=folder_by_id,
                expires_at=monotonic() + self.folder_cache_ttl,
            )
            logger.info('Закешировано %s папок МоегоСклада', len(folder_by_id))
            return folder_by_id

    async def _get_assortment_row_by_meta(self, assortment_meta: dict[str, Any] | None) -> tuple[dict[str, Any] | None, str | None]:
        if not assortment_meta:
            return None, None

        href = assortment_meta.get('href')
        assortment_id = assortment_meta.get('id') or self._extract_id_from_href(href)
        cache_key = href or assortment_id
        if not cache_key:
            return None, None

        cached = self._assortment_cache.get(cache_key)
        if self._cache_alive(cached):
            return cached.value, 'cache'

        lock = self._assortment_locks.setdefault(cache_key, asyncio.Lock())
        async with lock:
            cached = self._assortment_cache.get(cache_key)
            if self._cache_alive(cached):
                return cached.value, 'cache'

            try:
                if href:
                    row = await self.get_absolute(href)
                    source = 'assortment.meta.href'
                elif assortment_id:
                    row = await self.get(f'entity/assortment/{assortment_id}')
                    source = 'assortment.id'
                else:
                    return None, None
            except httpx.HTTPError:
                logger.warning('Не удалось точечно получить карточку ассортимента для %s', cache_key)
                return None, None

            self._assortment_cache[cache_key] = CacheEntry(
                value=row,
                expires_at=monotonic() + self.assortment_item_cache_ttl,
            )
            return row, source

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
            chain.append({
                'id': current_id,
                'name': current.get('name') or 'Без названия',
            })

            parent = current.get('productFolder') or {}
            parent_meta = parent.get('meta') or {}
            parent_id = parent.get('id') or parent_meta.get('id') or self._extract_id_from_href(parent_meta.get('href'))
            current = folder_by_id.get(parent_id) if parent_id else None

        chain.reverse()
        return chain

    async def _extract_folder_id(
        self,
        stock_row: dict[str, Any],
        folder_by_id: dict[str, dict[str, Any]],
    ) -> tuple[str | None, dict[str, Any]]:
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

        assortment_meta = (stock_row.get('assortment') or {}).get('meta') or {}
        assortment_row, lookup_source = await self._get_assortment_row_by_meta(assortment_meta)
        diagnostics['assortment_lookup'] = lookup_source or 'не найдено'
        diagnostics['assortment_found'] = bool(assortment_row)

        if not assortment_row:
            return None, diagnostics

        folder = assortment_row.get('productFolder') or assortment_row.get('folder') or {}
        meta = folder.get('meta') or {}
        folder_id = folder.get('id') or meta.get('id') or self._extract_id_from_href(meta.get('href'))
        diagnostics['folder_source'] = 'assortment.productFolder'

        if folder_id and folder_id not in folder_by_id:
            logger.warning('Папка %s найдена у ассортимента, но отсутствует в кеше productfolder', folder_id)

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
                'reason': 'Попытка найти карточку ассортимента была, но по ссылке/ID не удалось получить папку товара. Проверьте карточку ассортимента и её папку.',
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
        item_id = assortment_id or code or f"{location.lower()}-{item_name.lower().replace(' ', '-')}"
        return item_id, item_name

    def _extract_expected_qty(self, stock_row: dict[str, Any]) -> float:
        value = stock_row.get('stock', 0)
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    async def _get_entity_by_meta(self, meta: dict[str, Any] | None, *, cache: dict[str, CacheEntry], locks: dict[str, asyncio.Lock], entity_name: str) -> tuple[dict[str, Any] | None, str | None]:
        if not meta:
            return None, None

        href = meta.get('href')
        entity_id = meta.get('id') or self._extract_id_from_href(href)
        cache_key = href or entity_id
        if not cache_key:
            return None, None

        cached = cache.get(cache_key)
        if self._cache_alive(cached):
            return cached.value, 'cache'

        lock = locks.setdefault(cache_key, asyncio.Lock())
        async with lock:
            cached = cache.get(cache_key)
            if self._cache_alive(cached):
                return cached.value, 'cache'

            try:
                if href:
                    row = await self.get_absolute(href)
                    source = f'{entity_name}.meta.href'
                elif entity_id:
                    row = await self.get(f'entity/{entity_name}/{entity_id}')
                    source = f'{entity_name}.id'
                else:
                    return None, None
            except httpx.HTTPError:
                logger.warning('Не удалось получить карточку %s для %s', entity_name, cache_key)
                return None, None

            cache[cache_key] = CacheEntry(
                value=row,
                expires_at=monotonic() + self.assortment_item_cache_ttl,
            )
            return row, source

    async def _get_product_row_by_meta(self, product_meta: dict[str, Any] | None) -> tuple[dict[str, Any] | None, str | None]:
        return await self._get_entity_by_meta(product_meta, cache=self._product_cache, locks=self._product_locks, entity_name='product')

    def _extract_stock_retail_price(self, stock_row: dict[str, Any]) -> float | None:
        return self._normalize_money_value(stock_row.get('salePrice'))

    def _build_financial_seed(self, stock_row: dict[str, Any], item_id: str) -> dict[str, Any]:
        assortment_meta = (stock_row.get('assortment') or {}).get('meta') or {}
        code = (stock_row.get('code') or '').strip() or None
        return {
            'item_id': item_id,
            'code': code,
            'retail_price': self._extract_stock_retail_price(stock_row),
            'assortment_id': assortment_meta.get('id') or self._extract_id_from_href(assortment_meta.get('href')),
            'assortment_href': assortment_meta.get('href'),
        }

    def _get_financial_seed(self, location: str, item_id: str) -> dict[str, Any] | None:
        normalized = location.strip().title()
        cached = self._financials_by_location_cache.get(normalized)
        if self._cache_alive(cached):
            return (cached.value or {}).get(item_id)
        return None

    def _normalize_money_value(self, value: Any) -> float | None:
        if isinstance(value, dict):
            value = value.get('value')
        if value is None:
            return None
        try:
            amount = float(value)
        except (TypeError, ValueError):
            return None
        return round(amount / 100.0, 2)

    def _extract_sale_price_from_source(self, source: dict[str, Any] | None) -> float | None:
        if not source:
            return None

        sale_prices = source.get('salePrices') or []
        if isinstance(sale_prices, dict):
            sale_prices = sale_prices.get('rows') or []

        entries: list[dict[str, Any]] = [entry for entry in sale_prices if isinstance(entry, dict)]
        if not entries:
            direct_price = self._normalize_money_value(source.get('salePrice') or source.get('price'))
            return direct_price

        preferred = None
        for entry in entries:
            price_type = str(entry.get('priceType') or '').lower()
            if 'продаж' in price_type:
                preferred = entry
                break

        candidate = preferred or next((entry for entry in entries if self._normalize_money_value(entry) not in {None, 0.0}), None) or entries[0]
        return self._normalize_money_value(candidate)

    def _extract_buy_price_from_source(self, source: dict[str, Any] | None) -> float | None:
        if not source:
            return None
        return self._normalize_money_value(source.get('buyPrice'))

    def _extract_financials_from_sources(self, *sources: dict[str, Any] | None) -> tuple[float | None, float | None]:
        cost_price = None
        retail_price = None
        for source in sources:
            if cost_price is None:
                cost_price = self._extract_buy_price_from_source(source)
            if retail_price is None:
                retail_price = self._extract_sale_price_from_source(source)
            if cost_price is not None and retail_price is not None:
                break
        return cost_price, retail_price

    async def get_item_financials(self, location: str, item_id: str) -> dict[str, float | None]:
        if not item_id:
            return {'cost_price': None, 'retail_price': None}

        seed = self._get_financial_seed(location, item_id)
        if seed is None:
            try:
                await self.get_inventory(location)
            except Exception:
                logger.exception('Не удалось прогреть инвентарь для финансов товара %s (%s)', item_id, location)
            seed = self._get_financial_seed(location, item_id)

        retail_price = seed.get('retail_price') if seed else None
        assortment_meta = None
        if seed and (seed.get('assortment_href') or seed.get('assortment_id')):
            assortment_meta = {
                'href': seed.get('assortment_href'),
                'id': seed.get('assortment_id'),
            }
        elif item_id:
            assortment_meta = {'id': item_id}

        assortment_row, _ = await self._get_assortment_row_by_meta(assortment_meta)
        if not assortment_row:
            return {'cost_price': None, 'retail_price': retail_price}

        product_meta = assortment_row.get('product') if isinstance(assortment_row.get('product'), dict) else None
        product_row, _ = await self._get_product_row_by_meta((product_meta or {}).get('meta') if isinstance(product_meta, dict) else None)

        cost_price, fallback_retail_price = self._extract_financials_from_sources(assortment_row, product_row, product_meta)
        return {
            'cost_price': cost_price,
            'retail_price': retail_price if retail_price is not None else fallback_retail_price,
        }

    async def _build_inventory(self, location: str) -> dict[str, Any]:
        normalized, store_id = await self._resolve_store(location)
        store_href = f'{self.base_url}/entity/store/{store_id}'

        folder_by_id, stock_rows = await asyncio.gather(
            self._get_folder_map(),
            self.get_all_pages(
                'report/stock/all',
                params={'filter': f'stockMode=all;quantityMode=all;store={store_href}'},
            ),
        )

        categories_map: dict[str, dict[str, Any]] = {}
        financial_index: dict[str, dict[str, Any]] = {}

        for stock_row in stock_rows:
            item_id, item_name = self._extract_item_identity(normalized, stock_row)
            expected_qty = self._extract_expected_qty(stock_row)
            financial_index[item_id] = self._build_financial_seed(stock_row, item_id)

            folder_id, diagnostics = await self._extract_folder_id(stock_row, folder_by_id)
            folder_chain = self._resolve_folder_chain(folder_id, folder_by_id)
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

            categories.append({
                'id': category['id'],
                'name': category['name'],
                'subcategories': subcategories,
            })

        self._financials_by_location_cache[normalized] = CacheEntry(
            value=financial_index,
            expires_at=monotonic() + self.inventory_cache_ttl,
        )

        logger.info('Для точки %s собрано %s категорий и %s товаров', normalized, len(categories), len(stock_rows))
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
            self._inventory_cache[normalized] = CacheEntry(
                value=inventory,
                expires_at=monotonic() + self.inventory_cache_ttl,
            )
            return inventory

    async def prewarm_inventory(self, location: str) -> None:
        if not self.enabled or not location:
            return
        try:
            await self.get_inventory(location)
        except Exception:
            logger.exception('Не удалось прогреть кеш МоегоСклада для точки %s', location)


ms_client = MoySkladClient()
