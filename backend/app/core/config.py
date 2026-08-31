from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import computed_field, model_validator
from typing import List, Literal


# Дефолтное (небезопасное) значение SECRET_KEY — в проде запрещено.
_DEFAULT_SECRET_KEY = "dev-secret-key-change-in-production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Окружение: dev (по умолчанию) | production. В production включается проверка
    # секретов (см. _guard_prod_secrets) — приложение не стартует со слабыми ключами.
    APP_ENV: str = "dev"

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

    # Мониторинг (отправка событий/таймингов в ERP Elements Platform).
    # Выключено по умолчанию; включается только заданием URL+ключа в проде.
    MONITORING_ENABLED: bool = False
    MONITORING_URL: str = ""          # напр. https://erp.example/api/v1/monitoring
    MONITORING_KEY: str = ""          # ключ проекта (совпадает с X-Api-Key в ERP)
    MONITORING_PROJECT: str = "ms_scaner"
    MONITORING_TIMEOUT: float = 2.0

    # Техподдержка: создание тикета в AXIMA ERP по кнопке «Написать в поддержку».
    # В отличие от мониторинга (fire-and-forget) — синхронный вызов с ответом: тикет
    # либо создан (возвращаем номер), либо пользователь видит явную ошибку.
    # SUPPORT_ERP_URL — внешний endpoint AXIMA: POST /api/v1/it/tickets/external.
    # Аутентификация — X-Project/X-Api-Key (тот же механизм ключей проекта, что и мониторинг).
    SUPPORT_ERP_ENABLED: bool = False
    SUPPORT_ERP_URL: str = ""         # напр. https://erp.example/api/v1/it/tickets/external
    SUPPORT_ERP_KEY: str = ""         # X-Api-Key проекта в AXIMA (обычно = MONITORING_KEY)
    SUPPORT_TIMEOUT: float = 10.0
    # Резервный e-mail поддержки — показывается пользователю в UI как запасной канал.
    SUPPORT_EMAIL: str = "support@aximatech.ru"

    # CORS
    CORS_ORIGINS: str = "http://localhost:3000"

    @computed_field
    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",")]

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.strip().lower() in ("prod", "production")

    @model_validator(mode="after")
    def _guard_prod_secrets(self) -> "Settings":
        """В production запрещаем старт со слабыми/пустыми ключами шифрования.

        Без этого при пустом ENCRYPTION_KEY все токены МС/ЧЗ шифруются ключом,
        детерминированно выведенным из SECRET_KEY (см. security._get_fernet) — то
        есть фактически восстановимым. Падаем на старте, а не молча ослабляем крипту.
        """
        if self.is_production:
            problems: List[str] = []
            if not self.SECRET_KEY or self.SECRET_KEY == _DEFAULT_SECRET_KEY:
                problems.append("SECRET_KEY не задан или равен дефолтному")
            if not self.ENCRYPTION_KEY:
                problems.append("ENCRYPTION_KEY не задан (обязателен в production)")
            if problems:
                raise ValueError(
                    "Небезопасная конфигурация при APP_ENV=production: "
                    + "; ".join(problems)
                    + ". Задайте сильные SECRET_KEY и ENCRYPTION_KEY в .env."
                )
        return self


settings = Settings()
