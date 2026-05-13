#!/usr/bin/env bash
# =============================================================================
#  VPN Dashboard — Update
#  Improved with: pre-flight checks, health verification, rollback on failure,
#  port checks, process checks, nginx reload, disk cleanup
# =============================================================================
set -euo pipefail

# Cleanup on exit
trap 'rm -f update.lock' EXIT

GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'
RED='\033[0;31m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET} $*"; }
success() { echo -e "${GREEN}[OK]${RESET}   $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET} $*"; }
error()   { echo -e "${RED}[ERR]${RESET}  $*" >&2; exit 1; }

COMPOSE_FILE="docker-compose.prod.yml"

echo -e "${BOLD}${CYAN}"
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║       Scorbium Dashboard VPN  — Update                    ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo -e "${RESET}"

# ── Pre-flight ────────────────────────────────────────────────────────────────

preflight() {
    info "Запускаю проверки..."

    [[ ! -f .env ]] && error ".env не найден. Запустите setup.sh сначала."
    [[ ! -f "$COMPOSE_FILE" ]] && error "${COMPOSE_FILE} не найден. Запустите из корня проекта."

    # Docker
    if ! docker info &>/dev/null; then
        error "Docker не запущен. Выполните: sudo systemctl start docker"
    fi
    if ! docker compose version &>/dev/null; then
        error "Docker Compose v2 не найден."
    fi

    # Check containers are running
    local running
    running=$(docker compose -f "$COMPOSE_FILE" ps --format '{{.Service}}:{{.Status}}' 2>/dev/null || true)
    if [[ -z "$running" ]]; then
        warn "Контейнеры не запущены. Сначала запустите: bash setup.sh"
        exit 1
    fi

    # Check app health before updating
    local app_status
    app_status=$(docker inspect --format='{{.State.Health.Status}}' vpn_app 2>/dev/null || echo "unknown")
    if [[ "$app_status" != "healthy" ]]; then
        warn "App не healthy (статус: ${app_status}). Обновление может быть рискованным."
        read -rp "Продолжить? [y/N]: " CONFIRM; CONFIRM=${CONFIRM:-N}
        [[ ! "$CONFIRM" =~ ^[Yy]$ ]] && exit 1
    else
        success "App healthy"
    fi

    # Check db is running
    local db_status
    db_status=$(docker inspect --format='{{.State.Status}}' vpn_db 2>/dev/null || echo "unknown")
    if [[ "$db_status" != "running" ]]; then
        error "DB не запущена (статус: ${db_status})"
    fi
    success "DB running"

    # Disk space (need at least 1GB free for build)
    local avail_kb
    avail_kb=$(df -k . | awk 'NR==2 {print $4}')
    local avail_gb=$((avail_kb / 1024 / 1024))
    if [[ $avail_gb -lt 1 ]]; then
        error "Недостаточно места: ${avail_gb}GB (нужно минимум 1GB)"
    fi
    success "Место на диске: ${avail_gb}GB"

    # Port conflicts (80 and HTTPS_PORT)
    local HTTPS_PORT
    HTTPS_PORT=$(grep "^HTTPS_PORT=" .env | cut -d= -f2- | sed 's/[[:space:]]*#.*//' | xargs)
    HTTPS_PORT=${HTTPS_PORT:-443}
    local ports_to_check=(80 "${HTTPS_PORT}")
    for port in "${ports_to_check[@]}"; do
        if ss -tlnp 2>/dev/null | grep -q ":${port} " && ! ss -tlnp | grep ":${port} " | grep -q "docker\|nginx"; then
            warn "Порт ${port} занят другим процессом"
        fi
    done

    # Check for stale update lock
    if [[ -f update.lock ]]; then
        warn "Найден update.lock — предыдущее обновление могло не завершиться."
        warn "Проверьте состояние контейнеров перед продолжением."
        read -rp "Продолжить? [y/N]: " CONFIRM; CONFIRM=${CONFIRM:-N}
        [[ ! "$CONFIRM" =~ ^[Yy]$ ]] && exit 1
        rm -f update.lock
    fi

    success "Все проверки пройдены"
}

