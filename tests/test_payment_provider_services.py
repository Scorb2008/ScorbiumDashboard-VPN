from app.services.paypalych import PayPalychService
from app.services.encryption import encrypt_value
from app.services.platega import PlategaService
from app.services.cryptobot import CryptoBotService
from app.services.freekassa import FreeKassaService
from app.services.aikassa import AiKassaService


async def test_platega_service_awaits_request(monkeypatch):
    service = PlategaService("merchant", "secret")

    async def fake_request(method, path, body=None):
        assert method == "POST"
        assert path == "/v2/transaction/process"
        assert body["paymentDetails"]["amount"] == 199
        return {"transactionId": "tx_1", "status": "PENDING", "url": "https://pay"}

    monkeypatch.setattr(service, "_make_request", fake_request)

    result = await service.create_transaction(amount=199, payload_data="pl_1_2")

    assert result["ok"] is True
    assert result["transaction_id"] == "tx_1"
    assert result["url"] == "https://pay"


async def test_platega_service_uses_direct_method_endpoint_when_payment_method_selected(
    monkeypatch,
):
    service = PlategaService("merchant", "secret")

    async def fake_request(method, path, body=None):
        assert method == "POST"
        assert path == "/transaction/process"
        assert body["paymentMethod"] == 2
        return {
            "transactionId": "tx_2",
            "status": "PENDING",
            "redirect": "https://pay-method",
            "paymentMethod": "SBPQR",
        }

    monkeypatch.setattr(service, "_make_request", fake_request)

    result = await service.create_transaction(
        amount=299,
        payment_method=2,
        description="VPN payment",
    )

    assert result["ok"] is True
    assert result["transaction_id"] == "tx_2"
    assert result["url"] == "https://pay-method"
    assert result["payment_method"] == "SBPQR"


async def test_platega_status_and_balance_paths_match_docs(monkeypatch):
    service = PlategaService("merchant-uuid", "secret")
    calls = []

    async def fake_request(method, path, body=None):
        calls.append((method, path, body))
        if path == "/transaction/tx-123":
            return {
                "id": "tx-123",
                "status": "CONFIRMED",
                "paymentDetails": {"amount": 100},
            }
        if path == "/h2h/tx-123":
            return {"amount": 100, "qr": "https://qr"}
        if path.startswith("/rates/payment_method_rate?"):
            return {
                "paymentMethod": 2,
                "currencyFrom": "RUB",
                "currencyTo": "USDT",
                "rate": 0.01,
            }
        if path == "/balance/all":
            return [{"amount": 10, "currency": "RUB"}]
        raise AssertionError(path)

    monkeypatch.setattr(service, "_make_request", fake_request)

    status = await service.get_transaction_status("tx-123")
    qr = await service.get_qr_code("tx-123")
    rates = await service.get_rates(2)
    balance = await service.get_balance()

    assert status["ok"] is True
    assert qr["ok"] is True
    assert rates["ok"] is True
    assert balance["ok"] is True
    assert calls[0][1] == "/transaction/tx-123"
    assert calls[1][1] == "/h2h/tx-123"
    assert "merchantId=merchant-uuid" in calls[2][1]
    assert calls[3][1] == "/balance/all"


def test_platega_from_settings_decrypts_sensitive_values():
    service = PlategaService.from_settings(
        {
            "platega_merchant_id": encrypt_value("merchant-id"),
            "platega_secret": encrypt_value("secret-key"),
        }
    )

    assert service is not None
    assert service.merchant_id == "merchant-id"
    assert service.api_secret == "secret-key"


async def test_paypalych_service_awaits_request(monkeypatch):
    service = PayPalychService("token")

    async def fake_request(method, path, body=None, content_type=None):
        assert method == "POST"
        assert path == "/api/v1/bill/create"
        assert body["amount"] == 299
        assert content_type == "application/x-www-form-urlencoded"
        return {
            "success": True,
            "bill_id": "bill_1",
            "link_url": "https://pay",
            "link_page_url": "https://page",
        }

    monkeypatch.setattr(service, "_make_request", fake_request)

    result = await service.create_bill(amount=299, shop_id="shop", custom="pp_1_2")

    assert result["ok"] is True
    assert result["bill_id"] == "bill_1"
    assert result["link_url"] == "https://pay"


def test_paypalych_from_settings_decrypts_token():
    from app.services.encryption import encrypt_value

    service = PayPalychService.from_settings(
        {"paypalych_api_token": encrypt_value("bearer-token")}
    )

    assert service is not None
    assert service.api_token == "bearer-token"


def test_cryptobot_from_settings_decrypts_token():
    service = CryptoBotService.from_settings(
        {"cryptobot_token": encrypt_value("crypto-token")}
    )

    assert service is not None
    assert service._token == "crypto-token"


def test_freekassa_from_settings_decrypts_sensitive_values():
    service = FreeKassaService.from_settings(
        {
            "freekassa_shop_id": "shop-1",
            "freekassa_api_key": encrypt_value("api-key"),
            "freekassa_secret_word_1": encrypt_value("secret-1"),
            "freekassa_secret_word_2": encrypt_value("secret-2"),
        }
    )

    assert service is not None
    assert service._shop_id == "shop-1"
    assert service._api_key == "api-key"
    assert service._secret_word_1 == "secret-1"
    assert service._secret_word_2 == "secret-2"


def test_aikassa_from_settings_decrypts_token():
    service = AiKassaService.from_settings(
        {
            "aikassa_shop_id": "shop-42",
            "aikassa_token": encrypt_value("aikassa-token"),
        }
    )

    assert service is not None
    assert service._shop_id == "shop-42"
    assert service._token == "aikassa-token"
