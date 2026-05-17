#!/usr/bin/env bash
# =============================================================================
#  VPN Dashboard — Update
# =============================================================================
set -euo pipefail
GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; RESET='\033[0m'
COMPOSE_FILE="docker-compose.prod.yml"
NGINX_GENERATED_CONF="nginx/nginx.generated.conf"
MIN_FREE_KB=1048576
BACKUP_DIR="${BACKUP_DIR:-../ScorbiumDashboard-backups}"

info()    { echo -e "${CYAN}[INFO]${RESET} $*"; }
success() { echo -e "${GREEN}[OK]${RESET}   $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET} $*"; }
error()   { echo -e "${RED}[ERR]${RESET}  $*"; exit 1; }

print_recent_logs() {
    warn "Последние логи контейнеров:"
    docker compose -f "${COMPOSE_FILE}" logs app --tail=25 2>/dev/null || true
    docker compose -f "${COMPOSE_FILE}" logs nginx --tail=25 2>/dev/null || true
    docker compose -f "${COMPOSE_FILE}" logs db --tail=25 2>/dev/null || true
}

on_error() {
    local exit_code=$?
    echo ""
    warn "Обновление завершилось с ошибкой (code=${exit_code})"
    print_recent_logs
    exit "${exit_code}"
}

trap on_error ERR

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || error "Не найдена команда: $1"
}

read_env_value() {
    local key="$1"
    local line
    line="$(grep -m1 "^${key}=" .env 2>/dev/null || true)"
    printf '%s' "${line#*=}" | tr -d '\r'
}

read_env_trimmed() {
    read_env_value "$1" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
}

ensure_env_var() {
    local key="$1"
    local value="$2"
    if grep -q "^${key}=" .env; then
        return 0
    fi
    echo "${key}=${value}" >> .env
    success "${key} добавлен в .env"
}

replace_env_var() {
    local key="$1"
    local value="$2"
    if grep -q "^${key}=" .env; then
        if sed --version 2>/dev/null | grep -q GNU; then
            sed -i "s|^${key}=.*|${key}=${value}|" .env
        else
            sed -i '' "s|^${key}=.*|${key}=${value}|" .env
        fi
    else
        echo "${key}=${value}" >> .env
    fi
}