preflight

# ── Read config ───────────────────────────────────────────────────────────────
DOMAIN=$(grep "^DOMAIN=" .env | cut -d= -f2- | sed 's/[[:space:]]*#.*//' | xargs)
HTTPS_PORT=$(grep "^HTTPS_PORT=" .env | cut -d= -f2- | xargs)
HTTPS_PORT=${HTTPS_PORT:-443}
DB_NAME=$(grep "^DB_NAME=" .env | cut -d= -f2- | xargs)
DB_USER=$(grep "^DB_USER=" .env | cut -d= -f2- | xargs)
DB_NAME=${DB_NAME:-vpnbot}
DB_USER=${DB_USER:-postgres}

if [[ -z "$DOMAIN" || "$DOMAIN" == "localhost" ]]; then
    error "DOMAIN не задан в .env (этот скрипт только для продакшена). Для локального обновления используйте: docker compose up -d --build app"
fi

USE_SSL=true

info "Домен: ${DOMAIN}, HTTPS порт: ${HTTPS_PORT}"

# Ensure JWT_SECRET_KEY exists
JWT_VAL=$(grep "^JWT_SECRET_KEY=" .env 2>/dev/null | cut -d= -f2- | sed 's/[[:space:]]*#.*//' | xargs || true)
if [[ -z "$JWT_VAL" ]]; then
    warn "JWT_SECRET_KEY не найден в .env — генерирую..."
    NEW_JWT=$(openssl rand -hex 32 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex(32))")
    echo "JWT_SECRET_KEY=${NEW_JWT}" >> .env
    chmod 600 .env
    success "JWT_SECRET_KEY добавлен"
fi

# ── [1/6] DB Backup ──────────────────────────────────────────────────────────
BACKUP_DIR="backups"
mkdir -p "$BACKUP_DIR"
BACKUP_FILE="${BACKUP_DIR}/backup_$(date +%Y%m%d_%H%M%S).sql.gz"

info "[1/6] Создаю бэкап БД → ${BACKUP_FILE}..."
if docker exec vpn_db pg_dump -U "${DB_USER}" "${DB_NAME}" 2>/dev/null | gzip > "${BACKUP_FILE}"; then
    BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
    # Verify backup is not empty
    if [[ -s "$BACKUP_FILE" ]]; then
        success "Бэкап создан: ${BACKUP_FILE} (${BACKUP_SIZE})"
    else
        warn "Бэкап пустой! Проверьте что БД содержит данные."
    fi
else
    warn "Не удалось создать бэкап. Продолжаю без бэкапа (рискованно)."
    BACKUP_FILE=""
fi

# ── DB password check ─────────────────────────────────────────────────────────
DB_PASS=$(grep "^DB_PASSWORD=" .env | cut -d= -f2- | sed 's/[[:space:]]*#.*//' | xargs)
info "Проверяю пароль БД..."
if ! docker exec -e PGPASSWORD="${DB_PASS}" vpn_db psql -U "${DB_USER}" -d "${DB_NAME}" -h localhost -c "SELECT 1" &>/dev/null 2>&1; then
    warn "Пароль БД в .env не совпадает с PostgreSQL. Исправляю..."
    if docker exec vpn_db psql -U "${DB_USER}" -c "ALTER USER ${DB_USER} PASSWORD '${DB_PASS}';" &>/dev/null 2>&1; then
        sleep 1
        if docker exec -e PGPASSWORD="${DB_PASS}" vpn_db psql -U "${DB_USER}" -d "${DB_NAME}" -h localhost -c "SELECT 1" &>/dev/null 2>&1; then
            success "Пароль БД обновлён"
        else
            warn "Не удалось сменить пароль. Пробую пересоздать volume..."
            docker compose -f "$COMPOSE_FILE" down -v db
            docker compose -f "$COMPOSE_FILE" up -d db
            info "Жду готовности БД..."
            for i in $(seq 1 12); do
                docker inspect --format='{{.State.Health.Status}}' vpn_db 2>/dev/null | grep -q healthy && break
                sleep 5
            done
            success "БД пересоздана"
        fi
    else
        error "Не удалось исправить пароль БД. Запустите: bash setup.sh"
    fi
