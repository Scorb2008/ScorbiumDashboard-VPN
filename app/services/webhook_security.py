import hashlib
import hmac
import ipaddress
import json
from typing import Optional

from app.utils.log import log


async def verify_yookassa_signature(
    body: bytes,
    signature: str,
    secret_key: str,
) -> bool:
    """Verify YooKassa webhook notification signature."""
    try:
        expected = hmac.new(
            secret_key.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)
    except Exception as e:
        log.error(f"YooKassa signature verification error: {e}")
        return False


async def verify_ip_in_list(client_ip: str, allowed_ips: set) -> bool:
    """Check if IP is in allowed set (supports CIDR)."""
    try:
        addr = ipaddress.ip_address(client_ip)
        for allowed in allowed_ips:
            if isinstance(allowed, ipaddress.IPv4Network):
                if addr in allowed:
                    return True
            elif addr == ipaddress.ip_address(allowed):
                return True
        return False
    except ValueError:
        return False


def compute_cryptobot_hmac(token: str, body: dict) -> str:
    """Compute HMAC for CryptoBot webhook per their docs.

    CryptoBot signs the request body with HMAC-SHA256 using the API token as key.
    The signature is passed in the X-Crypto-Pay-API-Signature header.
    """
    sorted_keys = sorted(body.keys())
    parts = []
    for k in sorted_keys:
        v = body[k]
        if isinstance(v, dict):
            v = json.dumps(v, separators=(",", ":"))
        elif v is None:
            v = ""
        else:
            v = str(v)
        parts.append(v)
    data_string = ":".join(parts)
    return hmac.new(
        token.encode(),
        data_string.encode(),
        hashlib.sha256,
    ).hexdigest()


def verify_cryptobot_signature(body: dict, header_sig: str, token: str) -> bool:
    """Verify CryptoBot webhook signature."""
    try:
        expected = compute_cryptobot_hmac(token, body)
        return hmac.compare_digest(expected, header_sig)
    except Exception as e:
        log.error(f"CryptoBot signature verification error: {e}")
        return False
