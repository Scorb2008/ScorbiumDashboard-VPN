#!/bin/sh

# Auto-detect existing SSL certs or generate self-signed fallback.
# Creates a symlink at /etc/nginx/ssl/live/current -> real cert dir or self-signed.

CERT_LINK="/etc/nginx/ssl/live/current"

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

# No real certs — generate self-signed fallback
mkdir -p /etc/nginx/ssl/live/selfsigned
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout /etc/nginx/ssl/live/selfsigned/privkey.pem \
    -out /etc/nginx/ssl/live/selfsigned/fullchain.pem \
    -subj "/CN=localhost" 2>/dev/null
rm -f "$CERT_LINK"
ln -s /etc/nginx/ssl/live/selfsigned "$CERT_LINK"
