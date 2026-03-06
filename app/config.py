from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    moysklad_token: str | None = None
    store_dmitrov: str = "Дмитров"
    store_dubna: str = "Дубна"
    ms_api_base_url: str = "https://api.moysklad.ru/api/remap/1.2"
    database_url: str = "sqlite+aiosqlite:///./inventory.db"

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