check_clean_git_tree() {
    local status blocked ignored line path
    status="$(git status --porcelain --untracked-files=all)"
    [[ -z "${status}" ]] && return 0

    blocked=""
    ignored=""
    while IFS= read -r line; do
        [[ -z "${line}" ]] && continue
        path="${line:3}"
        if [[ "${line:0:2}" == "??" ]]; then
            case "${path}" in
                nginx/ssl|nginx/ssl/*|backup_*.sql|.env.backup.*)
                    ignored+="${line}"$'\n'
                    continue
                    ;;
            esac
        fi
        blocked+="${line}"$'\n'
    done <<< "${status}"

    [[ -n "${ignored}" ]] && warn "Игнорирую локальные служебные файлы:\n${ignored}"
    [[ -n "${blocked}" ]] && error "Рабочее дерево не чистое. Сначала сохраните локальные изменения или stash.\n${blocked}"
}

check_disk_space() {
    local free_kb
    free_kb="$(df -Pk . | awk 'NR==2 {print $4}')"
    [[ -z "${free_kb}" ]] && warn "Не удалось определить свободное место"
    if [[ -n "${free_kb}" && "${free_kb}" -lt "${MIN_FREE_KB}" ]]; then
        error "Недостаточно свободного места (< 1 GB). Освободите диск перед обновлением."
    fi
}

verify_backup_file() {
    local path="$1"
    [[ ! -s "${path}" ]] && error "Бэкап ${path} пустой или не создан"
    if ! head -n 5 "${path}" | grep -Eq 'PostgreSQL database dump|--'; then
        warn "Бэкап ${path} создан, но его заголовок выглядит нестандартно. Проверьте файл вручную."
    fi
}

wait_for_container_health() {
    local container="$1"
    local label="$2"
    local attempts="$3"
    local delay="$4"

    info "Жду готовности ${label}..."
    for i in $(seq 1 "${attempts}"); do
        local status
        status="$(docker inspect --format='{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${container}" 2>/dev/null || echo "unknown")"
        if [[ "${status}" == "healthy" || "${status}" == "running" ]]; then
            success "${label} готов"
            return 0
        fi
        sleep "${delay}"
    done
    error "${label} не стал ready вовремя"
}

prepare_generated_nginx_conf() {
    mkdir -p "$(dirname "${NGINX_GENERATED_CONF}")"
    if [[ -d "${NGINX_GENERATED_CONF}" ]]; then
        warn "${NGINX_GENERATED_CONF} оказался директорией. Пересоздаю как файл..."
        rm -rf "${NGINX_GENERATED_CONF}"
    fi
    : > "${NGINX_GENERATED_CONF}"
}

sql_quote_literal() {
    python3 - "$1" <<'PY'
import sys
value = sys.argv[1]
print("'" + value.replace("'", "''") + "'")
PY
}

sql_quote_ident() {
    python3 - "$1" <<'PY'
import sys
value = sys.argv[1]
print('"' + value.replace('"', '""') + '"')
PY
}

get_db_admin_role() {
    local candidate
    for candidate in pgg_superadmins postgres; do
        if docker exec vpn_db psql -U "${candidate}" -d postgres -tAc "SELECT 1" >/dev/null 2>&1; then
            printf '%s\n' "${candidate}"
            return 0
        fi
    done
    return 1
}

db_role_exists() {
    local role_lit
    role_lit="$(sql_quote_literal "$1")"
    docker exec vpn_db psql -U "${DB_ADMIN_ROLE}" -d postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname = ${role_lit}" | grep -q 1
}

db_database_exists() {
    local db_lit
    db_lit="$(sql_quote_literal "$1")"
    docker exec vpn_db psql -U "${DB_ADMIN_ROLE}" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = ${db_lit}" | grep -q 1
}

run_db_admin_sql() {
    local sql="$1"
    docker exec -i vpn_db psql -U "${DB_ADMIN_ROLE}" -d postgres -v ON_ERROR_STOP=1 >/dev/null <<SQL
${sql}
SQL
}

verify_app_db_connection() {
    docker compose -f "${COMPOSE_FILE}" run --rm app sh -lc 'PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -c "select 1;" >/dev/null'
}

ensure_database_prerequisites() {
    DB_ADMIN_ROLE="$(get_db_admin_role)" || error "Не удалось определить административную роль PostgreSQL внутри vpn_db"
    info "Использую DB admin role: ${DB_ADMIN_ROLE}"

    if ! db_role_exists "${DB_USER}"; then
        warn "Роль ${DB_USER} отсутствует — создаю"
        run_db_admin_sql "CREATE ROLE $(sql_quote_ident "${DB_USER}") LOGIN PASSWORD $(sql_quote_literal "${DB_PASSWORD}");"
        success "Роль ${DB_USER} создана"
    fi

    if ! db_database_exists "${DB_NAME}"; then
        warn "База ${DB_NAME} отсутствует — создаю"
        run_db_admin_sql "CREATE DATABASE $(sql_quote_ident "${DB_NAME}") OWNER $(sql_quote_ident "${DB_USER}");"
        success "База ${DB_NAME} создана"
    fi

    if ! verify_app_db_connection; then
        warn "Текущий пароль роли ${DB_USER} не совпадает с .env — синхронизирую"
        run_db_admin_sql "ALTER ROLE $(sql_quote_ident "${DB_USER}") WITH PASSWORD $(sql_quote_literal "${DB_PASSWORD}");"
        verify_app_db_connection || error "Не удалось синхронизировать доступ app к БД. Проверьте DB_USER/DB_PASSWORD в .env"
        success "Подключение app к БД восстановлено"
    fi
}

validate_pasarguard_url() {
    local panel_url="$1"
    case "${panel_url}" in
        ""|http://|https://)
            error "PASARGUARD_ADMIN_PANEL в .env не заполнен корректно"
            ;;
    esac
    if [[ ! "${panel_url}" =~ ^https?://[^/]+ ]]; then
        error "PASARGUARD_ADMIN_PANEL имеет некорректный формат: ${panel_url}"
    fi
}

run_app_http_check() {
    local path="$1"
    local expected="$2"
    docker compose -f "${COMPOSE_FILE}" exec -T app python - "$path" "$expected" <<'PY'
import sys
import urllib.request
import urllib.error

path = sys.argv[1]
allowed = {int(x) for x in sys.argv[2].split(",")}
url = f"http://localhost:8000{path}"
req = urllib.request.Request(url, method="GET")
opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler())
try:
    with opener.open(req, timeout=10) as resp:
        code = resp.getcode()
except urllib.error.HTTPError as exc:
    code = exc.code

if code not in allowed:
    raise SystemExit(f"{path} returned unexpected status {code}, expected one of {sorted(allowed)}")
print(f"{path} -> {code}")
PY
}

run_smoke_checks() {
    info "Запускаю smoke-check после обновления..."
    docker compose -f "${COMPOSE_FILE}" exec -T nginx nginx -t
    run_app_http_check "/health" "200"
    run_app_http_check "/api/v1/health/" "200"
    run_app_http_check "/panel/" "200,302,303,307"
    run_app_http_check "/cabinet/" "200,302,303,307"
    success "Smoke-check пройден"
}

# ── Проверки ──────────────────────────────────────────────────────────────────
[[ ! -f .env ]] && error ".env не найден. Запустите setup.sh сначала."
[[ ! -f "${COMPOSE_FILE}" ]] && error "Запустите скрипт из корня проекта."

require_cmd git
require_cmd docker
require_cmd openssl
docker compose version >/dev/null 2>&1 || error "Требуется Docker Compose v2"
check_disk_space
check_clean_git_tree

DOMAIN="$(read_env_trimmed "DOMAIN")"
HTTPS_PORT="$(read_env_trimmed "HTTPS_PORT")"
HTTPS_PORT=${HTTPS_PORT:-443}

[[ -z "$DOMAIN" || "$DOMAIN" == "localhost" ]] && error "DOMAIN не задан в .env (нужен продакшен-домен)"

# Ensure JWT_SECRET_KEY exists
if ! grep -q "^JWT_SECRET_KEY=" .env || [[ -z "$(grep "^JWT_SECRET_KEY=" .env | cut -d= -f2- | xargs)" ]]; then
    warn "JWT_SECRET_KEY не найден в .env — генерирую новый..."
    NEW_JWT=$(openssl rand -hex 32 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex(32))")
    echo "JWT_SECRET_KEY=${NEW_JWT}" >> .env
    success "JWT_SECRET_KEY добавлен в .env"
fi

ensure_env_var "TELEGRAM_WEBHOOK_PATH" "/webhook/bot"
ensure_env_var "SERVER_HOST" "0.0.0.0"
ensure_env_var "SERVER_PORT" "8000"
ensure_env_var "DB_HOST" "db"
ensure_env_var "DB_PORT" "5432"
ensure_env_var "DB_ENGINE" "postgresql"
ensure_env_var "VPN_PANEL_TYPE" "marzban"
ensure_env_var "APP_VERSION" "1.0.0"

PASARGUARD_ADMIN_PANEL="$(read_env_trimmed "PASARGUARD_ADMIN_PANEL")"
validate_pasarguard_url "${PASARGUARD_ADMIN_PANEL}"

info "Домен: ${DOMAIN}, HTTPS порт: ${HTTPS_PORT}"

# ── [1/4] git pull ────────────────────────────────────────────────────────────
info "[1/4] Обновляю код..."

# Бэкап БД перед обновлением
DB_NAME="$(read_env_value "DB_NAME")"
DB_USER="$(read_env_value "DB_USER")"
DB_PASSWORD="$(read_env_value "DB_PASSWORD")"
DB_NAME=${DB_NAME:-vpnbot}
DB_USER=${DB_USER:-postgres}
DB_PASSWORD=${DB_PASSWORD:-postgres}
mkdir -p "${BACKUP_DIR}"
BACKUP_FILE="${BACKUP_DIR}/backup_$(date +%Y%m%d_%H%M%S).sql"
info "Создаю бэкап БД → ${BACKUP_FILE}..."
if docker ps --format '{{.Names}}' | grep -qx 'vpn_db'; then
    if docker exec vpn_db pg_dump -U "${DB_USER}" "${DB_NAME}" > "${BACKUP_FILE}" 2>/dev/null; then
        verify_backup_file "${BACKUP_FILE}"
        success "Бэкап создан: ${BACKUP_FILE}"
    else
        warn "Не удалось создать бэкап БД. Продолжаю обновление без свежего backup."
        rm -f "${BACKUP_FILE}"
    fi
else
    warn "Контейнер vpn_db не запущен, пропускаю бэкап"
fi

git pull --ff-only || error "git pull --ff-only failed. Проверьте ветку и локальные изменения."

# ── APP_VERSION из pyproject.toml ─────────────────────────────────────────────
NEW_VER=$(grep '^version' pyproject.toml 2>/dev/null | head -1 | sed 's/.*= *"\(.*\)"/\1/' || true)
if [[ -n "$NEW_VER" ]]; then
    replace_env_var "APP_VERSION" "${NEW_VER}"
    info "APP_VERSION → ${NEW_VER}"
fi

# ── Синхронизируем TELEGRAM_WEBHOOK_URL с портом ──────────────────────────────
CURRENT_WEBHOOK="$(read_env_trimmed "TELEGRAM_WEBHOOK_URL")"
if [[ "$HTTPS_PORT" == "443" ]]; then
    CORRECT_WEBHOOK="https://${DOMAIN}/webhook/bot"
else
    CORRECT_WEBHOOK="https://${DOMAIN}:${HTTPS_PORT}/webhook/bot"
fi
if [[ "$CURRENT_WEBHOOK" != "$CORRECT_WEBHOOK" ]]; then
    warn "TELEGRAM_WEBHOOK_URL устарел: ${CURRENT_WEBHOOK}"
    info "Обновляю → ${CORRECT_WEBHOOK}"
    replace_env_var "TELEGRAM_WEBHOOK_URL" "${CORRECT_WEBHOOK}"
    success "TELEGRAM_WEBHOOK_URL обновлён"
fi

# ── [2/4] nginx.generated.conf ────────────────────────────────────────────────
info "[2/4] Генерирую nginx/nginx.generated.conf (${DOMAIN}:${HTTPS_PORT})..."
prepare_generated_nginx_conf

CERT_PATH="nginx/ssl/live/${DOMAIN}/fullchain.pem"
[[ ! -f "$CERT_PATH" ]] && warn "SSL сертификат не найден: ${CERT_PATH}. Запустите: certbot certonly --standalone -d ${DOMAIN}"

# Redirect: при 443 не добавляем порт в URL
if [[ "$HTTPS_PORT" == "443" ]]; then
    REDIR='return 301 https://$host$request_uri;'
else
    REDIR="return 301 https://\$host:${HTTPS_PORT}\$request_uri;"
fi

cat > "${NGINX_GENERATED_CONF}" << NGINXEOF
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
        add_header X-Frame-Options SAMEORIGIN always;
        add_header X-Content-Type-Options nosniff always;
        add_header Referrer-Policy "strict-origin-when-cross-origin" always;

        proxy_connect_timeout 10s;
        proxy_read_timeout    60s;
        proxy_send_timeout    60s;
        proxy_next_upstream   error timeout http_502 http_503;
        proxy_next_upstream_tries 2;

        location /panel/ {
            limit_req zone=panel burst=20 nodelay;
            proxy_pass http://vpn_app;
            proxy_set_header Host              \$host;
            proxy_set_header X-Real-IP         \$remote_addr;
            proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto \$scheme;
        }
        location = /cabinet {
            return 301 /cabinet/;
        }
        location /cabinet/ {
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
        # WebSocket notifications endpoint
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
success "nginx generated conf готов"

# ── [3/4] Пересобираем и запускаем ───────────────────────────────────────────
info "[3/4] Пересобираю app..."
docker compose -f "${COMPOSE_FILE}" build app

info "Очищаю старые образы..."
docker image prune -f --filter "until=168h" >/dev/null 2>&1 || true

info "Запускаю базу данных и применяю миграции до старта приложения..."
docker compose -f "${COMPOSE_FILE}" up -d db
wait_for_container_health "vpn_db" "PostgreSQL" 18 5
ensure_database_prerequisites

docker compose -f "${COMPOSE_FILE}" run --rm app uv run python fix_alembic.py
docker compose -f "${COMPOSE_FILE}" run --rm app uv run alembic upgrade head
success "Миграции БД применены"

info "Перезапускаю контейнеры приложения..."
docker compose -f "${COMPOSE_FILE}" up -d app

info "Жду готовности app (макс 90 сек)..."
wait_for_container_health "vpn_app" "App" 18 5

# ── [4/4] Миграции ────────────────────────────────────────────────────────────
info "[4/4] Перезапускаю nginx и выполняю smoke-check..."

# ── Перезапускаем nginx с новым конфигом ─────────────────────────────────────
docker compose -f "${COMPOSE_FILE}" up -d nginx
sleep 2
# Try reload first, fallback to restart
docker compose -f "${COMPOSE_FILE}" exec nginx nginx -s reload 2>/dev/null || docker compose -f "${COMPOSE_FILE}" restart nginx
sleep 3
NGINX_STATUS=$(docker inspect --format='{{.State.Status}}' vpn_nginx 2>/dev/null || echo "unknown")
if [[ "$NGINX_STATUS" == "running" ]]; then
    success "nginx запущен"
else
    warn "nginx не запустился, проверьте логи:"
    docker compose -f "${COMPOSE_FILE}" logs nginx --tail=15
fi
run_smoke_checks

# ── Итог ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════╗${RESET}"
echo -e "${GREEN}║  ✅  Обновление завершено                        ║${RESET}"
echo -e "${GREEN}╚══════════════════════════════════════════════════╝${RESET}"
echo ""
if [[ "$HTTPS_PORT" == "443" ]]; then
    echo -e "  🌐 Панель: ${CYAN}https://${DOMAIN}/panel/${RESET}"
else
    echo -e "  🌐 Панель: ${CYAN}https://${DOMAIN}:${HTTPS_PORT}/panel/${RESET}"
fi
echo -e "  Логи:  docker compose -f docker-compose.prod.yml logs -f app"
echo ""
