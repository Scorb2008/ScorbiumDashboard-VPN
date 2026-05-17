import hashlib
import hmac
from types import SimpleNamespace

from app.api.v1.payments import _get_yookassa_webhook_secret
from app.services.webhook_security import compute_cryptobot_hmac, verify_cryptobot_signature


def test_compute_cryptobot_hmac_uses_raw_json_body():
    token = "12345:secret-token"
    raw_body = b'{"update_type":"invoice_paid","payload":{"invoice_id":1}}'

    expected = hmac.new(
        hashlib.sha256(token.encode()).digest(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    assert compute_cryptobot_hmac(token, raw_body) == expected


def test_verify_cryptobot_signature_rejects_modified_body():
    token = "12345:secret-token"
    raw_body = b'{"update_type":"invoice_paid","payload":{"invoice_id":1}}'
    signature = compute_cryptobot_hmac(token, raw_body)

    assert verify_cryptobot_signature(raw_body, signature, token) is True
    assert (
        verify_cryptobot_signature(
            b'{"update_type":"invoice_paid","payload":{"invoice_id":2}}',
            signature,
            token,
        )
        is False
    )


async def test_get_yookassa_webhook_secret_falls_back_to_env(session, monkeypatch):
    monkeypatch.setattr(
        "app.api.v1.payments.config",
        SimpleNamespace(
            yookassa=SimpleNamespace(
                yookassa_secret_key=SimpleNamespace(get_secret_value=lambda: "env_secret_12345")
            )
        ),
    )

    assert await _get_yookassa_webhook_secret(session) == "env_secret_12345"
