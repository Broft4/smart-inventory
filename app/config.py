from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    moysklad_token: str
    store_dmitrov: str = "Дмитров"
    store_dubna: str = "Дубна"
    
    # Базовый URL API МоегоСклада версии 1.2
    ms_api_base_url: str = "https://api.moysklad.ru/api/remap/1.2"

    class Config:
        env_file = ".env"

settings = Settings()