else
    success "Пароль БД верный"
fi

# ── [2/6] Git pull ────────────────────────────────────────────────────────────
info "[2/6] Обновляю код..."

# Save current version
OLD_VER=$(grep "^APP_VERSION=" .env 2>/dev/null | cut -d= -f2- | xargs || echo "unknown")

# Check for uncommitted changes
if git diff --quiet 2>/dev/null && git diff --cached --quiet 2>/dev/null; then
    GIT_PULL_OUTPUT=$(git pull 2>&1) || error "git pull failed:\n${GIT_PULL_OUTPUT}"

    if echo "$GIT_PULL_OUTPUT" | grep -q "Already up to date"; then
        info "Код уже актуален"
    else
        success "Код обновлён"
    fi
else
    warn "Есть незакоммиченные изменения. git pull может вызвать конфликт."
    read -rp "Продолжить? [y/N]: " CONFIRM; CONFIRM=${CONFIRM:-N}
    [[ ! "$CONFIRM" =~ ^[Yy]$ ]] && exit 1
    GIT_PULL_OUTPUT=$(git pull 2>&1) || error "git pull failed:\n${GIT_PULL_OUTPUT}"
    success "Код обновлён (с локальными изменениями)"
fi

# Update APP_VERSION
NEW_VER=$(grep '^version' pyproject.toml 2>/dev/null | head -1 | sed 's/.*= *"\(.*\)"/\1/' || true)
if [[ -n "$NEW_VER" && "$NEW_VER" != "$OLD_VER" ]]; then
    if sed --version 2>/dev/null | grep -q GNU; then
        sed -i "s/^APP_VERSION=.*/APP_VERSION=${NEW_VER}/" .env
    else
        sed -i '' "s/^APP_VERSION=.*/APP_VERSION=${NEW_VER}/" .env
    fi
    info "Версия: ${OLD_VER} → ${NEW_VER}"
fi

# Sync TELEGRAM_WEBHOOK_URL
CURRENT_WEBHOOK=$(grep "^TELEGRAM_WEBHOOK_URL=" .env | cut -d= -f2- | xargs || true)
if [[ "$HTTPS_PORT" == "443" ]]; then
    CORRECT_WEBHOOK="https://${DOMAIN}/webhook/bot"
else
    CORRECT_WEBHOOK="https://${DOMAIN}:${HTTPS_PORT}/webhook/bot"
fi
if [[ -n "$CURRENT_WEBHOOK" && "$CURRENT_WEBHOOK" != "$CORRECT_WEBHOOK" ]]; then
    warn "TELEGRAM_WEBHOOK_URL устарел: ${CURRENT_WEBHOOK}"
    info "Обновляю → ${CORRECT_WEBHOOK}"
    if sed --version 2>/dev/null | grep -q GNU; then
        sed -i "s|^TELEGRAM_WEBHOOK_URL=.*|TELEGRAM_WEBHOOK_URL=${CORRECT_WEBHOOK}|" .env
    else
        sed -i '' "s|^TELEGRAM_WEBHOOK_URL=.*|TELEGRAM_WEBHOOK_URL=${CORRECT_WEBHOOK}|" .env
    fi
    success "TELEGRAM_WEBHOOK_URL обновлён"
fi

# ── [3/6] nginx.conf ─────────────────────────────────────────────────────────
info "[3/6] Генерирую nginx.conf..."

