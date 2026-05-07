# Деплой и подключение МойСклад

Документ описывает (1) как зарегистрировать приложение в МойСклад и получить OAuth-credentials, (2) как развернуть сервис на боевом сервере. Локальная разработка запускается через `docker compose up` без всего этого — см. `CLAUDE.md`.

---

## 1. Регистрация приложения в МойСклад

МойСклад работает по OAuth 2.0 — пользователю нужно нажать «Подключить МойСклад» в интерфейсе нашего приложения, его редиректит на `online.moysklad.ru/oauth/authorize`, он подтверждает доступ, мы получаем `access_token`. Чтобы это работало, наше приложение должно быть зарегистрировано в кабинете разработчика МойСклад.

### Шаги

1. Зайти на **https://dev.moysklad.ru/** под аккаунтом МойСклад (нужна действующая подписка).
2. Раздел **«Мои приложения»** → **«Создать приложение»**. Тип — внешнее (Vendor App / OAuth-приложение).
3. Заполнить поля:
   - **Название** — «МС-Сканер» (или как удобно).
   - **Иконка / описание** — для каталога (можно потом).
   - **Redirect URI** — `https://<ваш-домен>/auth/callback`.
     На время разработки можно указать `http://localhost:3000/auth/callback`. Боевой URL добавляется (или заменяется) перед запуском в прод.
   - **Права (scopes)** — минимум: чтение и запись `supply` (поступления), чтение `product` (товары), чтение `employee` (для определения аккаунта в `/auth/moysklad/callback`). Точные названия скоупов — в форме регистрации МойСклад.
4. После создания МойСклад выдаёт пару:
   - `Client ID` → `MOYSKLAD_CLIENT_ID`
   - `Client Secret` → `MOYSKLAD_CLIENT_SECRET`
   Сохраните их в менеджер паролей. **Никогда** не коммитьте в git.
5. Прописать значения в `.env` сервера:
   ```bash
   MOYSKLAD_CLIENT_ID=...
   MOYSKLAD_CLIENT_SECRET=...
   MOYSKLAD_REDIRECT_URI=https://<ваш-домен>/auth/callback
   ```
6. Перезапустить backend: `docker compose up -d backend`.

### Где это используется в коде

- `backend/app/api/auth.py` — `/auth/moysklad/login` строит URL авторизации, `/auth/moysklad/callback` обменивает `code` на токен.
- `backend/app/services/moysklad.py` — обращения к API `https://api.moysklad.ru/api/remap/1.2/...` от имени пользователя.
- Полученный `access_token` шифруется Fernet-ом и хранится в `Integration.moysklad_token`.

### Тестирование подключения

1. Зарегистрироваться в нашем приложении (`/auth/register`) или зайти существующим пользователем.
2. На странице **Настройки** нажать «Подключить МойСклад» → редирект → подтверждение → возврат с токеном.
3. На странице **Приёмка** → «Выбрать документ» должен появиться список ваших поступлений из МойСклад. Если список пустой — проверьте логи backend (`docker compose logs backend`) на ошибки `httpx.HTTPStatusError`; чаще всего это нехватающий scope или истёкший токен.

---

## 2. Честный Знак

В MVP сервис работает в **mock-режиме** (`CZ_MOCK_MODE=true`) — реальные запросы к ЧЗ не идут, возвращаются синтетические ответы. Этого достаточно для демо и пилота с одним клиентом.

Для перехода на реальный ЧЗ нужно:
1. У клиента должен быть **УКЭП** (квалифицированная подпись на токене Рутокен/JaCarta) и установленный **КриптоПро CSP**.
2. На рабочих местах кладовщиков установить **КриптоПро ЭЦП Browser plug-in** + положить файл `cadesplugin_api.js` (из дистрибутива КриптоПро) в `frontend/public/`.
3. В `.env`: `CZ_MOCK_MODE=false`, `CZ_AUTH_METHOD=cprob_plugin`, `CZ_API_BASE_URL=https://markirovka.crpt.ru` (для прода) или `https://markirovka.sandbox.crptech.ru` (sandbox).
4. Пересобрать фронт и backend.

Дальнейшая авторизация идёт через challenge-flow в браузере клиента (см. раздел "Честный Знак: mock vs УКЭП-флоу" в `CLAUDE.md`). Никаких client-секретов на сервере не нужно — приватный ключ остаётся у клиента.

---

## 3. Деплой на боевой сервер

### 3.1 Требования к серверу

- Linux (Ubuntu 22.04+ / Debian 12) с root/sudo.
- 2 vCPU, 4 ГБ RAM, 20 ГБ SSD — минимум для пилота. Postgres+Redis+API+воркер+фронт уместятся.
- Открытые порты: **80**, **443** наружу. Внутренние (5432, 6379, 8000, 3000) — только в docker-сети.
- Доменное имя с A-записью на сервер (нужно для HTTPS — МойСклад **требует https** в redirect URI на проде).
- `docker` и `docker compose v2` (Docker Engine ≥ 24).

### 3.2 Подготовка `.env`

