from __future__ import annotations

from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:///./inventory.db"
    session_secret_key: str = "smart-inventory-secret-key"

    default_admin_full_name: str = "Главный управляющий"
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
    ms_max_concurrent_requests: int = 2
    ms_rate_limit_window_requests: int = 45
    ms_rate_limit_window_seconds: float = 5.0
    ms_rate_limit_remaining_threshold: int = 3
    ms_financial_cache_ttl_seconds: int = 900
    app_log_level: str = "INFO"

    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_username: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_from_email: Optional[str] = None
    smtp_from_name: str = "UCHETKA"
    smtp_starttls: bool = True
    password_reset_code_ttl_minutes: int = 10
    password_reset_max_attempts: int = 5
    password_reset_resend_cooldown_seconds: int = 60

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
