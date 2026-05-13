# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Веб-приложение для кладовщиков — приёмка и отгрузка маркированных товаров в одном интерфейсе. Интеграции:
- **МойСклад** — серверное приложение из каталога решений (Vendor API): чтение поступлений/отгрузок/списаний/возвратов, запись `trackingCodes` в позиции документа.
- **Честный Знак** — проверка кодов маркировки, ввод в оборот при приёмке (УКЭП-флоу).

Полная исходная постановка — `Мой склад и ЧЗ Тех.задание.md`. Прод — `https://skandata.ru/` (VPS Ubuntu 24.04). Тесты ещё не подключены, pytest-конфигурации нет.

**Коды маркировки в JSON API МойСклад** (сущность `trackingCodes`, поля `cis` / `cis_1162`, параметр `codetype` при GET: `gs1`, `tag_1162`, `all`) — выдержка в корне репозитория: `Markirovka.md`.

## Commands

Dev: всё в Docker Compose, локальные Python/Node не нужны.
```bash
docker compose up                                         # postgres + redis + backend + worker + frontend
docker compose run --rm backend alembic upgrade head      # миграции
docker compose run --rm backend alembic revision --autogenerate -m "..."
docker compose run --rm frontend npm run build            # TS-чек + Vite билд
```

Перед первым запуском: `cp .env.example .env` и сгенерировать `SECRET_KEY` + `ENCRYPTION_KEY` (Fernet). Без `ENCRYPTION_KEY` — детерминированный ключ из `SECRET_KEY` (только dev).

Прод-деплой (`docker-compose.prod.yml` + Caddy + LE):
```bash
ssh root@185.197.75.195 'cd /root/ms_scanner && git pull --ff-only origin main \
  && docker compose -f docker-compose.prod.yml build backend worker frontend \
  && docker compose -f docker-compose.prod.yml run --rm backend alembic upgrade head \
  && docker compose -f docker-compose.prod.yml up -d --force-recreate backend worker frontend'
```
Важно: при изменении дескриптора `moysklad-descriptor.xml` или scope в `<access><permissions>` — нужно вручную **перезалить XML в `dev.moysklad.ru` и переустановить решение** на тестовом аккаунте, иначе Vendor API не выдаст обновлённый MC-токен.

## Architecture

### Единый поток для приёмки и отгрузки (`Document.kind`)
`Document.kind` — enum `supply | demand | loss | salesreturn` (`move` исключён: XSD-схема дескриптора v2 не разрешает `<update/>` для перемещений через `scope=custom`).

Все типы документов идут через один endpoint `POST /documents/{id}/process` и одну Celery-задачу `process_document_task`. Внутри ветка по `kind`:
- `supply` (приёмка) → `cz.accept_batch()` + `MoySkladService.update_document("supply", ...)`. Маркировка вводится в оборот через ЧЗ; в МС-документе `trackingCodes` не пишутся.
- `demand | loss | salesreturn` (отгрузка) → только `MoySkladService.update_document(kind, ...)`. ЧЗ-API не вызывается; КМ в МС: при существующих позициях — `POST .../positions/{id}/trackingCodes`, иначе вложенно в `PUT` документа. См. `WRITE_TRACKING_CODES_KINDS` в `backend/app/services/moysklad.py` и `Markirovka.md` в корне репозитория.

`/documents/{id}/accept` оставлен как backward-совместимый alias на `/process`.

### Сквозной поток сканирования
1. Frontend (`hooks/useScanner.ts`) парсит код. SSCC-короб (20 цифр на `00`) → `POST /scans/box`; обычный KM (`01`+GTIN) → `POST /scans/`.
2. `/scans/box` зовёт `ChestnyZnakService.unpack_box(sscc, plan_gtins)` (mock генерирует 3-5 KM с GTIN из `Document.plan`), создаёт массив сканов, на каждый кикает `verify_code_task`.
3. На обычном `POST /scans/`: вставка с unique-индексом `(document_id, code)` — при `IntegrityError` возвращаем существующий со `status=duplicate`.
4. `verify_code_task` зовёт `cz.verify_code()`, обновляет `Scan`. Если документ имеет план и для GTIN валидных уже `>= expected_qty` — текущий скан помечается **`overflow`** (визуально красный, но при `process` уходит в МС вместе с `valid`). Публикует событие в Redis pub/sub `ws:{user_id}`.
5. `redis_subscriber` в FastAPI lifespan (`backend/app/main.py`) пересылает через `WebSocketManager` фронту → `useScanner` обновляет Zustand, играет beep.
6. `process_document_task`: фильтрует сканы `status IN (valid, overflow)`. Для отгрузки ищет `product_id` по уникальным GTIN'ам через `find_product_by_gtin` на лету (в `Scan` не хранится); для приёмки — стандартный flow ЧЗ + МС.

