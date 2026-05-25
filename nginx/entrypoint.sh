#!/bin/sh
set -eu

# Render the dev nginx config from template so docker compose respects SET_PATH_ADMIN.
TEMPLATE_PATH="/etc/nginx/templates/nginx.local.template.conf"
OUTPUT_PATH="/etc/nginx/nginx.conf"

normalize_panel_path() {
    path="${1:-/panel/}"

    case "$path" in
        "")
            path="/panel/"
            ;;
        /*)
            ;;
        *)
            path="/$path"
            ;;
    esac

    path="$(printf '%s' "$path" | sed 's#//*#/#g')"

    case "$path" in
        */)
            ;;
        *)
            path="${path}/"
            ;;
    esac

    if [ "$path" = "/" ]; then
        path="/panel/"
    fi

    printf '%s' "$path"
}

render_nginx_config() {
    [ -f "$TEMPLATE_PATH" ] || return 0

    panel_root="$(normalize_panel_path "${SET_PATH_ADMIN:-/panel/}")"
    panel_prefix="${panel_root%/}"

    sed \
        -e "s|ADMIN_PATH_PREFIX_PLACEHOLDER|${panel_prefix}|g" \
        -e "s|ADMIN_PATH_ROOT_PLACEHOLDER|${panel_root}|g" \
        "$TEMPLATE_PATH" > "$OUTPUT_PATH"
}

render_nginx_config

# Auto-detect existing SSL certs or generate self-signed fallback.
# Creates a symlink at /etc/nginx/ssl/live/current -> real cert dir or self-signed.

CERT_LINK="/etc/nginx/ssl/live/current"
SELFSIGNED_DIR="/etc/nginx/ssl/live/selfsigned"

# Look for real certs under /etc/nginx/ssl/live/*/
for d in /etc/nginx/ssl/live/*/; do
    [ -d "$d" ] || continue
    cert="${d}fullchain.pem"
    key="${d}privkey.pem"
    if [ -f "$cert" ] && [ -f "$key" ]; then
        rm -f "$CERT_LINK"
        ln -s "${d%/}" "$CERT_LINK"
        exit 0
    fi
done

if [ -f "${CERT_LINK}/fullchain.pem" ] && [ -f "${CERT_LINK}/privkey.pem" ]; then
    exit 0
fi

if [ ! -w /etc/nginx/ssl/live ]; then
    echo "SSL directory is read-only and no usable certificate was found at ${CERT_LINK}" >&2
    exit 1
fi

# No real certs — generate self-signed fallback
mkdir -p "$SELFSIGNED_DIR"
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout "${SELFSIGNED_DIR}/privkey.pem" \
    -out "${SELFSIGNED_DIR}/fullchain.pem" \
    -subj "/CN=localhost" 2>/dev/null
rm -f "$CERT_LINK"
ln -s "$SELFSIGNED_DIR" "$CERT_LINK"
