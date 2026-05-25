# Быстрый старт

Запустить VPN Dashboard можно за 5 минут с помощью интерактивного скрипта.

## Требования

- Ubuntu 20.04+ или Debian 11+
- Docker установлен (`curl -fsSL https://get.docker.com | sh`)
- Домен с A-записью на IP сервера

## Установка

### 1. Установить Docker

```bash
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker
```

### 2. Клонировать репозиторий

```bash
git clone https://github.com/Scorb2008/ScorbiumDashboard.git
cd ScorbiumDashboard
```

### 3. Запустить скрипт установки

```bash
bash setup.sh
```

Скрипт задаст вопросы:

```
Режим запуска:
  1) Продакшен (домен + SSL)   ← выбери это для сервера
  2) Разработка (localhost)

Название панели [VPN Dashboard]: MyVPN
Telegram Bot Token: 1234567890:AABBcc...
Telegram Admin IDs: 123456789
Логин панели [admin]: admin
Пароль панели: ваш_пароль

База данных:
  Имя БД [vpnbot]: vpnbot
  Пользователь [postgres]: postgres
  Пароль: ваш_пароль_бд

PasarGuard / Marzban:
  URL панели: https://your-panel.com:8012
  Логин: admin
  Пароль: ваш_пароль

Домен: your-domain.com
Email для SSL: your@email.com
SET_PATH_ADMIN [/x7k/panel/ или Enter для автогенерации]:
```

### 4. Готово!

После завершения скрипт выведет:

```
✅ Готово!

  🌐 Панель:   https://your-domain.com/x7k/panel/
  👤 Логин:    admin
  🔑 Пароль:   ваш_пароль
```

## Что происходит под капотом

1. Генерируется `.env` с вашими настройками
2. Запускаются контейнеры: `db`, `app`
3. Устанавливается `certbot` и получается SSL сертификат
4. Запускается `nginx` с SSL
5. Применяются миграции БД
6. Платёжные системы настраиваются позже через админ-панель и сохраняются в БД

## Следующие шаги

- [Настройка Telegram бота](telegram-bot.md)
- [Подключение Marzban](pasarguard.md)
- [Настройка платежей](payments.md)
