#!/usr/bin/env bash
# =============================================================================
#  VPN Dashboard — Setup & Deploy
#  Improved with: pre-flight checks, port validation, Docker health, .env backup
# =============================================================================
set -euo pipefail

# Cleanup on exit
trap 'rm -f setup.lock' EXIT

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET} $*"; }
success() { echo -e "${GREEN}[OK]${RESET}   $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET} $*"; }
error()   { echo -e "${RED}[ERR]${RESET}  $*" >&2; exit 1; }

require_cmd() {
    command -v "$1" &>/dev/null || error "Не найдена команда '$1'"
}

diagnose_db_failure() {
    echo ""
    warn "Возникла проблема с подключением к PostgreSQL."
    echo "  Проверьте:"
    echo "  1) DB_HOST=db и DB_PORT=5432 в .env"
    echo "  1.1) если внешний порт занят — задайте другой DB_EXTERNAL_PORT (например 5433)"
    echo "  2) DB_USER / DB_PASSWORD совпадают с уже существующим volume PostgreSQL"
    echo "  3) контейнер БД healthy: docker inspect vpn_db"
    echo "  4) логи БД: docker compose -f docker-compose.prod.yml logs db --tail=40"
    echo ""
    echo "  Если пароль в .env изменили после первого запуска:"
    echo "     docker compose -f docker-compose.prod.yml down -v"
    echo "     bash setup.sh"
    echo ""
}

wait_for_health() {
    local container="$1"
    local label="$2"
    local attempts="$3"
    local delay="$4"
    local status

    info "Жду готовности ${label}..."
    for i in $(seq 1 "${attempts}"); do
        status="$(docker inspect --format='{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${container}" 2>/dev/null || echo "starting")"
        if [[ "$status" == "healthy" || "$status" == "running" ]]; then
            success "${label} готов"
            return 0
        fi
        sleep "${delay}"
    done

    return 1
}

run_prod_migrations() {
    local dc="$1"
    info "Применяю миграции БД..."
    if ! docker compose -f "$dc" run --rm app uv run python fix_alembic.py; then
        warn "fix_alembic.py завершился с ошибкой"
        diagnose_db_failure
        docker compose -f "$dc" logs db --tail=40 2>/dev/null || true
        return 1
    fi
    if ! docker compose -f "$dc" run --rm app uv run alembic upgrade head; then
        warn "alembic upgrade head завершился с ошибкой"
        diagnose_db_failure
        docker compose -f "$dc" logs db --tail=40 2>/dev/null || true
        return 1
    fi
    success "Миграции применены"
}

NGINX_GENERATED_CONF="nginx/nginx.generated.conf"

prepare_generated_nginx_conf() {
    mkdir -p "$(dirname "$NGINX_GENERATED_CONF")"
    if [[ -d "$NGINX_GENERATED_CONF" ]]; then
        warn "${NGINX_GENERATED_CONF} оказался директорией. Удаляю и пересоздаю как файл..."
        rm -rf "$NGINX_GENERATED_CONF"
    fi
    : > "$NGINX_GENERATED_CONF"
}

echo -e "${BOLD}${CYAN}"
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║       Scorbium Dashboard VPN  — Setup & Deploy            ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo -e "${RESET}"

# ── Pre-flight checks ────────────────────────────────────────────────────────