Инвариант: API-роуты **никогда** не ходят в ЧЗ/МС синхронно во время скана — только Celery. Быстрый отклик + устойчивость к таймаутам внешних систем.

### План сборки (`Document.plan`)
JSONB-массив `[{gtin, product_id, product_name, expected_qty}]` в `Document.plan`. Заполняется при `POST /documents/` если передан `moysklad_id+kind`: бэк зовёт `MoySkladService.build_plan(kind, doc_id)` — один запрос с `expand=positions.assortment` (нет N+1). `POST /documents/{id}/refresh-plan` для ручного обновления.

Если план пустой (документ создан вручную, без МС-привязки) — `ProgressTable` на фронте не показывается, поведение «свободная сборка». Если план есть — `ProgressTable` показывает прогресс по GTIN, кнопка «Отгрузить N/M [+ K сверх]». Логика подсчёта в `scanStore.getProgress()` — отдельные счётчики для plan и overflow.

### МС интеграция: Vendor API + iframe-лаунчер + новая вкладка
Архитектурное решение (вариант B из ТЗ): iframe внутри МС играет роль **лаунчера** (приветствие, settings, кнопки), а сам поток сканирования открывается в **отдельной вкладке** через одноразовый `launch_token`. Не превращайте iframe в полноценное приложение: USB-сканер требует фокуса, в iframe МС теряет его при любом клике мимо; 3rd-party storage partitioning ломает `localStorage`. См. `memory/project_ms_iframe_architecture.md`.

Поток:
1. МС открывает `https://skandata.ru/ms?contextKey=…` в iframe.
2. `MsIframePage` шлёт `POST /auth/ms-launch {contextKey}`. Бэк через Vendor JWT (`_build_vendor_jwt`) идёт за контекстом в МС, находит `Integration` по `accountId`, кладёт `launch_token` (`secrets.token_urlsafe(32)`) в Redis под ключом `ms_launch:<token>` с TTL 60 сек, в ответе отдаёт ещё и обычный JWT для работы Settings внутри iframe.
3. iframe рендерит `<SettingsPage embedded />` (без ручного ввода МС-токена) + две CTA: «📥 Начать приёмку» и «📤 Начать отгрузку». По клику — `window.open('/launch?t=…&mode=…','_blank')` без `noopener` (чтобы потом сработал `window.close()`).
4. `LaunchPage` в новой вкладке шлёт `POST /auth/launch {launch_token}` → атомарный `GETDEL` в Redis → JWT-пара → localStorage → `navigate('/')` или `'/shipment'` по `?mode=`.
5. После «Принять/Отгрузить товары» в новой вкладке: если `window.opener` есть — оверлей «Готово» и `window.close()` через 1.2с.

Vendor API endpoints (`backend/app/api/moysklad_vendor.py`, `/moysklad/vendor/1.0/apps/{app_id}/{account_id}`):
- `PUT` — активация (`vendor.activate`): создаёт `User + Integration` или обновляет токен.
- `DELETE` — деактивация: обнуляет `Integration.moysklad_token`, остальное (User, Document, Scan) сохраняется (требование п.12 регламента модерации).
- `GET` — статус.

JWT МС→Разработчик в этих endpoints проверяется через `_verify_vendor_jwt`: HS256 от `MOYSKLAD_VENDOR_SECRET_KEY`, защита от replay через `jti` в Redis, идемпотентность по `X-Lognex-RequestId`.

OAuth-флоу `/auth/moysklad/login` + `LoginPage` оставлены как fallback для прямого захода на `skandata.ru` вне МС-каталога — не удалять.

### Честный Знак: mock vs УКЭП-флоу
`ChestnyZnakService` (`backend/app/services/chestnyznak.py`) переключается флагом `CZ_MOCK_MODE`:
- **mock**: рандомные ответы с задержкой 100-600 мс (85% valid / 10% retired / 5% not found). Реальных ключей ЧЗ пока нет.
- **real (challenge-flow)**: трёхшаговая авторизация по УКЭП. (1) `POST /integrations/cz/challenge` → ЧЗ возвращает `{uuid, data}`, бэк кладёт uuid в Redis с TTL 60 сек (single-use). (2) фронт подписывает `data` через CryptoPro Browser Plugin (`frontend/src/lib/cprob.ts`) — приватный ключ остаётся в CSP клиента. (3) `POST /integrations/cz/login` обменивает подпись на `access_token`, бэк шифрует Fernet, кладёт в `Integration.cz_token`.

Воркер при истёкшем токене **не обновляет токен сам** (для нового нужна свежая подпись из браузера) — шлёт WS-событие `cz_token_expired`, фронт показывает баннер «Войдите заново» (см. Layout в `App.tsx`). Не добавлять рефреш на бэке.

