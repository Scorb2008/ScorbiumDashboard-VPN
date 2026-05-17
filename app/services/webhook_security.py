import hashlib
import hmac
import ipaddress

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


def compute_cryptobot_hmac(token: str, raw_body: bytes) -> str:
    """Compute CryptoBot webhook HMAC over the raw JSON body."""
    secret = hashlib.sha256(token.encode()).digest()
    return hmac.new(secret, raw_body, hashlib.sha256).hexdigest()


def verify_cryptobot_signature(raw_body: bytes, header_sig: str, token: str) -> bool:
    """Verify CryptoBot webhook signature."""
    try:
        expected = compute_cryptobot_hmac(token, raw_body)
        return hmac.compare_digest(expected, header_sig)
    except Exception as e:
        log.error(f"CryptoBot signature verification error: {e}")
        return False
