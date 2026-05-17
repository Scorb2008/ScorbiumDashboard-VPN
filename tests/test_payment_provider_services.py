from app.services.paypalych import PayPalychService
from app.services.encryption import encrypt_value
from app.services.platega import PlategaService


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


async def test_platega_service_uses_direct_method_endpoint_when_payment_method_selected(monkeypatch):
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
            return {"id": "tx-123", "status": "CONFIRMED", "paymentDetails": {"amount": 100}}
        if path == "/h2h/tx-123":
            return {"amount": 100, "qr": "https://qr"}
        if path.startswith("/rates/payment_method_rate?"):
            return {"paymentMethod": 2, "currencyFrom": "RUB", "currencyTo": "USDT", "rate": 0.01}
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

    async def fake_request(method, path, body=None):
        assert method == "POST"
        assert path == "/api/v1/bill/create"
        assert body["amount"] == 299
        return {"success": True, "bill_id": "bill_1", "link_url": "https://pay", "link_page_url": "https://page"}

    monkeypatch.setattr(service, "_make_request", fake_request)

    result = await service.create_bill(amount=299, shop_id="shop", custom="pp_1_2")

    assert result["ok"] is True
    assert result["bill_id"] == "bill_1"
    assert result["link_url"] == "https://pay"