preflight_checks() {
    info "Запускаю проверки..."

    # Docker
    if ! command -v docker &>/dev/null; then
        error "Docker не установлен. https://docs.docker.com/get-docker/"
    fi
    if ! docker info &>/dev/null; then
        error "Docker не запущен. Выполните: sudo systemctl start docker"
    fi

    # Docker Compose v2
    if ! docker compose version &>/dev/null; then
        error "Docker Compose v2 не найден. Обновите Docker."
    fi

    # Required tools
    for cmd in curl openssl python3; do
        require_cmd "$cmd"
    done

    # Disk space (need at least 2GB free)
    local avail_kb
    avail_kb=$(df -k . | awk 'NR==2 {print $4}')
    local avail_gb=$((avail_kb / 1024 / 1024))
    if [[ $avail_gb -lt 2 ]]; then
        error "Недостаточно места на диске: ${avail_gb}GB (нужно минимум 2GB)"
    fi
    success "Место на диске: ${avail_gb}GB"

    # Memory (need at least 1GB free)
    if command -v free &>/dev/null; then
        local avail_mb
        avail_mb=$(free -m | awk '/^Mem:/ {print $7}')
        if [[ $avail_mb -lt 512 ]]; then
            warn "Доступно RAM: ${avail_mb}MB (рекомендуется 1GB+)"
        else
            success "RAM доступно: ${avail_mb}MB"
        fi
    fi

    # Port conflicts
    local ports_to_check=(80 8000)
    local in_use=()
    for port in "${ports_to_check[@]}"; do
        if ss -tlnp 2>/dev/null | grep -q ":${port} " || netstat -tlnp 2>/dev/null | grep -q ":${port} "; then
            local proc
            proc=$(ss -tlnp 2>/dev/null | grep ":${port} " | awk '{print $7}' | head -1 || echo "unknown")
            in_use+=("${port}(${proc})")
        fi
    done

    if [[ ${#in_use[@]} -gt 0 ]]; then
        warn "Порты уже заняты: ${in_use[*]}"
        info "Порт 80 может конфликтовать с nginx."
        info "Если это контейнеры от предыдущей установки — они будут остановлены."
        read -rp "Продолжить? [Y/n]: " CONFIRM; CONFIRM=${CONFIRM:-Y}
        [[ ! "$CONFIRM" =~ ^[Yy]$ ]] && exit 0
    else
        success "Порты 80 и 8000 свободны"
    fi

    # Check for existing containers
    local existing
    existing=$(docker ps -a --filter "name=vpn_" --format "{{.Names}}" 2>/dev/null || true)
    if [[ -n "$existing" ]]; then
        warn "Найдены существующие контейнеры: $(echo $existing | tr '\n' ', ')"
        read -rp "Остановить и удалить? [Y/n]: " CONFIRM; CONFIRM=${CONFIRM:-Y}
        if [[ "$CONFIRM" =~ ^[Yy]$ ]]; then
            info "Останавливаю контейнеры..."
            docker compose down --remove-orphans 2>/dev/null || true
            docker compose -f docker-compose.prod.yml down --remove-orphans 2>/dev/null || true
            success "Контейнеры остановлены"
        else
            exit 0
        fi
    fi

    # Check for stale PID files or lock files
    if [[ -f "setup.lock" ]]; then
        warn "Найден setup.lock — предыдущая установка могла не завершиться."
        rm -f setup.lock
    fi

    success "Все проверки пройдены"
}

preflight_checks

# ── Backup existing .env ─────────────────────────────────────────────────────
if [[ -f .env ]]; then
    BACKUP=".env.backup.$(date +%Y%m%d_%H%M%S)"
    warn ".env уже существует → ${BACKUP}"
    cp .env "$BACKUP"
    success "Бэкап .env сохранён"
fi

# ── Режим ─────────────────────────────────────────────────────────────────────
echo ""
echo "Режим запуска:"
echo "  1) Продакшен (домен + SSL) — только на VPS"
echo "  2) Разработка (localhost)"
read -rp "Выбор [1/2]: " MODE
if [[ "$MODE" != "1" && "$MODE" != "2" ]]; then
    error "Введите 1 (продакшен) или 2 (разработка)"
fi

# ── Ввод данных ───────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}── Основные ────────────────────────────────────────${RESET}"
read -rp "Название панели [Scorbium Dashboard VPN]: " APP_NAME
APP_NAME=${APP_NAME:-"Scorbium Dashboard VPN"}

read -rp "Telegram Bot Token: " BOT_TOKEN
[[ -z "$BOT_TOKEN" ]] && error "Bot Token обязателен"
# Validate token format (should be digits:string)
if [[ ! "$BOT_TOKEN" =~ ^[0-9]+:[A-Za-z0-9_-] ]]; then
    error "Неверный формат токена. Ожидается: 123456789:AAH..."
fi

read -rp "Telegram Admin IDs (через запятую, например: 123456789): " ADMIN_IDS_RAW
[[ -z "$ADMIN_IDS_RAW" ]] && error "Admin IDs обязательны"
ADMIN_IDS="[$(echo "$ADMIN_IDS_RAW" | tr -s ' ,' ',' | sed 's/^,//;s/,$//')]"

read -rp "Логин панели [admin]: " WEB_USER
WEB_USER=${WEB_USER:-admin}

read -rsp "Пароль панели (мин. 8 символов): " WEB_PASS
echo ""
if [[ -z "$WEB_PASS" ]]; then
    error "Пароль не может быть пустым"
fi
[[ ${#WEB_PASS} -lt 8 ]] && error "Пароль слишком короткий (минимум 8 символов)"

# Generate a dedicated JWT secret
JWT_SECRET_KEY=$(openssl rand -hex 32 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex(32))")
info "Сгенерирован JWT_SECRET_KEY"

echo ""
echo -e "${BOLD}── База данных ─────────────────────────────────────${RESET}"
read -rp "Имя БД [vpnbot]: " DB_NAME; DB_NAME=${DB_NAME:-vpnbot}
read -rp "Пользователь БД [postgres]: " DB_USER; DB_USER=${DB_USER:-postgres}
DEFAULT_DB_PASS=$(openssl rand -hex 16 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex(16))")
read -rsp "Пароль БД [случайный]: " DB_PASS; echo ""; DB_PASS=${DB_PASS:-$DEFAULT_DB_PASS}
read -rp "Внешний порт PostgreSQL [5432]: " DB_EXTERNAL_PORT; DB_EXTERNAL_PORT=${DB_EXTERNAL_PORT:-5432}

if [[ ! "$DB_EXTERNAL_PORT" =~ ^[0-9]+$ ]] || (( DB_EXTERNAL_PORT < 1 || DB_EXTERNAL_PORT > 65535 )); then
    error "Порт PostgreSQL должен быть числом от 1 до 65535"
fi

if ss -tlnp 2>/dev/null | grep -q ":${DB_EXTERNAL_PORT} " || netstat -tlnp 2>/dev/null | grep -q ":${DB_EXTERNAL_PORT} "; then
    proc=$(ss -tlnp 2>/dev/null | grep ":${DB_EXTERNAL_PORT} " | awk '{print $7}' | head -1 || echo "unknown")
    warn "Порт PostgreSQL ${DB_EXTERNAL_PORT} уже занят (${proc})"
    read -rp "Продолжить с этим портом? [y/N]: " CONFIRM_DB_PORT; CONFIRM_DB_PORT=${CONFIRM_DB_PORT:-N}
    [[ ! "$CONFIRM_DB_PORT" =~ ^[Yy]$ ]] && exit 1
fi

# Validate DB password
if [[ ${#DB_PASS} -lt 8 ]]; then
    warn "Пароль БД слабый (${#DB_PASS} символов). Рекомендуется 8+."
    read -rp "Продолжить с этим паролем? [Y/n]: " CONFIRM; CONFIRM=${CONFIRM:-Y}
    [[ ! "$CONFIRM" =~ ^[Yy]$ ]] && exit 1
fi

echo ""
echo -e "${BOLD}── VPN Panel (Marzban / Pasarguard) ─────────────────${RESET}"
VPN_PANEL_TYPE=marzban
echo ""
read -rp "URL панели (например: https://panel.example.com:8012): " PASAR_URL
[[ -z "$PASAR_URL" ]] && error "URL панели обязателен"
# Validate URL format
if [[ ! "$PASAR_URL" =~ ^https?:// ]]; then
    error "URL должен начинаться с http:// или https://"
fi
read -rp "Логин Marzban [admin]: " PASAR_LOGIN; PASAR_LOGIN=${PASAR_LOGIN:-admin}
read -rsp "Пароль Marzban: " PASAR_PASS; echo ""
[[ -z "$PASAR_PASS" ]] && error "Пароль Marzban обязателен"
success "Выбрана панель: Marzban / Pasarguard"

echo ""
echo -e "${BOLD}── YooKassa и CryptoBot ────────────────────────────${RESET}"
echo -e "${YELLOW}Настройте платёжные системы через панель: Telegram → Платёжные системы${RESET}"

# ── Продакшен-специфичные ─────────────────────────────────────────────────────
if [[ "$MODE" == "1" ]]; then
    echo ""
    echo -e "${BOLD}════════════════════════════════════════════════════${RESET}"
    echo -e "${BOLD}── Домен и SSL ─────────────────────────────────────${RESET}"
    echo -e "${BOLD}════════════════════════════════════════════════════${RESET}"
    echo -e "${YELLOW}⚠️  Домен должен уже указывать A-записью на IP этого сервера!${RESET}"
    read -rp "Домен (без https://): " DOMAIN; [[ -z "$DOMAIN" ]] && error "Обязателен"

    # Validate domain format
    if [[ ! "$DOMAIN" =~ ^[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?\.[a-zA-Z]{2,}$ ]]; then
        error "Неверный формат домена"
    fi

    # Check DNS resolution
    info "Проверяю DNS: ${DOMAIN}..."
    if command -v dig &>/dev/null; then
        if ! dig +short "$DOMAIN" | grep -qE '^[0-9]+\.'; then
            warn "DNS для ${DOMAIN} не найден или не указывает на IP"
            read -rp "Продолжить? (сертификат может не получиться) [y/N]: " CONFIRM; CONFIRM=${CONFIRM:-N}
            [[ ! "$CONFIRM" =~ ^[Yy]$ ]] && exit 1
        else
            success "DNS OK: $(dig +short "$DOMAIN" | head -1)"
        fi
    fi

    read -rp "Email для Let's Encrypt: " LE_EMAIL; [[ -z "$LE_EMAIL" ]] && error "Обязателен"

    # Check if port 80 is accessible from outside (needed for certbot)
    info "Проверяю доступность порта 80 (нужен для SSL)..."
    if ss -tlnp 2>/dev/null | grep -q ":80 " && ! ss -tlnp | grep ":80 " | grep -q "docker"; then
        warn "Порт 80 занят чем-то кроме Docker. Certbot не сможет его использовать."
        read -rp "Продолжить? [Y/n]: " CONFIRM; CONFIRM=${CONFIRM:-Y}
        [[ ! "$CONFIRM" =~ ^[Yy]$ ]] && exit 1
    fi

    echo ""
    echo "HTTPS порт:"
    echo "  1) 443 (стандартный)"
    echo "  2) 8443 (альтернативный, если 443 занят)"
    read -rp "Выбор [1/2]: " PORT_CHOICE
    if [[ "$PORT_CHOICE" == "2" ]]; then
        HTTPS_PORT=8443
    else
        HTTPS_PORT=443
    fi

    # Check if HTTPS_PORT is free
    if ss -tlnp 2>/dev/null | grep -q ":${HTTPS_PORT} " || netstat -tlnp 2>/dev/null | grep -q ":${HTTPS_PORT} "; then
        error "Порт ${HTTPS_PORT} уже занят. Остановите сервис или выберите другой."
    fi

    TG_PROTOCOL=webhook
    if [[ "$HTTPS_PORT" == "443" ]]; then
        WEBHOOK_URL="https://${DOMAIN}/webhook/bot"
    else
        WEBHOOK_URL="https://${DOMAIN}:${HTTPS_PORT}/webhook/bot"
    fi
    ALLOWED_ORIGINS='["https://'"${DOMAIN}"'"]'
    if [[ "$HTTPS_PORT" == "443" ]]; then
        PANEL_URL="https://${DOMAIN}/panel/"
    else
        PANEL_URL="https://${DOMAIN}:${HTTPS_PORT}/panel/"
    fi
else
    DOMAIN="localhost"
    HTTPS_PORT=443
    TG_PROTOCOL=long
    WEBHOOK_URL="https://localhost/webhook/bot"
    ALLOWED_ORIGINS='["http://localhost:8000"]'
    PANEL_URL="http://localhost/panel/"
fi

# ── Read version ──────────────────────────────────────────────────────────────
if [[ -f "pyproject.toml" ]]; then
    APP_VERSION=$(grep '^version' pyproject.toml | head -1 | sed 's/.*= *"\(.*\)"/\1/')
    [[ -z "$APP_VERSION" ]] && APP_VERSION="1.0.0"
else
    APP_VERSION="1.0.0"
    warn "pyproject.toml не найден, использую версию ${APP_VERSION}"
fi

# ── Генерация .env ────────────────────────────────────────────────────────────
info "Генерирую .env..."

cat > .env <<EOF
# =============================================================================
#  Scorbium Dashboard VPN — Environment Configuration
#  Generated automatically by setup.sh
# =============================================================================

# ── Application ───────────────────────────────────────────────────────────────
APP_NAME=${APP_NAME}
APP_VERSION=${APP_VERSION}
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
ALLOWED_ORIGINS=${ALLOWED_ORIGINS}

# ── Security ──────────────────────────────────────────────────────────────────
JWT_SECRET_KEY=${JWT_SECRET_KEY}
WEB_SUPERADMIN_USERNAME=${WEB_USER}
WEB_SUPERADMIN_PASSWORD=${WEB_PASS}

# Metrics API key (optional - if set, /metrics endpoint requires Bearer token)
METRICS_API_KEY=

# Sentry DSN (optional - for error tracking)
SENTRY_DSN=
SENTRY_ENV=production

# ── Telegram Bot ──────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN=${BOT_TOKEN}
TELEGRAM_ADMIN_IDS=${ADMIN_IDS}
TELEGRAM_TYPE_PROTOCOL=${TG_PROTOCOL}
TELEGRAM_WEBHOOK_URL=${WEBHOOK_URL}
TELEGRAM_WEBHOOK_PATH=/webhook/bot

# ── VPN Panel (Marzban / Pasarguard) ──────────────────────────────────────────
PASARGUARD_ADMIN_PANEL=${PASAR_URL}
PASARGUARD_ADMIN_LOGIN=${PASAR_LOGIN}
PASARGUARD_ADMIN_PASSWORD=${PASAR_PASS}
PASARGUARD_API_KEY=
VPN_PANEL_TYPE=${VPN_PANEL_TYPE}

# ── Database ──────────────────────────────────────────────────────────────────
DB_ENGINE=postgresql
DB_NAME=${DB_NAME}
DB_HOST=db
DB_PORT=5432
DB_EXTERNAL_PORT=${DB_EXTERNAL_PORT}
DB_USER=${DB_USER}
DB_PASSWORD=${DB_PASS}

# ── Payment Systems ───────────────────────────────────────────────────────────
# Configure payment providers from the admin panel after first login.
CRYPTOBOT_TOKEN=

# ── Domain / SSL ──────────────────────────────────────────────────────────────
HTTPS_PORT=${HTTPS_PORT}
DOMAIN=${DOMAIN}

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_PATH=logs
LOG_ROTATION=1 day
LOG_RETENTION=30 days
LOG_LEVEL=INFO

# ── Redis (optional - enables shared production rate limiting) ───────────────
# If you enable the optional Redis profile in docker-compose.prod.yml, use:
# REDIS_URL=redis://redis:6379/0
REDIS_URL=
EOF

# Secure .env permissions
chmod 600 .env
success ".env создан (chmod 600)"

# ── Создание директорий ───────────────────────────────────────────────────────
mkdir -p logs nginx/ssl certbot_www

# ── Генерация nginx.conf (продакшен) ──────────────────────────────────────────
if [[ "$MODE" == "1" ]]; then
    info "Генерирую nginx.conf для ${DOMAIN}:${HTTPS_PORT}..."
    prepare_generated_nginx_conf

    if [[ "$HTTPS_PORT" == "443" ]]; then
        REDIR='return 301 https://$host$request_uri;'
    else
        REDIR="return 301 https://\$host:${HTTPS_PORT}\$request_uri;"
    fi

    cat > "$NGINX_GENERATED_CONF" << NGINXEOF
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

        proxy_connect_timeout 10s;
        proxy_read_timeout    60s;
        proxy_send_timeout    60s;
        proxy_next_upstream   error timeout http_502 http_503;
        proxy_next_upstream_tries 2;

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
    success "nginx.conf создан → ${NGINX_GENERATED_CONF}"
fi

# ── Запуск ────────────────────────────────────────────────────────────────────
echo ""
read -rp "Запустить? [Y/n]: " START; START=${START:-Y}
[[ ! "$START" =~ ^[Yy]$ ]] && { info "Запустите вручную: docker compose up -d"; exit 0; }

touch setup.lock

    if [[ "$MODE" == "1" ]]; then
    # ── ПРОДАКШЕН ─────────────────────────────────────────────────────────────
    docker compose -f docker-compose.prod.yml down --remove-orphans 2>/dev/null || true

    # SSL сертификат
    CERT_PATH="nginx/ssl/live/${DOMAIN}/fullchain.pem"
    if [[ -f "$CERT_PATH" ]]; then
        success "SSL сертификат уже существует"
    else
        info "Получаю SSL сертификат..."

        if ! command -v certbot &>/dev/null; then
            info "Устанавливаю certbot..."
            if command -v apt-get &>/dev/null; then
                apt-get update -qq && apt-get install -y -qq certbot
            elif command -v yum &>/dev/null; then
                yum install -y certbot
            else
                error "Не удалось установить certbot. Установите вручную."
            fi
        fi

        certbot certonly --standalone \
            --email "${LE_EMAIL}" \
            --agree-tos \
            --no-eff-email \
            -d "${DOMAIN}" || {
            warn "Не удалось получить SSL сертификат."
            warn "Убедитесь: домен указывает на сервер, порт 80 открыт."
            warn "Повторите: certbot certonly --standalone -d ${DOMAIN}"
            rm -f setup.lock
            exit 1
        }

        info "Копирую сертификаты..."
        CERT_SRC=$(ls -d /etc/letsencrypt/live/${DOMAIN}* 2>/dev/null | head -1)
        if [[ -z "$CERT_SRC" ]]; then
            error "Папка сертификатов не найдена в /etc/letsencrypt/live/"
        fi
        mkdir -p "nginx/ssl/live/${DOMAIN}"
        cp "$CERT_SRC/fullchain.pem" "nginx/ssl/live/${DOMAIN}/"
        cp "$CERT_SRC/privkey.pem" "nginx/ssl/live/${DOMAIN}/"
        chmod 600 "nginx/ssl/live/${DOMAIN}/privkey.pem"
        success "Сертификаты скопированы из ${CERT_SRC}"

        # Cron для автообновления
        PROJECT_DIR="$(pwd)"
        CRON_FILE="/etc/cron.d/vpn-certbot-renew"
        cat > "$CRON_FILE" <<CRONEOF
0 3 * * * root certbot renew --quiet --standalone \
  --pre-hook "docker compose -f ${PROJECT_DIR}/docker-compose.prod.yml stop nginx" \
  --post-hook "CERT_SRC=\$(ls -d /etc/letsencrypt/live/${DOMAIN}* | head -1); cp \$CERT_SRC/fullchain.pem ${PROJECT_DIR}/nginx/ssl/live/${DOMAIN}/ && cp \$CERT_SRC/privkey.pem ${PROJECT_DIR}/nginx/ssl/live/${DOMAIN}/ && docker compose -f ${PROJECT_DIR}/docker-compose.prod.yml start nginx"
CRONEOF
        chmod 644 "$CRON_FILE"
        echo "" >> "$CRON_FILE"
        success "Автообновление SSL настроено (каждый день в 3:00)"
    fi

    _DC="docker-compose.prod.yml"
    _DB_VOLUME="scorbiumdashboard_vpn_db_data"
    if docker volume ls --format '{{.Name}}' 2>/dev/null | grep -qF "$_DB_VOLUME"; then
        info "БД уже существует — проверяю пароль..."
        docker compose -f "$_DC" up -d db
        sleep 3
        if ! docker exec vpn_db psql -U "${DB_USER:-postgres}" -d "${DB_NAME:-vpnbot}" -c "SELECT 1" &>/dev/null 2>&1; then
            warn "Пароль БД в .env не совпадает с тем, с которым создана БД!"
            warn "Причина: DB_PASSWORD был изменён после первого запуска."
            echo ""
            echo "  Варианты:"
            echo "  1) Сбросить БД (все данные будут удалены):"
            echo "     docker compose -f $_DC down -v && bash setup.sh"
            echo "  2) Вернуть старый пароль в .env"
            echo ""
            read -rp "Сбросить БД и пересоздать? [y/N]: " RESET_DB; RESET_DB=${RESET_DB:-N}
            if [[ "$RESET_DB" =~ ^[Yy]$ ]]; then
                docker compose -f "$_DC" down -v
                success "Volume БД удалён — будет создан заново"
            else
                rm -f setup.lock
                exit 1
            fi
        else
            success "Пароль БД совпадает"
        fi
    fi

    # Запускаем только БД
    info "Запускаю БД..."
    docker compose -f "$_DC" up -d db

    if ! wait_for_health "vpn_db" "БД" 12 5; then
        warn "БД не стала healthy за 60 сек"
        docker compose -f "$_DC" logs db --tail=20
        diagnose_db_failure
        rm -f setup.lock
        exit 1
    fi

    run_prod_migrations "$_DC" || { rm -f setup.lock; exit 1; }

    # Теперь запускаем app
    info "Запускаю app..."
    docker compose -f "$_DC" up -d app

    if ! wait_for_health "vpn_app" "App" 18 5; then
        warn "App не стал healthy за 90 сек"
        docker compose -f "$_DC" logs app --tail=30
        read -rp "Продолжить? [Y/n]: " CONFIRM; CONFIRM=${CONFIRM:-Y}
        [[ ! "$CONFIRM" =~ ^[Yy]$ ]] && { rm -f setup.lock; exit 1; }
    fi

    # Nginx
    info "Запускаю nginx..."
    docker compose -f docker-compose.prod.yml up -d nginx
    sleep 3

    NGINX_STATUS=$(docker inspect --format='{{.State.Status}}' vpn_nginx 2>/dev/null || echo "unknown")
    if [[ "$NGINX_STATUS" != "running" ]]; then
        warn "nginx не запустился:"
        docker compose -f docker-compose.prod.yml logs nginx --tail=20
        rm -f setup.lock
        exit 1
    fi

    # Verify HTTPS
    sleep 2
    if curl -sk "https://${DOMAIN}:${HTTPS_PORT}/health" | grep -q "ok" 2>/dev/null; then
        success "HTTPS работает: https://${DOMAIN}:${HTTPS_PORT}/health"
    else
        warn "HTTPS не отвечает. Проверьте: curl -sk https://${DOMAIN}:${HTTPS_PORT}/health"
    fi

else
    # ── РАЗРАБОТКА ────────────────────────────────────────────────────────────
    info "Запускаю в режиме разработки..."
    docker compose down --remove-orphans 2>/dev/null || true
    docker compose up -d db app nginx

    if ! wait_for_health "vpn_app" "App" 12 5; then
        warn "App не стал healthy за 60 сек:"
        docker compose logs app --tail=20
        rm -f setup.lock
        exit 1
    fi

    info "Проверяю пароль БД..."
    DB_PASS=${DB_PASS:-postgres}
    if ! docker exec -e PGPASSWORD="${DB_PASS}" vpn_db psql -U "${DB_USER}" -d "${DB_NAME}" -h localhost -c "SELECT 1" &>/dev/null 2>&1; then
        warn "Пароль БД не совпадает. Пытаюсь синхронизировать..."
        if ! docker exec vpn_db psql -U "${DB_USER}" -c "ALTER USER ${DB_USER} PASSWORD '${DB_PASS}';" &>/dev/null 2>&1; then
            diagnose_db_failure
            rm -f setup.lock
            exit 1
        fi
        sleep 1
    fi

    info "Применяю миграции БД..."
    if ! docker compose exec app uv run python fix_alembic.py; then
        diagnose_db_failure
        rm -f setup.lock
        exit 1
    fi
    if ! docker compose exec app uv run alembic upgrade head; then
        diagnose_db_failure
        rm -f setup.lock
        exit 1
    fi
    success "Миграции применены"
fi

# Cleanup
rm -f setup.lock

# ── Итог ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${GREEN}║           ✅  Готово!                            ║${RESET}"
echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════════════╝${RESET}"
echo ""
echo -e "  🌐 Панель:   ${BOLD}${CYAN}${PANEL_URL}${RESET}"
echo -e "  👤 Логин:    ${BOLD}${WEB_USER}${RESET}"
echo -e "  🔑 Пароль:   ${BOLD}${WEB_PASS}${RESET}"
echo -e "  🛡️  VPN:      ${BOLD}Marzban / Pasarguard${RESET} (${PASAR_URL})"
echo ""
echo -e "  📋 Логи:    ${YELLOW}docker compose logs -f app${RESET}"
echo -e "  🛑 Стоп:    ${YELLOW}docker compose down${RESET}"
echo -e "  🔄 Обновить: ${YELLOW}bash update.sh${RESET}"
echo ""
echo -e "  📌 Не забудьте:"
echo -e "     • Настроить платёжные системы в панели"
echo -e "     • Загрузить фото для кнопок бота"
echo -e "     • Проверить подключение к Marzban/Pasarguard"
echo ""
