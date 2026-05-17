# Документация Scorbium Dashboard

Этот каталог содержит подробные инструкции по проекту. Если нужен быстрый рабочий старт, сначала открой [../README.md](/Users/itsskramb/ScorbiumDashboard/README.md).

## Разделы

| Файл | О чем |
|---|---|
| [introduction.md](introduction.md) | Общее описание и архитектура |
| [quick-start.md](quick-start.md) | Быстрый запуск |
| [installation.md](installation.md) | Развертывание на сервере |
| [configuration.md](configuration.md) | Переменные окружения |
| [telegram-bot.md](telegram-bot.md) | Настройка и поведение Telegram-бота |
| [panel.md](panel.md) | Админ-панель |
| [pasarguard.md](pasarguard.md) | Интеграция с Marzban / Pasarguard |
| [payments.md](payments.md) | Платежные провайдеры |
| [update.md](update.md) | Обновление проекта |
| [faq.md](faq.md) | Частые проблемы |

## Актуальные замечания по проекту

- Миграции нужно прогонять через `fix_alembic.py`, а потом `alembic upgrade head`
- Для продакшена основной путь обновления — `./update.sh`
- Для первой установки удобнее использовать `bash setup.sh`
- Админ-панель находится на `/panel/`, пользовательский кабинет — на `/cabinet/`

## Если документация расходится с кодом

Ориентируйся в таком порядке:

1. [AGENTS.md](/Users/itsskramb/ScorbiumDashboard/AGENTS.md)
2. [../README.md](/Users/itsskramb/ScorbiumDashboard/README.md)
3. `setup.sh` / `update.sh`
4. фактические `docker-compose*.yml`
