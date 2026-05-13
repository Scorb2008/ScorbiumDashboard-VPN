from app.services.paypalych import PayPalychService
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
