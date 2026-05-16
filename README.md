<div align="center">

<img src="icon/logo.svg" alt="Scorbium Dashboard logo" width="80" height="80" />

# Scorbium Dashboard

Платформа для управления VPN-сервисом: FastAPI-панель, пользовательский кабинет, Telegram-бот и интеграция с Marzban/Pasarguard.

[Документация](docs/README.md) · [Nginx](nginx/README.md) · [Миграции](alembic/README)

</div>

## О проекте

Проект запускается как одно Python 3.13 приложение с несколькими интерфейсами:

- `/panel/` — админ-панель на FastAPI + Jinja2 + HTMX
- `/cabinet/` — пользовательский веб-кабинет
- `/api/` — REST API
- `/webhook/bot` — webhook для aiogram 3
- `/ws/notifications` — realtime-уведомления панели

В фоне работают задачи проверки оплат, истечения VPN-ключей и синхронизации с VPN-панелью.

## Стек

- Python 3.13
- FastAPI
- aiogram 3
- PostgreSQL 15
- SQLAlchemy 2 async
- Alembic
- Jinja2 + HTMX
- Docker Compose
- `uv`

## Структура

```text
app/
  api/
    panel/      админ-панель
    cabinet/    пользовательский кабинет
    v1/         REST API
  bot/          handlers и middlewares Telegram-бота
  core/         конфиг, БД, create_app()
  services/     бизнес-логика
  tasks/        фоновые циклы
  templates/    Jinja2 шаблоны
  static/       CSS/JS/изображения
alembic/        миграции
nginx/          dev/prod nginx-конфиги
```

## Быстрый старт

### Требования

- Docker
- Docker Compose v2
- Git

### Разработка

Самый простой путь — интерактивный setup:

```bash
bash setup.sh
```

Если нужна ручная последовательность:

```bash
docker compose up -d db
docker compose run --rm app uv run python fix_alembic.py
docker compose run --rm app uv run alembic upgrade head
docker compose up -d app nginx
```

Панель в dev-режиме обычно доступна по адресу [http://localhost/panel/](http://localhost/panel/).

### Продакшен

Продакшен-установка тоже идет через `setup.sh`, если нужен первичный деплой с доменом и SSL:

```bash
bash setup.sh
```

Скрипт:

- собирает `.env`
- проверяет Docker, порты и ресурсы
- генерирует `nginx/nginx.conf`
- поднимает `db`, `app`, `nginx`
- настраивает webhook URL и HTTPS-порт

## Обновление

Для продакшена используйте только:

```bash
./update.sh
```

Скрипт сам:

- делает backup базы
- проверяет пароль PostgreSQL
- выполняет `git pull --ff-only`
- регенерирует `nginx.conf`
- запускает `fix_alembic.py`
- применяет `alembic upgrade head`
- перезапускает `app` и `nginx`
- проверяет health endpoint

## Миграции

В этом проекте перед `alembic upgrade head` нужно запускать `fix_alembic.py`, иначе можно попасть в конфликт версий схемы.

Правильная последовательность:

```bash
docker compose run --rm app uv run python fix_alembic.py
docker compose run --rm app uv run alembic upgrade head
```

Подробности: [alembic/README](/Users/itsskramb/ScorbiumDashboard/alembic/README)

## CLI

У проекта есть CLI-команда `scorbium`.

Локально:

```bash
uv run scorbium
```

В контейнере:

```bash
docker compose exec app uv run scorbium
```

## Полезные команды

```bash
# Логи приложения
docker compose logs -f app

# Полный reset локальной БД
docker compose down -v
docker compose up -d db
docker compose run --rm app uv run python fix_alembic.py
docker compose run --rm app uv run alembic upgrade head
docker compose up -d app nginx

# Запуск тестов
uv run pytest -q
```

## Конфигурация

Основные переменные лежат в `.env`. Образец — [.env.example](/Users/itsskramb/ScorbiumDashboard/.env.example).

Ключевые группы:

- web: `APP_NAME`, `APP_VERSION`, `JWT_SECRET_KEY`, `WEB_SUPERADMIN_*`
- telegram: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_IDS`, `TELEGRAM_TYPE_PROTOCOL`
- database: `DB_*`
- vpn panel: `PASARGUARD_*`, `VPN_PANEL_TYPE`
- payments: provider settings are stored in the database; `CRYPTOBOT_TOKEN` can be used for initial seed
- domain/ssl: `DOMAIN`, `HTTPS_PORT`

## Что важно знать

- Контейнеры называются `vpn_app`, `vpn_db`, `vpn_nginx`
- В dev используется `nginx/nginx.local.conf`
- В prod используется `nginx/nginx.conf`, который генерируют `setup.sh` и `update.sh`
- `/health` и `/api/v1/health/` — это разные endpoint'ы
- В dev Telegram обычно работает в режиме `long`, в prod — `webhook`