```bash
cp .env.example .env
```

Сгенерировать ключи и подставить в `.env`:

```bash
# SECRET_KEY (JWT)
python -c "import secrets; print(secrets.token_hex(32))"

# ENCRYPTION_KEY (Fernet, шифрование токенов в БД)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Заполнить:

```ini
POSTGRES_DB=ms_scaner
POSTGRES_USER=ms_scaner
POSTGRES_PASSWORD=<длинный случайный>

SECRET_KEY=<из python secrets выше>
ENCRYPTION_KEY=<из Fernet выше>

MOYSKLAD_CLIENT_ID=<из dev.moysklad.ru>
MOYSKLAD_CLIENT_SECRET=<из dev.moysklad.ru>
MOYSKLAD_REDIRECT_URI=https://your-domain.ru/auth/callback

CZ_MOCK_MODE=true            # пока в моке
CORS_ORIGINS=https://your-domain.ru
VITE_API_URL=https://your-domain.ru
VITE_WS_URL=wss://your-domain.ru
```

⚠️ **`ENCRYPTION_KEY` менять нельзя после первого запуска** — иначе все сохранённые токены МойСклад/ЧЗ перестанут расшифровываться, пользователям придётся переподключаться. Бэкапьте этот ключ отдельно от БД.

### 3.3 Production-режим Docker Compose

Текущий `docker-compose.yml` ориентирован на dev (uvicorn `--reload`, Vite dev-server). Для прода нужно:

- **Backend**: убрать `--reload` из команды, монтирование `./backend:/app` тоже убрать (код берётся из образа).
- **Frontend**: вместо `npm run dev` собрать статику (`npm run build`) и раздавать через nginx из того же docker-compose. Или вынести фронт под reverse proxy (см. ниже).
- **Postgres**: вынести `POSTGRES_PASSWORD` в `.env` (уже так), бэкап volume `postgres_data`.

Минимальный приём: создать рядом `docker-compose.prod.yml` с overrides и поднимать `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d`.

### 3.4 HTTPS через Caddy (рекомендуется)

Caddy автоматически выпускает Let's Encrypt-сертификат. Положить `Caddyfile` рядом с `docker-compose.yml`:

```caddy
your-domain.ru {
    reverse_proxy /api/* backend:8000
    reverse_proxy /ws/*  backend:8000
    reverse_proxy /*     frontend:3000
}
```

И добавить в `docker-compose.yml`:

```yaml
caddy:
  image: caddy:2-alpine
  ports:
    - "80:80"
    - "443:443"
  volumes:
    - ./Caddyfile:/etc/caddy/Caddyfile
    - caddy_data:/data
  depends_on:
    - backend
    - frontend

volumes:
  caddy_data:
```

После запуска Caddy сам получит сертификат. Альтернатива — nginx + certbot, но ручной возни больше.

### 3.5 Миграции и первый запуск

```bash
# Билд образов
docker compose build

# Поднять БД и Redis заранее
docker compose up -d postgres redis

# Применить миграции
docker compose run --rm backend alembic upgrade head

# Поднять всё
docker compose up -d
```

Проверка:

```bash
curl https://your-domain.ru/api/health   # → {"status":"ok"}
docker compose logs -f backend worker    # лог старта без ошибок
```

### 3.6 Бэкапы

Минимум — ежедневный дамп Postgres:

```bash
# В cron раз в сутки
docker compose exec -T postgres pg_dump -U ms_scaner ms_scaner | gzip > /var/backups/ms_scaner_$(date +%F).sql.gz
```

Хранить вне сервера (S3 / Yandex Object Storage). Также сохранять `.env` и `ENCRYPTION_KEY` в менеджер паролей — без них восстановление БД бесполезно (зашифрованные токены).

### 3.7 Обновление версии

```bash
git pull
docker compose build backend worker frontend
docker compose run --rm backend alembic upgrade head
docker compose up -d
```

Воркер и backend перезапускаются с новой версией; БД мигрирует до апа контейнеров. Если миграция тяжёлая — лучше выводить сервис в maintenance (отключить Caddy на время миграции).

---

## 4. Чек-лист перед запуском в прод

- [ ] Домен с DNS, открытый 443 порт.
- [ ] Сгенерированы и сохранены `SECRET_KEY` и `ENCRYPTION_KEY` (последний — в офлайн-бэкап).
- [ ] Зарегистрировано приложение в `dev.moysklad.ru`, redirect URI указан с https и продовым доменом.
- [ ] `CORS_ORIGINS`, `VITE_API_URL`, `VITE_WS_URL` указывают на боевой домен.
- [ ] Снят `--reload` с uvicorn, фронт собран как статика.
- [ ] Caddy/nginx поднят, сертификат получен (`curl -I https://...` → 200).
- [ ] `alembic upgrade head` отработал без ошибок.
- [ ] Настроен ежедневный `pg_dump` + выгрузка наружу.
- [ ] Проверен сквозной сценарий: регистрация → подключение МойСклад → выбор поступления → скан → приёмка.