Все запросы к ЧЗ логируются в `cz_logs` (`backend/app/services/cz_logger.py`); `_redact()` вырезает `signed_data` из тел `/auth/cert/`.

### Шифрование секретов
Все внешние токены (`Integration.moysklad_token`, `Integration.cz_token`, `Integration.moysklad_app_token` из Vendor API) хранятся под Fernet (`backend/app/core/security.py`: `encrypt_token` / `decrypt_token`). Никогда не писать plaintext-токен в БД и не возвращать наружу из API — `IntegrationResponse` отдаёт только булевы `has_*` и метаданные.

### Frontend
React 18 + Vite + TS SPA, без CSS-фреймворка — стили inline через `style={...}` или CSS-классы из `index.css`. Состояние: **Zustand** для скан-сессии (`store/scanStore.ts`), **React Query** для серверных данных (`hooks/useDocuments.ts`). Vite dev-сервер проксирует `/api` → backend:8000 и `/ws` → ws://backend:8000. JWT в `localStorage.access_token`; axios-interceptor (`api/client.ts`) при 401 чистит токен и редиректит на `/login`.

Роуты:
- `/` → `AcceptancePage` (приёмка, kind=supply)
- `/shipment` → `ShipmentPage` (переключатель demand/loss/salesreturn)
- `/settings` → `SettingsPage` (привязка МС/ЧЗ)
- `/login` → `LoginPage` (fallback OAuth)
- `/ms` → `MsIframePage` (лаунчер для iframe МС, использует embedded Settings)
- `/launch` → `LaunchPage` (обмен `?t=launch_token` на JWT)

`MsIframePage` и `LaunchPage` ходят на `/api/auth/ms-launch` и `/api/auth/launch` через **прямой `fetch`**, минуя axios-interceptor — иначе 401 при просроченном launch_token увёл бы пользователя на `/login` вместо понятного сообщения.

CryptoPro плагин подключается из `frontend/public/cadesplugin_api.js` (файл из дистрибутива КриптоПро, в репозитории его нет — добавлять руками для real-ЧЗ).

### Celery воркеры и async
Celery-задачи синхронные, но внутри запускают asyncio через `_run(coro)` (`backend/app/worker/tasks.py`). Работает потому что worker процесс однопоточный по задаче (`worker_prefetch_multiplier=1`, `task_acks_late=True`). При добавлении задач **не** создавать новый event loop вручную — использовать существующий `_run`.

## Дескриптор и регламент модерации МС

`moysklad-descriptor.xml` — XSD-схема `application-v2.xsd` (см. описание в `Documents/Obsidian Vault/Дескриптор.md`). Тонкие места:
- Имена тегов permissions — **camelCase**: `<salesReturn>`, `<purchaseReturn>`, `<invoiceIn>`. Не путать с lowercase URL-сегментами в МС API (`/entity/salesreturn`).
- У каждого типа свой набор разрешённых действий. У `move` нет `<update/>` через `scope=custom` — поэтому `move` исключён из `SUPPORTED_KINDS` в `moysklad.py` и из `DocumentKind` на фронте.
- `<scope>custom</scope>` — обязательно. `admin` нарушает п.5 регламента модерации (минимум прав).

Регламент полностью — `Downloads/apps-regulations.pdf`. Закрытые пункты: 1, 2, 5, 6 (Settings внутри iframe), 7, 11, 12. Открытые: 4в (инструкция), 9 (кнопки «Отвязать МС/ЧЗ» в Settings), 10 (прогон ошибок на русском), карточка решения (описание, тариф, триал).

## Conventions

- Сообщения в логах и `error_message`/`detail` в API — на **русском** (видит конечный пользователь-кладовщик). Имена идентификаторов и комментарии — английский/русский по контексту.
- Структурное логирование через `structlog` (`backend/app/core/logging.py`): `logger.info("event.name", key=value, ...)` — точечные события с keyword-полями, не f-строки. `event.name` — иерархия точкой (`vendor.activate`, `ms_launch.issued`, `process_document.done`).
- Pydantic v2: `model_config = {"from_attributes": True}` для ORM-конверсии, `EmailStr` для email.
- Имя миграций Alembic: `YYYYMMDD_NN_slug.py`. Postgres enum-значения добавляются через `op.execute("ALTER TYPE … ADD VALUE …")` (см. `20260509_03_overflow_status.py`); удалить значение из enum нельзя без пересоздания типа — downgrade оставляем noop.
- Память (`C:\Users\sanch\.claude\projects\C--code-Projects-ms-scaner\memory\`) — там зафиксированы важные архитектурные решения (вариант B для МС, статус деплоя). Перед перепроектированием — заглядывать туда.
