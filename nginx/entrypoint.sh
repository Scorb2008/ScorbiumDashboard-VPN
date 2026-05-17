#!/bin/sh

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
