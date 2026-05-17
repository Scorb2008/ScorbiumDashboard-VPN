import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import bcrypt
from jose import JWTError, jwt

from app.core.config import config
from app.utils.log import log

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7
_TEMP_SECRET_KEY: str | None = None

def _secret_key() -> str:
    """Return a dedicated JWT signing secret."""
    import os
    import secrets as _secrets
    global _TEMP_SECRET_KEY
    secret = os.environ.get("JWT_SECRET_KEY", "").strip()
    if not secret:
        if _TEMP_SECRET_KEY is None:
            log.warning(
                "JWT_SECRET_KEY is not set. Using temporary random key — "
                "all sessions will be invalidated on restart!"
            )
            _TEMP_SECRET_KEY = _secrets.token_urlsafe(32)
        secret = _TEMP_SECRET_KEY
    return secret

def hash_password(password: str) -> str:
    """Hash password with bcrypt (auto-generates salt, handles encoding)."""
    # bcrypt has 72-byte limit; passlib does this internally, we do it explicitly
    pw_bytes = password.encode("utf-8")[:72]
    return bcrypt.hashpw(pw_bytes, bcrypt.gensalt(rounds=12)).decode("ascii")

def verify_password(plain: str, hashed: str) -> bool:
    """Verify plain password against bcrypt hash."""
    try:
        pw_bytes = plain.encode("utf-8")[:72]
        hash_bytes = hashed.encode("ascii")
        return bcrypt.checkpw(pw_bytes, hash_bytes)
    except Exception:
        return False

def create_access_token(
    subject: Any,
    role: str = "superadmin",
    expires_delta: Optional[timedelta] = None,
    extra: Optional[dict] = None,
) -> str:
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    payload = {"sub": str(subject), "role": role, "exp": expire, "jti": str(uuid.uuid4())}
    if extra:
        payload.update(extra)
    return jwt.encode(payload, _secret_key(), algorithm=ALGORITHM)

def decode_access_token(token: str) -> Optional[str]:
    """Returns subject (str) or None if token is invalid/expired."""
    try:
        payload = jwt.decode(token, _secret_key(), algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None

def decode_access_token_full(token: str) -> Optional[dict]:
    """Returns full payload or None if token is invalid/expired."""
    try:
        payload = jwt.decode(token, _secret_key(), algorithms=[ALGORITHM])
        if payload.get("sub") is None:
            return None
        return payload
    except JWTError:
        return None