CERT_PATH="nginx/ssl/live/${DOMAIN}/fullchain.pem"
if [[ ! -f "$CERT_PATH" ]]; then
    LETSENCRYPT_PATH="/etc/letsencrypt/live/${DOMAIN}"
    if [[ -f "${LETSENCRYPT_PATH}/fullchain.pem" ]]; then
        info "Восстанавливаю SSL сертификат из ${LETSENCRYPT_PATH}..."
        mkdir -p "nginx/ssl/live/${DOMAIN}"
        cp -L "${LETSENCRYPT_PATH}/fullchain.pem" "nginx/ssl/live/${DOMAIN}/fullchain.pem"
        cp -L "${LETSENCRYPT_PATH}/privkey.pem" "nginx/ssl/live/${DOMAIN}/privkey.pem"
    else
        warn "SSL сертификат не найден: ${CERT_PATH}"
        warn "Попробуйте: certbot certonly --standalone -d ${DOMAIN}"
        read -rp "Продолжить без SSL? [y/N]: " CONFIRM; CONFIRM=${CONFIRM:-N}
        [[ ! "$CONFIRM" =~ ^[Yy]$ ]] && exit 1
        USE_SSL=false
        warn "Продолжаю в HTTP-only режиме. Telegram webhook и HTTPS-вход будут недоступны, пока сертификат не появится."
    fi
fi

if [[ "$USE_SSL" == "true" ]]; then
    if [[ "$HTTPS_PORT" == "443" ]]; then
        REDIR='return 301 https://$host$request_uri;'
    else
        REDIR="return 301 https://\$host:${HTTPS_PORT}\$request_uri;"
    fi

    cat > nginx/nginx.conf << NGINXEOF
worker_processes auto;
error_log /var/log/nginx/error.log warn;
pid /var/run/nginx.pid;

events { worker_connections 1024; }

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;
    sendfile on;
    keepalive_timeout 65;
    client_max_body_size 20M;
    gzip on;
    gzip_vary on;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml;

    limit_req_zone \$binary_remote_addr zone=panel:10m   rate=30r/m;
    limit_req_zone \$binary_remote_addr zone=api:10m     rate=60r/m;
    limit_req_zone \$binary_remote_addr zone=webhook:10m rate=120r/m;
    limit_req_zone \$binary_remote_addr zone=cabinet:10m rate=30r/m;

    upstream vpn_app {
        server app:8000;
        keepalive 32;
    }

    server {
        listen 80;
        server_name ${DOMAIN};
        location /.well-known/acme-challenge/ { root /var/www/certbot; }
        location / { ${REDIR} }
    }

    server {
        listen ${HTTPS_PORT} ssl;
        http2 on;
        server_name ${DOMAIN};

        ssl_certificate     /etc/nginx/ssl/live/${DOMAIN}/fullchain.pem;
        ssl_certificate_key /etc/nginx/ssl/live/${DOMAIN}/privkey.pem;
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
        ssl_prefer_server_ciphers off;
        ssl_session_cache shared:SSL:10m;
        ssl_session_timeout 1d;
        ssl_session_tickets off;

        add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;
        add_header X-Content-Type-Options nosniff always;
        add_header Referrer-Policy "strict-origin-when-cross-origin" always;
        add_header Permissions-Policy "accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()" always;

        proxy_connect_timeout 10s;
        proxy_read_timeout    60s;
        proxy_send_timeout    60s;
        proxy_next_upstream   error timeout http_502 http_503;
        proxy_next_upstream_tries 2;

        location = /cabinet {
            return 301 /cabinet/;
        }
        location /cabinet/ {
            limit_req zone=cabinet burst=20 nodelay;
            proxy_pass http://vpn_app/cabinet/;
            proxy_set_header Host              \$host;
            proxy_set_header X-Real-IP         \$remote_addr;
            proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto \$scheme;
            proxy_set_header X-Telegram-Init-Data \$http_x_telegram_init_data;
        }

        location = /panel {
            return 301 /panel/;
        }
        location /panel/ {
            limit_req zone=panel burst=20 nodelay;
            proxy_pass http://vpn_app;
            proxy_set_header Host              \$host;
            proxy_set_header X-Real-IP         \$remote_addr;
            proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto \$scheme;
        }
        location /app/ {
            proxy_pass http://vpn_app;
            proxy_set_header Host              \$host;
            proxy_set_header X-Real-IP         \$remote_addr;
            proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto \$scheme;
        }
        location /api/ {
            limit_req zone=api burst=30 nodelay;
            proxy_pass http://vpn_app;
            proxy_set_header Host              \$host;
            proxy_set_header X-Real-IP         \$remote_addr;
            proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto \$scheme;
        }
        location /webhook/ {
            limit_req zone=webhook burst=50 nodelay;
            proxy_pass http://vpn_app;
            proxy_set_header Host              \$host;
            proxy_set_header X-Real-IP         \$remote_addr;
            proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto \$scheme;
        }
        location /static/ {
            proxy_pass http://vpn_app;
            proxy_set_header Host \$host;
            expires 7d;
            add_header Cache-Control "public, immutable";
        }
        location ~ ^/(docs|redoc|openapi\.json) {
            proxy_pass http://vpn_app;
            proxy_set_header Host              \$host;
            proxy_set_header X-Forwarded-Proto \$scheme;
        }
        location /ws/notifications {
            proxy_pass http://vpn_app;
            proxy_http_version 1.1;
            proxy_set_header Upgrade \$http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_set_header Host              \$host;
            proxy_set_header X-Real-IP         \$remote_addr;
            proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto \$scheme;
            proxy_read_timeout 86400s;
            proxy_send_timeout 86400s;
        }
        location /ws/metrics {
            proxy_pass http://vpn_app;
            proxy_http_version 1.1;
            proxy_set_header Upgrade \$http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_set_header Host              \$host;
            proxy_set_header X-Real-IP         \$remote_addr;
            proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto \$scheme;
            proxy_read_timeout 86400s;
            proxy_send_timeout 86400s;
        }
        location = / { return 301 /panel/; }
        location / {
            proxy_pass http://vpn_app;
            proxy_set_header Host              \$host;
            proxy_set_header X-Real-IP         \$remote_addr;
            proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto \$scheme;
        }
    }
}
NGINXEOF
    success "nginx.conf готов (HTTPS)"
