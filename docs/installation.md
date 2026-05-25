
# Установка на сервер

Полная инструкция для продакшен-деплоя на Ubuntu/Debian.

## Подготовка сервера

### 1. Обновить систему

```bash
apt-get update && apt-get upgrade -y
```

### 2. Установить Docker

```bash
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker
```

### 3. Открыть порты

```bash
ufw allow 22    # SSH
ufw allow 80    # HTTP (для SSL challenge)
ufw allow 443   # HTTPS
ufw enable
```

### 4. Настроить DNS

В панели вашего регистратора домена добавьте A-запись:
```
Тип: A
Имя: @  (или поддомен, например: vpn)
Значение: IP_ВАШЕГО_СЕРВЕРА
TTL: 300
```

Проверить что DNS обновился:
```bash
dig +short your-domain.com
# Должен вернуть IP вашего сервера
```

## Установка

### 1. Клонировать репозиторий

```bash
git clone https://github.com/Scorb2008/ScorbiumDashboard.git /opt/vpn-dashboard
cd /opt/vpn-dashboard
```

### 2. Запустить setup.sh

```bash
bash setup.sh
```

Выбрать режим **1 (Продакшен)** и заполнить все поля.

### 3. Проверить работу

```bash
# Статус контейнеров
docker compose -f docker-compose.prod.yml ps

# Логи приложения
docker compose -f docker-compose.prod.yml logs app --tail=50

# Проверить HTTPS
curl -I https://your-domain.com/<admin-path>/
```

## Ручная установка (без setup.sh)

Если хотите настроить вручную:

```bash
# 1. Скопировать .env
cp .env.example .env
nano .env  # заполнить все переменные

# 2. Настроить nginx
sed -i 's/YOUR_DOMAIN/your-domain.com/g' nginx/nginx.conf

# 3. Запустить db и app
docker compose -f docker-compose.prod.yml up -d db app

# 4. Получить SSL
apt-get install -y certbot
docker compose -f docker-compose.prod.yml stop nginx 2>/dev/null || true
certbot certonly --standalone \
  --email your@email.com --agree-tos --no-eff-email \
  -d your-domain.com

# 5. Скопировать сертификаты
mkdir -p nginx/ssl/live/your-domain.com
cp /etc/letsencrypt/live/your-domain.com/fullchain.pem nginx/ssl/live/your-domain.com/
cp /etc/letsencrypt/live/your-domain.com/privkey.pem nginx/ssl/live/your-domain.com/

# 6. Запустить nginx
docker compose -f docker-compose.prod.yml up -d nginx

# 7. Применить миграции
docker compose -f docker-compose.prod.yml exec app uv run alembic upgrade head
```

## Автообновление SSL

Сертификат Let's Encrypt действует 90 дней. Настройте автообновление:

```bash
cat > /etc/cron.d/vpn-certbot << 'EOF'
0 3 * * * root certbot renew --quiet --standalone \
  --pre-hook "docker compose -f /opt/vpn-dashboard/docker-compose.prod.yml stop nginx" \
  --post-hook "cp /etc/letsencrypt/live/your-domain.com/fullchain.pem /opt/vpn-dashboard/nginx/ssl/live/your-domain.com/ && cp /etc/letsencrypt/live/your-domain.com/privkey.pem /opt/vpn-dashboard/nginx/ssl/live/your-domain.com/ && docker compose -f /opt/vpn-dashboard/docker-compose.prod.yml start nginx"
EOF
```
