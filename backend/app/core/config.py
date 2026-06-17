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

    # МойСклад Vendor API (входящие callbacks от МойСклада к нам)
    # Заполняются после создания черновика решения в dev.moysklad.ru
    MOYSKLAD_APP_UID: str = ""              # алиас_решения.алиас_разработчика
    MOYSKLAD_VENDOR_SECRET_KEY: str = ""    # для проверки JWT-подписи
    MOYSKLAD_VENDOR_JWT_MAX_LIFETIME: int = 300  # секунд
    MOYSKLAD_VENDOR_BASE: str = "https://apps-api.moysklad.ru/api/vendor/1.0"

    # Честный Знак
    CZ_MOCK_MODE: bool = False
    CZ_API_BASE_URL: str = "https://markirovka.crpt.ru"
    CZ_AUTH_METHOD: Literal["mock", "cprob_plugin"] = "cprob_plugin"
    CZ_CHALLENGE_TTL_SECONDS: int = 60
    # Товарные группы (pg) для True API cises/info — перебор при детекте агрегата (блока).
    # pg обязателен и зависит от группы товара; перебираем до первого ответа 200.
    CZ_PRODUCT_GROUPS: str = "otp,tobacco,ncp"

    @computed_field
    @property
    def cz_product_groups_list(self) -> List[str]:
        return [g.strip() for g in self.CZ_PRODUCT_GROUPS.split(",") if g.strip()]

    # CORS
    CORS_ORIGINS: str = "http://localhost:3000"

    @computed_field
    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",")]


settings = Settings()
