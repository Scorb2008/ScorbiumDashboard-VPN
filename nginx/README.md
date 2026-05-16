# Nginx в проекте

В этом репозитории nginx настраивается не вручную, а через скрипты проекта.

## Какие файлы используются

- `nginx/nginx.local.conf` — dev-конфиг для локального `docker compose`
- `nginx/nginx.generated.conf` — prod-конфиг, который генерируют скрипты
- `nginx/nginx.conf` — больше не используется в деплое и может оставаться только как справочный пример

## Кто генерирует `nginx.generated.conf`

- `setup.sh` — при первой прод-установке
- `update.sh` — при каждом обновлении

Из-за этого редактировать `nginx/nginx.generated.conf` руками обычно бессмысленно: скрипт его перезапишет.

## Какой роутинг ожидается

- `/panel/` → админ-панель
- `/api/` → REST API
- `/cabinet/` → пользовательский кабинет
- `/webhook/bot` → Telegram webhook
- `/ws/notifications` → websocket панели

## Локальная разработка

Для dev используется `docker-compose.yml`, который монтирует:

```text
./nginx/nginx.local.conf:/etc/nginx/nginx.conf
```

Обычно панель доступна на:

- [http://localhost/panel/](http://localhost/panel/)

## Продакшен

Для prod используется `docker-compose.prod.yml`, который монтирует:

```text
./nginx/nginx.generated.conf:/etc/nginx/nginx.conf
```

HTTPS-порт берется из `.env` через `HTTPS_PORT`, по умолчанию `443`.

## Опциональный Redis

Для shared rate limiting в prod можно дополнительно включить Redis profile:

```bash
docker compose -f docker-compose.prod.yml --profile redis up -d
```

Тогда в `.env` можно указать:

```text
REDIS_URL=redis://redis:6379/0
```

Если Redis не включен или недоступен, приложение автоматически откатывается на in-memory rate limiting.

## SSL

Скрипты ожидают сертификаты в одном из путей:

- `nginx/ssl/live/<domain>/fullchain.pem`
- `nginx/ssl/live/<domain>/privkey.pem`

Если сертификаты лежат в `/etc/letsencrypt/live/<domain>/`, `update.sh` умеет их оттуда скопировать.

## Что не забыть

- не редактировать `nginx.generated.conf` вручную перед обычным деплоем
- после смены домена обновить `.env`
- использовать `setup.sh` для первичной настройки и `update.sh` для обновлений
