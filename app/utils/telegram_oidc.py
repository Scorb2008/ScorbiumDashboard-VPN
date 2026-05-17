import time
from typing import Optional

import httpx
from jose import jwk, jwt
from jose.exceptions import JWTError

from app.core.config import config
from app.utils.log import log

JWKS_URL = "https://oauth.telegram.org/.well-known/jwks.json"
OIDC_ISSUER = "https://oauth.telegram.org"
JWKS_CACHE_TTL = 3600

_jwks_cache: list = [0.0, []]  # [timestamp, keys]


async def _fetch_jwks() -> list[dict]:
    now = time.time()
    if now - _jwks_cache[0] < JWKS_CACHE_TTL:
        return _jwks_cache[1]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(JWKS_URL)
            resp.raise_for_status()
            data = resp.json()
            keys = data.get("keys", [])
            _jwks_cache[0] = now
            _jwks_cache[1] = keys
            log.info("Fetched Telegram OIDC JWKS ({} keys)", len(keys))
            return keys
    except Exception as e:
        log.error("Failed to fetch Telegram JWKS: {}", e)
        return _jwks_cache[1]


def _find_key(keys: list[dict], kid: str) -> Optional[dict]:
    for k in keys:
        if k.get("kid") == kid:
            return k
    return None


async def verify_telegram_id_token(id_token: str) -> Optional[dict]:
    try:
        header = jwt.get_unverified_header(id_token)
        kid = header.get("kid", "")
        keys = await _fetch_jwks()
        key_data = _find_key(keys, kid)
        if not key_data:
            log.error("No matching JWK found for kid={}", kid)
            return None
        public_key = jwk.construct(key_data, algorithm="RS256")
        payload = jwt.decode(
            id_token,
            public_key,
            algorithms=["RS256"],
            audience=str(config.telegram.telegram_client_id),
            issuer=OIDC_ISSUER,
            options={"verify_exp": True, "verify_at_hash": False},
        )
        return payload
    except JWTError as e:
        log.error("Telegram id_token verification failed: {}", e)
        return None
    except Exception as e:
        log.error("Unexpected error verifying Telegram id_token: {}", e)
        return None