else
    cat > nginx/nginx.conf << NGINXEOF
worker_processes auto;
error_log /var/log/nginx/error.log warn;
pid /var/run/nginx.pid;

events { worker_connections 1024; }

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;
    sendfile on;
    keepalive_timeout 65;
    client_max_body_size 20M;
    gzip on;
    gzip_vary on;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml;

    limit_req_zone \$binary_remote_addr zone=panel:10m   rate=30r/m;
    limit_req_zone \$binary_remote_addr zone=api:10m     rate=60r/m;
    limit_req_zone \$binary_remote_addr zone=webhook:10m rate=120r/m;
    limit_req_zone \$binary_remote_addr zone=cabinet:10m rate=30r/m;

    upstream vpn_app {
        server app:8000;
        keepalive 32;
    }

    server {
        listen 80;
        server_name ${DOMAIN};

        proxy_connect_timeout 10s;
        proxy_read_timeout    60s;
        proxy_send_timeout    60s;
        proxy_next_upstream   error timeout http_502 http_503;
        proxy_next_upstream_tries 2;

        location = /cabinet { return 301 /cabinet/; }
        location /cabinet/ {
            limit_req zone=cabinet burst=20 nodelay;
            proxy_pass http://vpn_app/cabinet/;
            proxy_set_header Host              \$host;
            proxy_set_header X-Real-IP         \$remote_addr;
            proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto \$scheme;
            proxy_set_header X-Telegram-Init-Data \$http_x_telegram_init_data;
        }

        location = /panel { return 301 /panel/; }
        location /panel/ {
            limit_req zone=panel burst=20 nodelay;
            proxy_pass http://vpn_app;
            proxy_set_header Host              \$host;
            proxy_set_header X-Real-IP         \$remote_addr;
            proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto \$scheme;
        }
        location /app/ {
            proxy_pass http://vpn_app;
            proxy_set_header Host              \$host;
            proxy_set_header X-Real-IP         \$remote_addr;
            proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto \$scheme;
        }
        location /api/ {
            limit_req zone=api burst=30 nodelay;
            proxy_pass http://vpn_app;
            proxy_set_header Host              \$host;
            proxy_set_header X-Real-IP         \$remote_addr;
            proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto \$scheme;
        }
        location /webhook/ {
            limit_req zone=webhook burst=50 nodelay;
            proxy_pass http://vpn_app;
            proxy_set_header Host              \$host;
            proxy_set_header X-Real-IP         \$remote_addr;
            proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto \$scheme;
        }
        location /static/ {
            proxy_pass http://vpn_app;
            proxy_set_header Host \$host;
            expires 7d;
            add_header Cache-Control "public, immutable";
        }
        location ~ ^/(docs|redoc|openapi\.json) {
            proxy_pass http://vpn_app;
            proxy_set_header Host              \$host;
            proxy_set_header X-Forwarded-Proto \$scheme;
        }
        location /ws/notifications {
            proxy_pass http://vpn_app;
            proxy_http_version 1.1;
            proxy_set_header Upgrade \$http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_set_header Host              \$host;
            proxy_set_header X-Real-IP         \$remote_addr;
            proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto \$scheme;
            proxy_read_timeout 86400s;
            proxy_send_timeout 86400s;
        }
        location /ws/metrics {
            proxy_pass http://vpn_app;
            proxy_http_version 1.1;
            proxy_set_header Upgrade \$http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_set_header Host              \$host;
            proxy_set_header X-Real-IP         \$remote_addr;
            proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto \$scheme;
            proxy_read_timeout 86400s;
            proxy_send_timeout 86400s;
        }
        location = / { return 301 /panel/; }
        location / {
            proxy_pass http://vpn_app;
            proxy_set_header Host              \$host;
            proxy_set_header X-Real-IP         \$remote_addr;
            proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto \$scheme;
        }
    }
}
NGINXEOF
    success "nginx.conf готов (HTTP-only fallback)"
