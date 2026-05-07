from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import computed_field
from typing import List, Literal


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/ms_scaner"

    # Redis & Celery
    REDIS_URL: str = "redis://localhost:6379/0"

    @computed_field
    @property
    def CELERY_BROKER_URL(self) -> str:
        return self.REDIS_URL

    @computed_field
    @property
    def CELERY_RESULT_BACKEND(self) -> str:
        return self.REDIS_URL

    # Security
    SECRET_KEY: str = "dev-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Token encryption (Fernet key, base64 encoded)
    ENCRYPTION_KEY: str = ""

    # МойСклад OAuth
    MOYSKLAD_CLIENT_ID: str = ""
    MOYSKLAD_CLIENT_SECRET: str = ""
    MOYSKLAD_REDIRECT_URI: str = "http://localhost:3000/auth/callback"
    MOYSKLAD_OAUTH_URL: str = "https://online.moysklad.ru/oauth/authorize"
    MOYSKLAD_TOKEN_URL: str = "https://online.moysklad.ru/oauth/token"
    MOYSKLAD_API_BASE: str = "https://api.moysklad.ru/api/remap/1.2"

    # Честный Знак
    CZ_MOCK_MODE: bool = True
    CZ_API_BASE_URL: str = "https://markirovka.sandbox.crptech.ru"
    CZ_AUTH_METHOD: Literal["mock", "cprob_plugin"] = "mock"
    CZ_CHALLENGE_TTL_SECONDS: int = 60

    # CORS
    CORS_ORIGINS: str = "http://localhost:3000"

    @computed_field
    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",")]


settings = Settings()
