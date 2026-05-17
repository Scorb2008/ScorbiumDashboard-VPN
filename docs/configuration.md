
# Конфигурация

Все настройки хранятся в файле `.env` в корне проекта.

## Полный список переменных

### Приложение

| Переменная | Описание | Пример |
|-----------|----------|--------|
| `APP_NAME` | Название панели | `VPN Dashboard` |
| `APP_VERSION` | Версия | `1.0.0` |

### Веб-сервер

| Переменная | Описание | Пример |
|-----------|----------|--------|
| `SERVER_HOST` | Хост сервера | `0.0.0.0` |
| `SERVER_PORT` | Порт | `8000` |
| `ALLOWED_ORIGINS` | CORS origins (JSON массив) | `["https://your-domain.com"]` |

### Панель администратора

| Переменная | Описание | Пример |
|-----------|----------|--------|
| `WEB_SUPERADMIN_USERNAME` | Логин для входа | `admin` |
| `WEB_SUPERADMIN_PASSWORD` | Пароль (мин. 6 символов) | `secure_password` |

### Telegram

| Переменная | Описание | Пример |
|-----------|----------|--------|
| `TELEGRAM_BOT_TOKEN` | Токен бота от @BotFather | `1234567890:AABBcc...` |
| `TELEGRAM_ADMIN_IDS` | ID администраторов (JSON массив) | `[123456789]` |
| `TELEGRAM_TYPE_PROTOCOL` | Режим бота: `webhook` или `long` | `webhook` |
| `TELEGRAM_WEBHOOK_URL` | URL для webhook (только для webhook режима) | `https://your-domain.com/webhook/bot` |
| `TELEGRAM_WEBHOOK_PATH` | Путь webhook | `/webhook/bot` |

> ⚠️ Для продакшена используйте `webhook`. Для разработки — `long`.

### PasarGuard / Marzban

| Переменная | Описание | Пример |
|-----------|----------|--------|
| `PASARGUARD_ADMIN_PANEL` | URL панели Marzban | `https://panel.example.com:8012` |
| `PASARGUARD_ADMIN_LOGIN` | Логин администратора | `admin` |
| `PASARGUARD_ADMIN_PASSWORD` | Пароль администратора | `password` |
| `PASARGUARD_API_KEY` | API ключ (необязательно) | *(оставьте пустым)* |

> 💡 Достаточно указать логин/пароль. API ключ необязателен.

### Платёжные системы

Платёжные креденшалы больше не задаются через `.env`.

- YooKassa и остальные платёжные настройки вносятся через админ-панель
- значения сохраняются в базе данных
- после первого запуска откройте раздел `Telegram -> Платёжные системы`

### База данных

| Переменная | Описание | Пример |
|-----------|----------|--------|
| `DB_ENGINE` | Движок БД | `postgresql` |
| `DB_NAME` | Имя базы данных | `vpnbot` |
| `DB_HOST` | Хост БД | `db` (в Docker) / `localhost` |
| `DB_PORT` | Порт | `5432` |
| `DB_USER` | Пользователь | `postgres` |
| `DB_PASSWORD` | Пароль | `secure_password` |

> ⚠️ При запуске через Docker используйте `DB_HOST=db`. При локальном запуске — `DB_HOST=localhost`.

### Логирование

| Переменная | Описание | Пример |
|-----------|----------|--------|
| `LOG_PATH` | Папка для логов | `logs` |
| `LOG_ROTATION` | Ротация логов | `1 day` |
| `LOG_RETENTION` | Хранение логов | `30 days` |
| `LOG_LEVEL` | Уровень логов | `INFO` |

## Пример .env для продакшена

```env
APP_NAME=MyVPN Dashboard
APP_VERSION=1.0.0
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
ALLOWED_ORIGINS=["https://vpn.example.com"]

WEB_SUPERADMIN_USERNAME=admin
WEB_SUPERADMIN_PASSWORD=very_secure_password_123

TELEGRAM_BOT_TOKEN=1234567890:AABBccDDeeFFggHH
TELEGRAM_ADMIN_IDS=[123456789]
TELEGRAM_TYPE_PROTOCOL=webhook
TELEGRAM_WEBHOOK_URL=https://vpn.example.com/webhook/bot
TELEGRAM_WEBHOOK_PATH=/webhook/bot

PASARGUARD_ADMIN_PANEL=https://panel.example.com:8012
PASARGUARD_ADMIN_LOGIN=admin
PASARGUARD_ADMIN_PASSWORD=marzban_password
PASARGUARD_API_KEY=

DB_ENGINE=postgresql
DB_NAME=vpnbot
DB_HOST=db
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=db_secure_password

LOG_PATH=logs
LOG_ROTATION=1 day
LOG_RETENTION=30 days
LOG_LEVEL=INFO
```