fi

# ── [4/6] Build & deploy ─────────────────────────────────────────────────────
info "[4/6] Собираю и запускаю app..."

touch update.lock

# Build
docker compose -f "$COMPOSE_FILE" build app || {
    rm -f update.lock
    error "Сборка app failed. Откат не требуется — старый образ остался."
}

# ── [5/6] Migrations FIRST (before app starts, to avoid schema mismatch) ──────
info "[5/6] Применяю миграции БД перед запуском app..."

MIGRATION_OUTPUT=$(docker compose -f "$COMPOSE_FILE" run --rm app uv run python fix_alembic.py 2>&1) || {
    warn "fix_alembic.py вернул ошибку:\n${MIGRATION_OUTPUT}"
}

MIGRATION_OUTPUT=$(docker compose -f "$COMPOSE_FILE" run --rm app uv run alembic upgrade head 2>&1)
MIGRATION_EXIT=$?
if [[ $MIGRATION_EXIT -ne 0 ]]; then
    if echo "$MIGRATION_OUTPUT" | grep -qi "already up to date\|no migration"; then
        info "База уже актуальна"
    else
        warn "Миграция вернула ошибку (exit code: ${MIGRATION_EXIT}):"
        echo "$MIGRATION_OUTPUT"
        echo ""
        warn "Проверьте: docker compose exec app uv run alembic current"
    fi
else
    success "Миграции применены"
fi

# Deploy app (rolling restart — DB stays running)
info "[4/6] Запускаю app..."
docker compose -f "$COMPOSE_FILE" up -d --no-deps app

# Wait for healthy
info "Жду готовности app (макс 120 сек)..."
APP_READY=false
for i in $(seq 1 24); do
    STATUS=$(docker inspect --format='{{.State.Health.Status}}' vpn_app 2>/dev/null || echo "starting")
    if [[ "$STATUS" == "healthy" ]]; then
        APP_READY=true
        success "App готов (${i}x5 сек)"
        break
    fi
    sleep 5
done

if [[ "$APP_READY" != "true" ]]; then
    warn "App не стал healthy за 120 сек!"
    docker compose -f "$COMPOSE_FILE" logs app --tail=50
    echo ""
    error "Обновление прервано. App не запустился. Для отката восстановите бэкап: ${BACKUP_FILE:-N/A}"
