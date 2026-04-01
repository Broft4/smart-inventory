from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import date
from time import monotonic
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from app.config import settings


logger = logging.getLogger(__name__)

DEFAULT_CATEGORY_NAME = 'Без категории'
DEFAULT_SUBCATEGORY_NAME = 'Без подкатегории'


@dataclass(slots=True)
class CacheEntry:
    value: Any
    expires_at: float


def _normalize_location(value: str | None) -> str:
    return str(value or '').strip().title()


def _sqlite_db_path() -> str | None:
    database_url = str(settings.database_url or '').strip()
    prefix = 'sqlite+aiosqlite:///'
    if not database_url.startswith(prefix):
        return None
    raw_path = database_url[len(prefix):]
    if not raw_path:
        return None
    if raw_path.startswith('./'):
        raw_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), raw_path[2:])
    return os.path.abspath(raw_path)


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

        self._inventory_cache: dict[tuple[str, str | None], CacheEntry] = {}
        self._inventory_locks: dict[tuple[str, str | None], asyncio.Lock] = {}

        self._folders_cache: CacheEntry | None = None
        self._folders_lock = asyncio.Lock()

        self._assortment_cache: dict[str, CacheEntry] = {}
        self._assortment_locks: dict[str, asyncio.Lock] = {}

        self._stores_cache: CacheEntry | None = None
        self._stores_lock = asyncio.Lock()

        self._product_cache: dict[str, CacheEntry] = {}
        self._product_locks: dict[str, asyncio.Lock] = {}

        self._financials_by_location_cache: dict[tuple[str, str | None], CacheEntry] = {}
        self._financial_result_cache: dict[str, CacheEntry] = {}
        self._financial_result_locks: dict[str, asyncio.Lock] = {}

        self._assortment_search_cache: dict[str, CacheEntry] = {}
        self._assortment_search_locks: dict[str, asyncio.Lock] = {}

        self.max_concurrent_requests = max(1, min(4, int(settings.ms_max_concurrent_requests or 4)))
        self.financial_cache_ttl = max(120, int(settings.ms_financial_cache_ttl_seconds or 900))
        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()
        self._location_config_cache: dict[str, CacheEntry] = {}

    def _load_location_config_from_db(self, location: str) -> dict[str, str | None] | None:
        db_path = _sqlite_db_path()
        normalized = _normalize_location(location)
        if not db_path or not normalized or not os.path.exists(db_path):
            return None

        conn = None
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                'SELECT name, ms_token, ms_store_id, ms_store_name FROM location_points WHERE lower(name) = lower(?) LIMIT 1',
                (normalized,),
            ).fetchone()
        except Exception:
            logger.exception('Не удалось прочитать настройки интеграции точки %s из БД.', normalized)
            return None
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

        if row is None:
            return None
        return {
            'name': _normalize_location(row['name']),
            'token': (str(row['ms_token'] or '').strip() or None),
            'store_id': (str(row['ms_store_id'] or '').strip() or None),
            'store_name': (str(row['ms_store_name'] or '').strip() or None),
        }

    def _get_location_config(self, location: str) -> dict[str, str | None] | None:
        normalized = _normalize_location(location)
        cached = self._location_config_cache.get(normalized)
        if self._cache_alive(cached):
            return cached.value
        value = self._load_location_config_from_db(normalized)
        self._location_config_cache[normalized] = CacheEntry(value=value, expires_at=monotonic() + 120)
        return value

    def _resolve_token(self, token: str | None = None, *, location: str | None = None) -> str | None:
        explicit = str(token or '').strip()
        if explicit:
            return explicit
        if location:
            db_token = str((self._get_location_config(location) or {}).get('token') or '').strip()
            if db_token:
                return db_token
        normalized = str(settings.moysklad_token or '').strip()
        return normalized or None

    def enabled(self, token: str | None = None, *, location: str | None = None) -> bool:
        return bool(self._resolve_token(token, location=location))

    def headers(self, token: str | None = None, *, location: str | None = None) -> dict[str, str]:
        resolved_token = self._resolve_token(token, location=location)
        if not resolved_token:
            raise RuntimeError('Токен МоегоСклада не задан. Клиент МоегоСклада недоступен.')
        return {
            'Authorization': f'Bearer {resolved_token}',
            'Accept-Encoding': 'gzip',
            'Content-Type': 'application/json',
        }

    def _inventory_cache_key(self, location: str, store_id: str | None = None) -> tuple[str, str | None]:
        return _normalize_location(location), (str(store_id).strip() or None)

    def _financial_cache_prefix(self, location: str, store_id: str | None = None) -> str:
        normalized, normalized_store_id = self._inventory_cache_key(location, store_id)
        return f"{normalized.lower()}::{normalized_store_id or '-'}"

    def _build_timeout(self) -> httpx.Timeout:
        connect_timeout = min(10.0, self.request_timeout)
        return httpx.Timeout(
            connect=connect_timeout,
            read=self.request_timeout,
            write=self.request_timeout,
            pool=connect_timeout,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        client = self._client
        if client is not None and not client.is_closed:
            return client

        async with self._client_lock:
            client = self._client
            if client is not None and not client.is_closed:
                return client

            limits = httpx.Limits(
                max_connections=self.max_concurrent_requests,
                max_keepalive_connections=self.max_concurrent_requests,
            )
            self._client = httpx.AsyncClient(timeout=self._build_timeout(), limits=limits)
            return self._client

    async def aclose(self) -> None:
        client = self._client
        if client is not None and not client.is_closed:
            await client.aclose()
        self._client = None

    def _cache_alive(self, entry: CacheEntry | None) -> bool:
        return bool(entry and entry.expires_at > monotonic())

    def _extract_id_from_href(self, href: str | None) -> str | None:
        if not href:
            return None
        path = urlparse(href).path.rstrip('/')
        if not path:
            return None
        return self._normalize_entity_id(path.split('/')[-1])

    def _normalize_entity_id(self, value: Any) -> str | None:
        raw = str(value or '').strip()
        if not raw:
            return None
        raw = raw.split('?', 1)[0].split('#', 1)[0].strip().rstrip('/')
        return raw or None

    def _sanitize_meta_href(self, href: str | None) -> str | None:
        if not href:
            return None
        parsed = urlparse(href)
        sanitized = parsed._replace(query='', fragment='')
        return urlunparse(sanitized)

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
        token: str | None = None,
        location: str | None = None,
    ) -> dict[str, Any]:
        url = url_or_endpoint if absolute else f"{self.base_url}/{url_or_endpoint.lstrip('/')}"
        client = await self._get_client()

        last_error: Exception | None = None

        for attempt in range(1, self.retry_attempts + 1):
            try:
                response = await client.get(url, headers=self.headers(token, location=location), params=params)

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
                if status_code == 404:
                    logger.warning(
                        'HTTP-ошибка МойСклад %s для %s на попытке %s/%s.',
                        status_code,
                        url,
                        attempt,
                        self.retry_attempts,
                    )
                else:
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

    async def get(self, endpoint: str, params: dict[str, Any] | None = None, token: str | None = None, location: str | None = None) -> dict[str, Any]:
        return await self._request_json(endpoint, params=params, absolute=False, token=token, location=location)

    async def get_absolute(self, url: str, params: dict[str, Any] | None = None, token: str | None = None, location: str | None = None) -> dict[str, Any]:
        return await self._request_json(url, params=params, absolute=True, token=token, location=location)

    async def get_all_pages(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        token: str | None = None,
        location: str | None = None,
        *,
        page_limit: int | None = None,
    ) -> list[dict[str, Any]]:
        params = dict(params or {})
        normalized_limit = max(1, min(1000, int(page_limit or 1000)))
        params['limit'] = normalized_limit
        params['offset'] = 0
        all_rows: list[dict[str, Any]] = []

        while True:
            data = await self.get(endpoint, params=params, token=token, location=location)
            rows = data.get('rows', [])
            all_rows.extend(rows)
            if len(rows) < normalized_limit:
                break
            params['offset'] += normalized_limit

        logger.info('Получено %s записей из %s (page_limit=%s)', len(all_rows), endpoint, normalized_limit)
        return all_rows

    async def get_document_positions(
        self,
        entity_name: str,
        document_id: str,
        *,
        expand: str | None = None,
        token: str | None = None,
        location: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if expand:
            params['expand'] = expand
        page_limit = 100 if expand else 1000
        return await self.get_all_pages(
            f'entity/{entity_name}/{document_id}/positions',
            params=params,
            token=token,
            location=location,
            page_limit=page_limit,
        )

    async def populate_document_positions(
        self,
        entity_name: str,
        rows: list[dict[str, Any]],
        *,
        expand: str | None = None,
        token: str | None = None,
        location: str | None = None,
    ) -> list[dict[str, Any]]:
        if not rows:
            return rows

        semaphore = asyncio.Semaphore(self.max_concurrent_requests)

        async def enrich(row: dict[str, Any]) -> None:
            positions = row.get('positions')
            if isinstance(positions, dict) and positions.get('rows'):
                return

            document_id = str(row.get('id') or '').strip() or self._extract_id_from_href(((row.get('meta') or {}).get('href') if isinstance(row.get('meta'), dict) else None))
            if not document_id:
                return

            async with semaphore:
                loaded_rows = await self.get_document_positions(
                    entity_name,
                    document_id,
                    expand=expand,
                    token=token,
                    location=location,
                )

            meta = positions.get('meta') if isinstance(positions, dict) and isinstance(positions.get('meta'), dict) else {}
            row['positions'] = {
                'meta': {
                    **meta,
                    'size': len(loaded_rows),
                    'limit': 1000,
                    'offset': 0,
                },
                'rows': loaded_rows,
            }

        await asyncio.gather(*(enrich(row) for row in rows))
        return rows

    async def get_documents_by_period(
        self,
        entity_name: str,
        date_from: date,
        date_to: date,
        *,
        filters: list[str] | None = None,
        expand: str | None = None,
        include_positions: bool = False,
        positions_expand: str | None = None,
        token: str | None = None,
        location: str | None = None,
    ) -> list[dict[str, Any]]:
        filter_parts = [
            f'moment>={date_from.isoformat()} 00:00:00',
            f'moment<={date_to.isoformat()} 23:59:59',
        ]
        for raw_filter in filters or []:
            normalized_filter = str(raw_filter or '').strip()
            if normalized_filter:
                filter_parts.append(normalized_filter)

        params: dict[str, Any] = {'filter': ';'.join(filter_parts)}
        page_limit = 1000
        if expand:
            params['expand'] = expand
            page_limit = 100

        rows = await self.get_all_pages(
            f'entity/{entity_name}',
            params=params,
            token=token,
            location=location,
            page_limit=page_limit,
        )
        if include_positions:
            await self.populate_document_positions(
                entity_name,
                rows,
                expand=positions_expand,
                token=token,
                location=location,
            )
        return rows

    async def get_stores_ids(self, token: str | None = None, location: str | None = None) -> dict[str, str]:
        if self._cache_alive(self._stores_cache):
            return self._stores_cache.value

        async with self._stores_lock:
            if self._cache_alive(self._stores_cache):
                return self._stores_cache.value

            data = await self.get('entity/store', token=token, location=location)
            stores_mapping: dict[str, str] = {}

            for store in data.get('rows', []):
                store_name = (store.get('name') or '').strip()
                store_id = store.get('id')
                if store_name and store_id:
                    stores_mapping[store_name] = store_id

            self._stores_cache = CacheEntry(
                value=stores_mapping,
                expires_at=monotonic() + 3600,
            )
            return stores_mapping

    async def _resolve_store(self, location: str, *, token: str | None = None, store_id: str | None = None) -> tuple[str, str]:
        normalized = _normalize_location(location)
        normalized_store_id = str(store_id or '').strip() or None
        if normalized_store_id:
            return normalized, normalized_store_id

        config = self._get_location_config(normalized) or {}
        db_store_id = str(config.get('store_id') or '').strip() or None
        if db_store_id:
            return normalized, db_store_id

        if normalized.lower() == (settings.store_dmitrov or '').strip().lower():
            if settings.store_dmitrov_id:
                return normalized, settings.store_dmitrov_id
        elif normalized.lower() == (settings.store_dubna or '').strip().lower():
            if settings.store_dubna_id:
                return normalized, settings.store_dubna_id

        lookup_names = {normalized.lower()}
        db_store_name = str(config.get('store_name') or '').strip().lower()
        if db_store_name:
            lookup_names.add(db_store_name)

        stores = await self.get_stores_ids(token=token, location=normalized)
        for name, resolved_store_id in stores.items():
            if str(name or '').strip().lower() in lookup_names:
                return normalized, resolved_store_id

        raise ValueError(f'Для точки {location} не найден склад в МойСклад.')

    async def _get_folder_map(self, token: str | None = None, location: str | None = None) -> dict[str, dict[str, Any]]:
        if self._cache_alive(self._folders_cache):
            return self._folders_cache.value

        async with self._folders_lock:
            if self._cache_alive(self._folders_cache):
                return self._folders_cache.value

            rows = await self.get_all_pages('entity/productfolder', token=token, location=location)
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

    async def _get_assortment_row_by_meta(self, assortment_meta: dict[str, Any] | None, *, token: str | None = None) -> tuple[dict[str, Any] | None, str | None]:
        if not assortment_meta:
            return None, None

        href = assortment_meta.get('href')
        assortment_id = self._normalize_entity_id(assortment_meta.get('id')) or self._extract_id_from_href(href)
        cache_key = self._sanitize_meta_href(href) or assortment_id
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
                    sanitized_href = self._sanitize_meta_href(href)
                    try:
                        row = await self.get_absolute(sanitized_href or href, token=token)
                        source = 'assortment.meta.href'
                    except httpx.HTTPStatusError as exc:
                        if exc.response is None or exc.response.status_code not in {400, 404}:
                            raise
                        if not assortment_id:
                            raise
                        data = await self.get(
                            'entity/assortment',
                            params={'filter': f'id={assortment_id}', 'limit': 1},
                            token=token,
                        )
                        rows = data.get('rows') or []
                        row = rows[0] if rows else None
                        if row is None:
                            return None, None
                        source = 'assortment.filter.id'
                elif assortment_id:
                    data = await self.get(
                        'entity/assortment',
                        params={'filter': f'id={assortment_id}', 'limit': 1},
                        token=token,
                    )
                    rows = data.get('rows') or []
                    row = rows[0] if rows else None
                    if row is None:
                        return None, None
                    source = 'assortment.filter.id'
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
        *,
        token: str | None = None,
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
        assortment_row, lookup_source = await self._get_assortment_row_by_meta(assortment_meta, token=token)
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
        assortment_id = self._normalize_entity_id(assortment_meta.get('id')) or self._extract_id_from_href(assortment_meta.get('href'))
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

    async def _get_entity_by_meta(self, meta: dict[str, Any] | None, *, cache: dict[str, CacheEntry], locks: dict[str, asyncio.Lock], entity_name: str, token: str | None = None) -> tuple[dict[str, Any] | None, str | None]:
        if not meta:
            return None, None

        href = meta.get('href')
        entity_id = self._normalize_entity_id(meta.get('id')) or self._extract_id_from_href(href)
        cache_key = self._sanitize_meta_href(href) or entity_id
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
                    row = await self.get_absolute(self._sanitize_meta_href(href) or href, token=token)
                    source = f'{entity_name}.meta.href'
                elif entity_id:
                    row = await self.get(f'entity/{entity_name}/{entity_id}', token=token)
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

    async def _get_product_row_by_meta(self, product_meta: dict[str, Any] | None, *, token: str | None = None) -> tuple[dict[str, Any] | None, str | None]:
        return await self._get_entity_by_meta(product_meta, cache=self._product_cache, locks=self._product_locks, entity_name='product', token=token)

    async def _search_assortment_row_by_code(self, code: str | None, *, token: str | None = None) -> tuple[dict[str, Any] | None, str | None]:
        normalized_code = (code or '').strip()
        if not normalized_code:
            return None, None

        cache_key = f'code:{normalized_code.lower()}'
        cached = self._assortment_search_cache.get(cache_key)
        if self._cache_alive(cached):
            return cached.value, 'cache'

        lock = self._assortment_search_locks.setdefault(cache_key, asyncio.Lock())
        async with lock:
            cached = self._assortment_search_cache.get(cache_key)
            if self._cache_alive(cached):
                return cached.value, 'cache'

            row = None
            try:
                data = await self.get(
                    'entity/assortment',
                    params={
                        'filter': f'code={normalized_code}',
                        'limit': 100,
                    },
                    token=token,
                )
                rows = data.get('rows') or []
                normalized_code_lower = normalized_code.lower()
                for candidate in rows:
                    candidate_code = str(candidate.get('code') or '').strip().lower()
                    if candidate_code == normalized_code_lower:
                        row = candidate
                        break
                if row is None and rows:
                    row = rows[0]
            except httpx.HTTPError:
                logger.warning('Не удалось найти ассортимент по коду %s', normalized_code)
                return None, None

            self._assortment_search_cache[cache_key] = CacheEntry(
                value=row,
                expires_at=monotonic() + self.assortment_item_cache_ttl,
            )
            return row, 'assortment.code'

    def _extract_stock_retail_price(self, stock_row: dict[str, Any]) -> float | None:
        return self._normalize_money_value(stock_row.get('salePrice'))

    def _build_financial_seed(self, stock_row: dict[str, Any], item_id: str) -> dict[str, Any]:
        assortment_meta = (stock_row.get('assortment') or {}).get('meta') or {}
        code = (stock_row.get('code') or '').strip() or None
        return {
            'item_id': item_id,
            'code': code,
            'retail_price': self._extract_stock_retail_price(stock_row),
            'assortment_id': self._normalize_entity_id(assortment_meta.get('id')) or self._extract_id_from_href(assortment_meta.get('href')),
            'assortment_href': assortment_meta.get('href'),
        }

    def _get_financial_seed(self, location: str, item_id: str, *, store_id: str | None = None) -> dict[str, Any] | None:
        cache_key = self._inventory_cache_key(location, store_id)
        cached = self._financials_by_location_cache.get(cache_key)
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

    def _extract_price_type_name(self, entry: dict[str, Any]) -> str:
        price_type = entry.get('priceType')
        if isinstance(price_type, dict):
            return str(price_type.get('name') or price_type.get('meta', {}).get('name') or '').strip().lower()
        return str(price_type or entry.get('priceTypeName') or entry.get('name') or '').strip().lower()

    def _extract_sale_price_from_source(self, source: dict[str, Any] | None) -> float | None:
        if not source:
            return None

        sale_prices = source.get('salePrices') or []
        if isinstance(sale_prices, dict):
            sale_prices = sale_prices.get('rows') or []

        entries: list[dict[str, Any]] = [entry for entry in sale_prices if isinstance(entry, dict)]
        direct_price = self._normalize_money_value(source.get('salePrice') or source.get('price'))
        if not entries:
            return direct_price

        preferred_keywords = ('цена продажи', 'продаж', 'рознич', 'retail', 'sale')
        preferred = None
        for entry in entries:
            if self._normalize_money_value(entry) in {None, 0.0}:
                continue
            price_type = self._extract_price_type_name(entry)
            if any(keyword in price_type for keyword in preferred_keywords):
                preferred = entry
                break

        candidate = preferred or next((entry for entry in entries if self._normalize_money_value(entry) not in {None, 0.0}), None)
        if candidate is None:
            return direct_price if direct_price is not None else self._normalize_money_value(entries[0])
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

    async def get_item_financials(self, location: str, item_id: str, *, token: str | None = None, store_id: str | None = None) -> dict[str, float | None]:
        if not item_id:
            return {'cost_price': None, 'retail_price': None}

        normalized_location, normalized_store_id = self._inventory_cache_key(location, store_id)
        cache_key = f"{self._financial_cache_prefix(normalized_location, normalized_store_id)}::{item_id}"
        cached = self._financial_result_cache.get(cache_key)
        if self._cache_alive(cached):
            return cached.value

        lock = self._financial_result_locks.setdefault(cache_key, asyncio.Lock())
        async with lock:
            cached = self._financial_result_cache.get(cache_key)
            if self._cache_alive(cached):
                return cached.value

            seed = self._get_financial_seed(normalized_location, item_id, store_id=normalized_store_id)
            if seed is None:
                try:
                    await self.get_inventory(normalized_location, token=token, store_id=normalized_store_id)
                except Exception:
                    logger.exception('Не удалось прогреть инвентарь для финансов товара %s (%s)', item_id, normalized_location)
                seed = self._get_financial_seed(normalized_location, item_id, store_id=normalized_store_id)

            retail_price = seed.get('retail_price') if seed else None
            code = (seed or {}).get('code')

            assortment_meta = None
            if seed and (seed.get('assortment_href') or seed.get('assortment_id')):
                assortment_meta = {
                    'href': seed.get('assortment_href'),
                    'id': seed.get('assortment_id'),
                }
            elif item_id:
                assortment_meta = {'id': item_id}

            assortment_row, assortment_source = await self._get_assortment_row_by_meta(assortment_meta, token=token)
            product_meta = assortment_row.get('product') if isinstance((assortment_row or {}).get('product'), dict) else None
            product_row, _ = await self._get_product_row_by_meta((product_meta or {}).get('meta') if isinstance(product_meta, dict) else None, token=token)

            cost_price, fallback_retail_price = self._extract_financials_from_sources(assortment_row, product_row, product_meta)

            if cost_price is None and code:
                search_row, search_source = await self._search_assortment_row_by_code(code, token=token)
                search_product_meta = search_row.get('product') if isinstance((search_row or {}).get('product'), dict) else None
                search_product_row, _ = await self._get_product_row_by_meta((search_product_meta or {}).get('meta') if isinstance(search_product_meta, dict) else None, token=token)
                searched_cost_price, searched_retail_price = self._extract_financials_from_sources(search_row, search_product_row, search_product_meta)
                if searched_cost_price is not None:
                    cost_price = searched_cost_price
                    logger.info('Себестоимость для %s (%s) получена через поиск по коду %s', item_id, normalized_location, code)
                if fallback_retail_price is None and searched_retail_price is not None:
                    fallback_retail_price = searched_retail_price
                if assortment_row is None and search_row is not None:
                    assortment_row = search_row
                    assortment_source = search_source

            if cost_price is None:
                logger.warning(
                    'Не удалось определить себестоимость для товара %s (%s). seed=%s assortment_source=%s code=%s',
                    item_id,
                    normalized_location,
                    bool(seed),
                    assortment_source,
                    code,
                )

            resolved_retail_price = retail_price
            if resolved_retail_price in {None, 0.0}:
                resolved_retail_price = fallback_retail_price

            result = {
                'cost_price': cost_price,
                'retail_price': resolved_retail_price,
            }
            self._financial_result_cache[cache_key] = CacheEntry(
                value=result,
                expires_at=monotonic() + self.financial_cache_ttl,
            )
            return result

    async def _build_inventory(self, location: str, *, token: str | None = None, store_id: str | None = None) -> dict[str, Any]:
        normalized, resolved_store_id = await self._resolve_store(location, token=token, store_id=store_id)
        store_href = f'{self.base_url}/entity/store/{resolved_store_id}'

        folder_by_id, stock_rows = await asyncio.gather(
            self._get_folder_map(token=token, location=normalized),
            self.get_all_pages(
                'report/stock/all',
                params={'filter': f'stockMode=all;quantityMode=all;store={store_href}'},
                token=token,
            ),
        )

        categories_map: dict[str, dict[str, Any]] = {}
        financial_index: dict[str, dict[str, Any]] = {}
        semaphore = asyncio.Semaphore(self.max_concurrent_requests)

        async def prepare_stock_row(stock_row: dict[str, Any]) -> dict[str, Any]:
            item_id, item_name = self._extract_item_identity(normalized, stock_row)
            expected_qty = self._extract_expected_qty(stock_row)

            async with semaphore:
                folder_id, diagnostics = await self._extract_folder_id(stock_row, folder_by_id, token=token)

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

            return {
                'item_id': item_id,
                'item_name': item_name,
                'expected_qty': expected_qty,
                'financial_seed': self._build_financial_seed(stock_row, item_id),
                'category_id': category_id,
                'category_name': category_name,
                'subcategory_id': subcategory_id,
                'subcategory_name': subcategory_name,
                'item_diagnostics': item_diagnostics,
            }

        prepared_rows = await asyncio.gather(*(prepare_stock_row(stock_row) for stock_row in stock_rows))

        for prepared in prepared_rows:
            item_id = prepared['item_id']
            financial_index[item_id] = prepared['financial_seed']

            category_bucket = categories_map.setdefault(
                prepared['category_id'],
                {'id': prepared['category_id'], 'name': prepared['category_name'], 'subcategories': {}},
            )
            subcategory_bucket = category_bucket['subcategories'].setdefault(
                prepared['subcategory_id'],
                {'id': prepared['subcategory_id'], 'name': prepared['subcategory_name'], 'items': []},
            )
            subcategory_bucket['items'].append({
                'id': item_id,
                'name': prepared['item_name'],
                'expected_qty': prepared['expected_qty'],
                'diagnostics': prepared['item_diagnostics'],
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

        financial_cache_key = self._inventory_cache_key(normalized, resolved_store_id)
        self._financials_by_location_cache[financial_cache_key] = CacheEntry(
            value=financial_index,
            expires_at=monotonic() + self.inventory_cache_ttl,
        )

        logger.info('Для точки %s собрано %s категорий и %s товаров', normalized, len(categories), len(stock_rows))
        return {'location': normalized, 'categories': categories}

    async def get_inventory(self, location: str, *, token: str | None = None, store_id: str | None = None) -> dict[str, Any]:
        normalized, normalized_store_id = self._inventory_cache_key(location, store_id)
        cache_key = self._inventory_cache_key(normalized, normalized_store_id)
        cached = self._inventory_cache.get(cache_key)
        if self._cache_alive(cached):
            logger.debug('Inventory cache hit. location=%s', normalized)
            return cached.value

        lock = self._inventory_locks.setdefault(cache_key, asyncio.Lock())
        async with lock:
            cached = self._inventory_cache.get(cache_key)
            if self._cache_alive(cached):
                logger.debug('Inventory cache hit after lock. location=%s', normalized)
                return cached.value

            started = monotonic()
            logger.info('Inventory cache miss. Начинаем полную сборку. location=%s ttl_seconds=%s', normalized, self.inventory_cache_ttl)
            inventory = await self._build_inventory(normalized, token=token, store_id=normalized_store_id)
            self._inventory_cache[cache_key] = CacheEntry(
                value=inventory,
                expires_at=monotonic() + self.inventory_cache_ttl,
            )
            duration_ms = round((monotonic() - started) * 1000, 1)
            logger.info(
                'Inventory сохранён в кеш. location=%s categories=%s duration_ms=%s ttl_seconds=%s',
                normalized,
                len(inventory.get('categories', [])),
                duration_ms,
                self.inventory_cache_ttl,
            )
            return inventory

    def invalidate_inventory(self, location: str | None = None) -> None:
        if not location:
            self._inventory_cache.clear()
            self._inventory_locks.clear()
            self._financials_by_location_cache.clear()
            self._financial_result_cache.clear()
            self._financial_result_locks.clear()
            self._location_config_cache.clear()
            return

        normalized = _normalize_location(location)
        normalized_prefix = f"{normalized.lower()}::"
        for key in [key for key in self._inventory_cache if isinstance(key, tuple) and key[0] == normalized]:
            self._inventory_cache.pop(key, None)
            self._inventory_locks.pop(key, None)
        for key in [key for key in self._financials_by_location_cache if isinstance(key, tuple) and key[0] == normalized]:
            self._financials_by_location_cache.pop(key, None)
        for key in [key for key in self._financial_result_cache if key.startswith(normalized_prefix)]:
            self._financial_result_cache.pop(key, None)
            self._financial_result_locks.pop(key, None)
        self._location_config_cache.pop(normalized, None)

    async def prewarm_inventory(self, location: str, *, token: str | None = None, store_id: str | None = None) -> None:
        if not self.enabled(token, location=location) or not location:
            return
        try:
            await self.get_inventory(location, token=token, store_id=store_id)
        except Exception:
            logger.exception('Не удалось прогреть кеш МоегоСклада для точки %s', location)


ms_client = MoySkladClient()
