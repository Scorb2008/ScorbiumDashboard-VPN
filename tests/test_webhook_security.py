import hashlib
import hmac
import json
from types import SimpleNamespace

from starlette.requests import Request

from app.api.v1.payments import _get_yookassa_webhook_secret, _platega_headers_match, platega_webhook
from app.models.bot_settings import BotSettings
from app.models.payment import Payment, PaymentProvider, PaymentStatus, PaymentType
from app.models.plan import Plan
from app.models.user import User
from app.services.bot_settings import reset_bot_settings_cache
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


def _make_json_request(path: str, payload: dict, headers: dict[str, str]) -> Request:
    body = json.dumps(payload).encode("utf-8")
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": [
            (key.lower().encode("utf-8"), value.encode("utf-8"))
            for key, value in headers.items()
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 443),
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def test_platega_headers_match_expected_credentials():
    request = _make_json_request(
        "/api/v1/payments/webhook/platega",
        {"id": "tx-1"},
        {"X-MerchantId": "merchant", "X-Secret": "secret"},
    )

    assert _platega_headers_match(
        request,
        {"platega_merchant_id": "merchant", "platega_secret": "secret"},
    ) is True
    assert _platega_headers_match(
        request,
        {"platega_merchant_id": "merchant", "platega_secret": "other"},
    ) is False


async def test_platega_webhook_uses_confirmed_status_and_documented_id_field(session, monkeypatch):
    await reset_bot_settings_cache()
    user = User(id=987654321, username="platega-user", full_name="Platega User")
    plan = Plan(
        id=77,
        name="Test Plan",
        slug="test-plan",
        description="Test",
        duration_days=30,
        price=100,
        is_active=True,
    )
    payment = Payment(
        id=555,
        user_id=user.id,
        provider=PaymentProvider.PLATEGA.value,
        payment_type=PaymentType.SUBSCRIPTION.value,
        amount=100,
        currency="RUB",
        status=PaymentStatus.PENDING.value,
    )
    session.add_all(
        [
            user,
            plan,
            payment,
            BotSettings(key="platega_merchant_id", value="merchant"),
            BotSettings(key="platega_secret", value="secret"),
        ]
    )
    await session.commit()

    sent = []

    class _Notify:
        async def send_message(self, chat_id, message):
            sent.append((chat_id, message))

    async def fake_verify_remote(*args, **kwargs):
        assert kwargs["external_id"] == "tx-confirmed"
        assert kwargs["payment_id"] == 555
        return True

    async def fake_finalize(*args, **kwargs):
        fake_payment = SimpleNamespace(user_id=user.id)
        fake_key = SimpleNamespace(access_url="https://vpn-key", expires_at=None)
        return fake_payment, fake_key, True, True

    monkeypatch.setattr("app.api.v1.payments._verify_remote_provider_payment", fake_verify_remote)
    monkeypatch.setattr("app.api.v1.payments._finalize_subscription_payment", fake_finalize)
    monkeypatch.setattr("app.services.telegram_notify.TelegramNotifyService", lambda: _Notify())

    request = _make_json_request(
        "/api/v1/payments/webhook/platega",
        {
            "id": "tx-confirmed",
            "amount": 100,
            "currency": "RUB",
            "status": "CONFIRMED",
            "paymentMethod": 2,
            "payload": "pl_555_77",
        },
        {"X-MerchantId": "merchant", "X-Secret": "secret"},
    )

    result = await platega_webhook(request, db=session)

    assert result == "OK"
    assert sent
