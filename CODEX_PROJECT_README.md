# Codex Project README

Рабочая памятка по Scorbium Dashboard для быстрых будущих правок.

## Суть проекта

Один Python 3.13 процесс совмещает FastAPI-панель, REST API, пользовательский кабинет и Telegram-бот на aiogram 3. Данные в PostgreSQL через async SQLAlchemy. Пакетный менеджер и запуск команд: `uv`.

Точка входа: `main.py` -> `app.core.server:create_app()`.

## Запуск и миграции

Локальный стек:

```bash
docker compose up -d db
docker compose run --rm app uv run python fix_alembic.py
docker compose run --rm app uv run alembic upgrade head
docker compose up -d app nginx
```

Важно: перед `alembic upgrade head` всегда запускать `fix_alembic.py`.

Если `uv` в локальном sandbox не может писать в `~/.cache/uv`, использовать временный кеш:

```bash
UV_CACHE_DIR=/private/tmp/uv-cache uv run ...
```

## Основная архитектура

- `app/core/server.py` собирает FastAPI, lifespan, middleware, webhook/polling бота и фоновые задачи.
- `app/core/config.py` даёт singleton `config`; импортировать как `from app.core.config import config`.
- `app/core/database.py` содержит async engine/session.
- `app/api/v1/` — REST API.
- `app/api/panel/routes/` — админ-панель, модульные роуты.
- `app/api/cabinet/` — пользовательский кабинет через Telegram Login Widget.
- `app/bot/handlers/` — aiogram handlers.
- `app/bot/keyboards/main.py` строит главное меню из `bot_settings.keyboard_layout`.
- `app/services/` — бизнес-логика, платежки, VPN, i18n, настройки бота.
- `app/tasks/` — фоновые циклы оплат, истечения ключей и синхронизации VPN.
- `app/templates/` — Jinja2 + HTMX шаблоны панели и кабинета.
- `alembic/versions/` — миграции.

## Панель Telegram

Главная страница настроек: `app/api/panel/routes/telegram.py` + `app/templates/telegram.html`.

Вкладки в `telegram.html`:

- `tab-bot` — инфо бота, включение, админы, рефералка, прямые сообщения.
- `tab-customize` — внешний вид, пробный период, уведомления, тексты, URL кабинета/панели.
- `tab-buttons` — редактор клавиатуры.
- `tab-photos` — фото разделов бота.
- `tab-languages` — язык по умолчанию и строки i18n.
- `tab-channel` — обязательная подписка на канал.
- `tab-payments` — YooKassa, CryptoBot, FreeKassa, AiKassa, Platega, PayPalych, Stars.

Редактор клавиатуры также существует отдельной страницей:

- route: `app/api/panel/routes/keyboard.py`
- template: `app/templates/keyboard_editor.html`
- save: `POST /panel/keyboard/save`
- styles: `POST /panel/keyboard/styles`

Список доступных кнопок и дефолтная раскладка живут в `app/api/panel/routes/shared.py` как `_ALL_BUTTONS` и `_DEFAULT_LAYOUT`.

## Bot settings

Сервис: `app/services/bot_settings.py`.

`BotSettingsService.get_all()` объединяет `DEFAULTS` и строки из таблицы `bot_settings`, затем кеширует на 300 секунд. `set()` сбрасывает кеш. Чувствительные ключи шифруются через `app/services/encryption.py`.

Частые ключи:

- `keyboard_layout`
- `welcome_message`
- `bot_language`
- `required_channel_id`, `required_channel_name`
- `photo_*`
- `ps_*_enabled`
- `*_token`, `*_secret*`
- `btn_style_*`

## Бот

`app/core/server.py:_make_dp()` динамически reload-ит handler modules перед сборкой Dispatcher, потому что aiogram Router singleton нельзя повторно attach-ить.

Главные handlers:

- `start.py` — стартовое меню.
- `buy.py` — выбор тарифа и платежки.
- `payments.py` — подтверждение оплат и выдача ключей.
- `my_keys.py` — подписки пользователя.
- `profile.py`, `features.py`, `language.py`, `trial.py`, `admin.py`.

Middleware:

- `BanCheck`
- `Throttle`
- `ChannelCheck`
- `UserNotify`
- metrics middleware

## Платежи

Внутренняя модель: `app/models/payment.py`, сервис: `app/services/payment.py`.

Провайдеры:

- `yookassa.py`
- `cryptobot.py`
- `freekassa.py`
- `aikassa.py`
- `platega.py`
- `paypalych.py`
- `telegram_stars.py`

UI настройки платежек находятся в `telegram.html`, endpoints — в `app/api/panel/routes/telegram.py`.

## VPN

Интерфейс: `app/services/vpn_panel_interface.py`.

Pasarguard/Marzban реализация:

- `app/services/pasarguard/pasar_auth.py`
- `app/services/pasarguard/pasarguard.py`

Ключи/подписки:

- `app/services/vpn_key.py`
- `app/services/subscription.py`
- `app/models/vpn_key.py`

## Безопасность панели

Auth helpers в `app/api/panel/routes/shared.py`:

- `_require_auth`
- `_require_permission`
- `_base_ctx`

Роли/права: `app/core/permissions.py`.

Сессия панели хранится в cookie `vpn_session`. CSRF token прокидывается в шаблоны через `csrf_token`.

## Тесты и проверки

В проекте есть pytest-тесты:

- `tests/test_payment_service.py`
- `tests/test_user_service.py`
- `tests/test_referral_service.py`

Запуск:

```bash
UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest
```

Для быстрой проверки Jinja-шаблонов можно рендерить их через `jinja2.Environment` с dummy context и проверять извлечённый JS через `node --check`.

## Заметка по багу вкладок Telegram

Если вкладки `Кнопки`, `Фото`, `Языки`, `Канал`, `Платёжные системы` не показываются, сначала проверить баланс HTML в `app/templates/telegram.html`. Уже был баг: блок `tab-customize` не закрывался перед `tab-buttons`, поэтому последующие вкладки становились вложенными в скрытую кастомизацию.
