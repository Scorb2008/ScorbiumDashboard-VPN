
# FAQ — Частые вопросы

## Установка

### Панель не открывается после установки

1. Проверьте статус контейнеров:
```bash
docker compose -f docker-compose.prod.yml ps
```

2. Проверьте логи nginx:
```bash
docker compose -f docker-compose.prod.yml logs nginx --tail=20
```

3. Убедитесь что SSL сертификат получен:
```bash
ls nginx/ssl/live/your-domain.com/
# Должны быть: fullchain.pem, privkey.pem
```

4. Проверьте что домен указывает на сервер:
```bash
curl -I https://your-domain.com/<admin-path>/
```

---

### Ошибка "relation does not exist"

Не применены миграции БД:
```bash
docker compose -f docker-compose.prod.yml exec app uv run alembic upgrade head
```

---

### Бот не отвечает

1. Проверьте токен в `.env`
2. Для webhook — убедитесь что `TELEGRAM_WEBHOOK_URL` правильный
3. Проверьте логи:
```bash
docker compose -f docker-compose.prod.yml logs app | grep -i bot
```

---

### Certbot не может получить сертификат

- Убедитесь что домен указывает на IP сервера
- Порт 80 должен быть открыт: `ufw allow 80`
- Остановите nginx перед получением: `docker compose -f docker-compose.prod.yml stop nginx`

---

## Работа

### Как сменить пароль панели

Отредактируйте `.env`:
```env
WEB_SUPERADMIN_PASSWORD=new_password
```
Перезапустите: `docker compose -f docker-compose.prod.yml restart app`

---

### Как добавить нового администратора бота

В `.env`:
```env
TELEGRAM_ADMIN_IDS=[123456789, 987654321]
```
Перезапустите приложение.

---

### Как сбросить базу данных

```bash
docker compose -f docker-compose.prod.yml down -v
docker compose -f docker-compose.prod.yml up -d db app
sleep 10
docker compose -f docker-compose.prod.yml exec app uv run alembic upgrade head
```

---

### Цвета кнопок не отображаются

Функция `style` (цвет кнопок) работает только если бот купил username на [Fragment](https://fragment.com) или владелец бота имеет Telegram Premium.

---

### Marzban показывает 401

Токен авторизации истёк. Система автоматически обновляет его. Если проблема повторяется — проверьте логин/пароль в `.env`.

---

### Как настроить автопродление

Автопродление работает автоматически если:
1. У пользователя есть баланс
2. Подписка истекает

Пользователь может пополнить баланс через бота или администратор через панель.

---

## Обновление

### Как обновить без потери данных

```bash
git pull
docker compose -f docker-compose.prod.yml build app
docker compose -f docker-compose.prod.yml up -d app
docker compose -f docker-compose.prod.yml exec app uv run alembic upgrade head
```

БД и `.env` не затрагиваются.
