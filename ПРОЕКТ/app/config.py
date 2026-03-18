from __future__ import annotations

from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:///./inventory.db"
    session_secret_key: str = "smart-inventory-secret-key"

    default_admin_full_name: str = "Главный администратор"
    default_admin_birth_date: str = "1990-01-01"
    default_admin_username: str = "admin"
    default_admin_password: str = "admin123"

    moysklad_token: Optional[str] = None
    store_dmitrov: str = "Дмитров"
    store_dubna: str = "Дубна"
    store_dmitrov_id: Optional[str] = None
    store_dubna_id: Optional[str] = None
    ms_api_base_url: str = "https://api.moysklad.ru/api/remap/1.2"
    ms_inventory_cache_ttl_seconds: int = 120
    ms_request_timeout_seconds: int = 30
    ms_retry_attempts: int = 4

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