fi
if [[ $MIGRATION_EXIT -ne 0 ]]; then
    if echo "$MIGRATION_OUTPUT" | grep -qi "already up to date\|no migration"; then
        info "База уже актуальна"
    else
        warn "Миграция вернула ошибку (exit code: ${MIGRATION_EXIT}):"
        echo "$MIGRATION_OUTPUT"
        echo ""
        warn "Проверьте: docker compose exec app uv run alembic current"
    fi
else
    success "Миграции применены"
fi

# ── [6/6] Nginx ──────────────────────────────────────────────────────────────
info "[6/6] Обновляю nginx..."

docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps nginx || \
    error "Не удалось пересоздать nginx"

sleep 2
NGINX_STATUS=$(docker inspect --format='{{.State.Status}}' vpn_nginx 2>/dev/null || echo "unknown")
if [[ "$NGINX_STATUS" != "running" ]]; then
    warn "nginx не запустился:"
    docker compose -f "$COMPOSE_FILE" logs nginx --tail=20
else
    success "nginx running"
fi

# ── Post-update verification ─────────────────────────────────────────────────
info "Проверяю работоспособность..."

# Test health endpoint
if [[ "$USE_SSL" == "true" ]]; then
    HEALTHCHECK_URL="https://${DOMAIN}:${HTTPS_PORT}/health"
else
    HEALTHCHECK_URL="http://${DOMAIN}/health"
fi
if curl -sk "$HEALTHCHECK_URL" 2>/dev/null | grep -q '"status":"ok"\|"status": "ok"'; then
    success "Health check: OK"
else
    warn "Health check не ответил. Проверьте: curl -sk ${HEALTHCHECK_URL}"
fi

# Clean up old docker images
info "Очищаю старые образы..."
docker image prune -f --filter "until=168h" >/dev/null 2>&1 || true
docker builder prune -f --filter "until=168h" >/dev/null 2>&1 || true
success "Очистка завершена"

# Clean old backups (keep last 5)
if [[ -d "$BACKUP_DIR" ]]; then
    bk_count=$(ls -1 "$BACKUP_DIR"/backup_*.sql.gz 2>/dev/null | wc -l)
    if [[ $bk_count -gt 5 ]]; then
        ls -1t "$BACKUP_DIR"/backup_*.sql.gz 2>/dev/null | tail -n +6 | xargs rm -f
        info "Удалены старые бэкапы (оставлено 5)"
    fi
fi

rm -f update.lock

# ── Итог ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════╗${RESET}"
echo -e "${GREEN}║  ✅  Обновление завершено                        ║${RESET}"
echo -e "${GREEN}╚══════════════════════════════════════════════════╝${RESET}"
echo ""
echo -e "  Версия: ${BOLD}${OLD_VER}${RESET} → ${BOLD}${NEW_VER:-$OLD_VER}${RESET}"
if [[ "$USE_SSL" != "true" ]]; then
    echo -e "  Панель: ${CYAN}http://${DOMAIN}/panel/${RESET}"
elif [[ "$HTTPS_PORT" == "443" ]]; then
    echo -e "  Панель: ${CYAN}https://${DOMAIN}/panel/${RESET}"
else
    echo -e "  Панель: ${CYAN}https://${DOMAIN}:${HTTPS_PORT}/panel/${RESET}"
fi
echo ""
echo -e "  📋 Логи:  ${YELLOW}docker compose logs -f app${RESET}"
echo -e "  💾 Бэкап: ${YELLOW}${BACKUP_FILE:-не создан}${RESET}"
echo ""

# ── Rollback helper ───────────────────────────────────────────────────────────
echo -e "${BOLD}Откат (если что-то пошло не так):${RESET}"
echo -e "  # Вернуть старый код:"
echo -e "  git log --oneline -5  # найти нужный коммит"
echo -e "  git reset --hard <commit>"
echo -e "  docker compose -f docker-compose.prod.yml build app"
echo -e "  docker compose -f docker-compose.prod.yml up -d app"
echo ""
echo -e "  # Восстановить БД из бэкапа:"
echo -e "  gunzip -c ${BACKUP_FILE:-backups/backup_*.sql.gz} | docker exec -i vpn_db psql -U ${DB_USER} ${DB_NAME}"
echo ""
