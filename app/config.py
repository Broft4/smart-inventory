from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    database_url: str = 'sqlite+aiosqlite:///./inventory.db'
    session_secret_key: str = 'change-me-in-production'
    default_admin_username: str = 'admin'
    default_admin_password: str = 'admin123'
    default_admin_full_name: str = 'Главный администратор'
    default_admin_birth_date: str = '1990-01-01'


settings = Settings()
