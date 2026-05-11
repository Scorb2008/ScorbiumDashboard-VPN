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

    # Support both raw 32-byte key and base64-encoded
    if len(key_env) == 44:
        key_bytes = base64.urlsafe_b64decode(key_env)
    else:
        key_bytes = base64.urlsafe_b64encode(key_env.ljust(32, "\x00")[:32].encode())

    _MASTER_KEY = key_env
    _FERNET = Fernet(key_bytes)
    log.info("Encryption engine initialized")
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
        return encrypted


def is_encrypted(value: str) -> bool:
    """Heuristic: encrypted values are base64 and start with 'gAAAAA' (Fernet prefix)."""
    return value.startswith("gAAAAA") and len(value) > 50


def get_encryption_key_info() -> str:
    """Return info about the encryption key status."""
    f = _get_fernet()
    if _MASTER_KEY:
        return "Configured (from ENCRYPTION_KEY)"
    return "Auto-generated (set ENCRYPTION_KEY for persistence)"


def generate_key() -> str:
    """Generate a new base64-encoded Fernet key for .env."""
    return Fernet.generate_key().decode()
