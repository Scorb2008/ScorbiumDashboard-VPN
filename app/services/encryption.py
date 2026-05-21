"""
Fernet-based encryption for sensitive values stored in the database.
Uses a master key from the ENCRYPTION_KEY env var.
"""
import os
import base64

from cryptography.fernet import Fernet

from app.utils.log import log

_MASTER_KEY: str | None = None
_FERNET: Fernet | None = None


def _build_fernet_from_env(key_env: str) -> Fernet:
    normalized = key_env.strip()
    if not normalized:
        raise ValueError("Encryption key is empty")

    # A standard Fernet key is already urlsafe-base64 encoded and 44 chars long.
    if len(normalized) == 44:
        try:
            decoded = base64.urlsafe_b64decode(normalized.encode())
            if len(decoded) != 32:
                raise ValueError("Decoded Fernet key must be 32 bytes")
            return Fernet(normalized.encode())
        except Exception as exc:
            raise ValueError("Invalid base64 Fernet key") from exc

    raw_bytes = normalized.encode()
    if len(raw_bytes) < 32:
        raw_bytes = raw_bytes.ljust(32, b"\x00")
    else:
        raw_bytes = raw_bytes[:32]
    return Fernet(base64.urlsafe_b64encode(raw_bytes))


def _get_fernet() -> Fernet:
    global _FERNET, _MASTER_KEY
    if _FERNET is not None:
        return _FERNET

    key_env = os.environ.get("ENCRYPTION_KEY", "").strip()
    if not key_env:
        _FERNET = Fernet(Fernet.generate_key())
        log.warning(
            "⚠️ ENCRYPTION_KEY not set — using auto-generated key. "
            "Set ENCRYPTION_KEY in .env for persistent encryption!"
        )
        return _FERNET

    try:
        _FERNET = _build_fernet_from_env(key_env)
        _MASTER_KEY = key_env
        log.info("Encryption engine initialized")
    except Exception as exc:
        _FERNET = Fernet(Fernet.generate_key())
        _MASTER_KEY = None
        log.error(
            "Invalid ENCRYPTION_KEY provided, falling back to in-memory key: %s",
            exc,
        )
    return _FERNET


def encrypt_value(value: str) -> str:
    """Encrypt a string value, returns base64-encoded ciphertext."""
    if not value:
        return value
    f = _get_fernet()
    return f.encrypt(value.encode()).decode()


def decrypt_value(encrypted: str) -> str:
    """Decrypt a previously encrypted string."""
    if not encrypted:
        return encrypted
    f = _get_fernet()
    try:
        return f.decrypt(encrypted.encode()).decode()
    except Exception as e:
        log.error("Decryption failed: %s", e)
        return ""


def is_encrypted(value: str) -> bool:
    """Heuristic: encrypted values are base64 and start with 'gAAAAA' (Fernet prefix)."""
    return value.startswith("gAAAAA") and len(value) > 50


def get_encryption_key_info() -> str:
    """Return info about the encryption key status."""
    _get_fernet()
    if _MASTER_KEY:
        return "Configured (from ENCRYPTION_KEY)"
    return "Auto-generated (set ENCRYPTION_KEY for persistence)"


def generate_key() -> str:
    """Generate a new base64-encoded Fernet key for .env."""
    return Fernet.generate_key().decode()
