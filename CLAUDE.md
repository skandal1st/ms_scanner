# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Веб-приложение для кладовщиков — приёмка маркированных товаров в одном интерфейсе. Интеграции: **МойСклад** (поступления, продукты) и **Честный Знак** (проверка кодов маркировки, ввод в оборот). Полная исходная постановка — `Мой склад и ЧЗ Тех.задание.md`.

## Commands

Everything runs in Docker Compose. Local Python/Node installs are not needed for normal dev.

```bash
# Полный стек (postgres + redis + backend + worker + frontend)
docker compose up

# Backend / worker only (логи API)
docker compose up backend worker

# Применить миграции (новый контейнер выполнит alembic upgrade head)
docker compose run --rm backend alembic upgrade head

# Создать новую миграцию по diff моделей
docker compose run --rm backend alembic revision --autogenerate -m "описание"

# Frontend prod-билд (TS check + Vite)
docker compose run --rm frontend npm run build
```

Перед первым запуском: `cp .env.example .env` и сгенерировать `SECRET_KEY` + `ENCRYPTION_KEY` (Fernet) — команды есть в `.env.example`. Без `ENCRYPTION_KEY` шифрование токенов работает на детерминированном ключе из `SECRET_KEY` (только для разработки).

Тесты ещё не подключены — pytest-конфигурации нет.

## Architecture

### Сквозной поток сканирования
1. Frontend (`frontend/src/hooks/useScanner.ts`) шлёт `POST /scans/` с кодом → запись `Scan(status=pending)` в Postgres.
2. `backend/app/api/scans.py` ставит Celery-задачу `verify_code` (`backend/app/worker/tasks.py`).
3. Воркер вызывает `ChestnyZnakService.verify_code()`, обновляет `Scan` и публикует событие в Redis pub/sub-канал `ws:{user_id}`.
4. `redis_subscriber` в FastAPI lifespan (`backend/app/main.py`) пересылает сообщение через `WebSocketManager` подключённому фронту → `useScanner` обновляет Zustand store, играет beep.
5. `POST /documents/{id}/accept` запускает Celery-задачу `accept_document` — она вызывает `cz.accept_batch()` и `MoySkladService.update_supply()`, ставит `DocumentStatus.accepted`.

Ключевая инвариант: API-роуты **никогда** не ходят в ЧЗ/МС синхронно во время скана — только Celery. Это даёт быстрый отклик кладовщику и устойчивость к таймаутам внешних систем.

### Честный Знак: mock vs УКЭП-флоу
`ChestnyZnakService` (`backend/app/services/chestnyznak.py`) переключается флагом `CZ_MOCK_MODE`:
- **mock**: рандомные ответы с реалистичной задержкой 100–600 мс (85% valid / 10% retired / 5% not found) — это режим разработки по умолчанию, реальных API-ключей ЧЗ пока нет.
- **real (challenge-flow)**: трёхшаговая авторизация по УКЭП. (1) `POST /integrations/cz/challenge` → ЧЗ возвращает `{uuid, data}`, бэк кладёт uuid в Redis с TTL 60 сек (single-use, защита от replay). (2) фронт подписывает `data` через CryptoPro Browser Plugin (`frontend/src/lib/cprob.ts`) — приватный ключ остаётся в КриптоПро CSP клиента. (3) `POST /integrations/cz/login` обменивает подпись на `access_token`, бэк шифрует его Fernet и хранит в `Integration.cz_token` с TTL.

Воркер при истёкшем токене **не обновляет токен сам** (для нового нужна свежая подпись из браузера) — он шлёт WS-событие `cz_token_expired`, фронт показывает баннер "Войдите заново" (см. `App.tsx` Layout). Этот узор важен — не пытайтесь добавить рефреш на бэке.

Все запросы к ЧЗ логируются в `cz_logs` (`backend/app/services/cz_logger.py`); `_redact()` вырезает `signed_data` из тел `/auth/cert/`.

### МойСклад OAuth
`backend/app/api/auth.py` — `/auth/moysklad/login` и `/auth/moysklad/callback`. Стандартный OAuth code-flow, state хранится в `oauth_states` с TTL 15 минут. Колбэк находит/создаёт `User` по `accountId` из `/context/employee` и редиректит на фронт с JWT-токенами в query.

### Шифрование секретов
Все внешние токены (`Integration.moysklad_token`, `Integration.cz_token`) хранятся под Fernet (`backend/app/core/security.py`: `encrypt_token` / `decrypt_token`). Никогда не пишите plaintext-токен в БД и не возвращайте его наружу из API — `IntegrationResponse` отдаёт только булевы `has_*` и метаданные.

### Frontend
React 18 + Vite + TypeScript SPA, без CSS-фреймворка — стили инлайн через `style={...}`. Состояние: **Zustand** для скан-сессии (`store/scanStore.ts`), **React Query** для серверных данных (`hooks/useDocuments.ts`). Vite dev-сервер проксирует `/api` → backend:8000 и `/ws` → ws://backend:8000. JWT хранится в `localStorage.access_token`; axios-interceptor (`api/client.ts`) при 401 чистит токен и редиректит на `/login`.

CryptoPro плагин подключается из `frontend/public/cadesplugin_api.js` (файл из дистрибутива КриптоПро, в репозитории его нет — добавлять руками для real-ЧЗ режима).

### Celery воркеры и async
Celery-задачи синхронные, но внутри запускают asyncio через `_run(coro)` (`backend/app/worker/tasks.py`). Это работает потому что worker процесс однопоточный по задаче (`worker_prefetch_multiplier=1`, `task_acks_late=True`). При добавлении задач **не** создавайте новый event loop вручную — используйте существующий `_run`.

## Conventions

- Сообщения в логах и `error_message`/`detail` в API — на русском (это видит конечный пользователь-кладовщик). Имена идентификаторов и комментарии — английский/русский по контексту.
- Структурное логирование через `structlog` (`backend/app/core/logging.py`): `logger.info("event.name", key=value, ...)` — точечные события с keyword-полями, не f-строки.
- Pydantic v2: `model_config = {"from_attributes": True}` для ORM-конверсии, `EmailStr` для email.
- Имя миграций Alembic: `YYYYMMDD_NN_slug.py` (см. `alembic/versions/20260506_01_cz_ukep_auth.py`).
