"""
Platega.io payment service integration.
Docs: https://docs.platega.io/
Auth: X-MerchantId + X-Secret headers
Base URL: https://app.platega.io
"""
import os
import json
import http.client
from typing import Optional, Dict, Any


class PlategaService:
    """Service for Platega.io API integration."""

    def __init__(self, merchant_id: Optional[str] = None, secret: Optional[str] = None):
        self.merchant_id = merchant_id or os.getenv("PLATEGA_MERCHANT_ID", "")
        self.api_secret = secret or os.getenv("PLATEGA_SECRET", "")
        self.base_url = "app.platega.io"

    def _get_headers(self) -> Dict[str, str]:
        """Return request headers with auth."""
        return {
            "X-MerchantId": self.merchant_id,
            "X-Secret": self.api_secret,
            "Content-Type": "application/json"
        }

    async def _make_request(
        self,
        method: str,
        path: str,
        body: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Make HTTP request to Platega API."""
        import asyncio
        conn = None
        try:
            conn = http.client.HTTPSConnection(self.base_url, timeout=15)
            payload = json.dumps(body) if body else ""
            headers = self._get_headers()
            # Run blocking I/O in executor
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: conn.request(method, path, payload, headers) or None
            )
            response = await loop.run_in_executor(None, conn.getresponse)
            data = await loop.run_in_executor(None, response.read)
            data = data.decode("utf-8")
            if not data:
                return {"ok": False, "error": "Empty response"}
            result = json.loads(data)
            return result
        except Exception as e:
            return {"ok": False, "error": str(e)}
        finally:
            if conn:
                conn.close()

    async def create_transaction(
        self,
        amount: float,
        currency: str = "RUB",
        description: str = "",
        return_url: str = "",
        failed_url: str = "",
        payload_data: str = "",
        payment_method: Optional[int] = None,
        user_telegram_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a transaction via POST /v2/transaction/process
        Returns: transactionId, status, url/redirect, expiresIn, rate
        """
        body = {
            "paymentDetails": {
                "amount": amount,
                "currency": currency,
            },
        }
        if description:
            body["description"] = description
        if return_url:
            body["return"] = return_url
        if failed_url:
            body["failedUrl"] = failed_url
        if payload_data:
            body["payload"] = payload_data
        if payment_method is not None:
            body["paymentMethod"] = payment_method
        # Add Telegram ID for Stars payments
        if user_telegram_id and user_id:
            body["description"] = f"TgId:{user_telegram_id} UserId:{user_id} {description}".strip()

        result = await self._make_request("POST", "/v2/transaction/process", body)
        if "transactionId" in result:
            return {
                "ok": True,
                "transaction_id": result.get("transactionId", ""),
                "status": result.get("status", "PENDING"),
                "url": result.get("url") or result.get("redirect", ""),
                "expires_in": result.get("expiresIn", ""),
                "rate": result.get("rate", 0),
                "payment_method": result.get("paymentMethod", ""),
                "qr": result.get("qr", ""),
            }
        return {"ok": False, "error": result.get("error", "Unknown error")}

    async def get_transaction_status(self, transaction_id: str) -> Dict[str, Any]:
        """Get transaction status via GET /v2/transaction/{id}"""
        result = await self._make_request("GET", f"/v2/transaction/{transaction_id}")
        if "id" in result or "status" in result:
            return {
                "ok": True,
                "transaction_id": result.get("id", transaction_id),
                "status": result.get("status", "PENDING"),
                "payment_details": result.get("paymentDetails", {}),
                "payment_method": result.get("paymentMethod", ""),
                "expires_in": result.get("expiresIn", ""),
            }
        return {"ok": False, "error": result.get("error", "Unknown error")}

    async def get_qr_code(self, transaction_id: str) -> Dict[str, Any]:
        """Get QR code for H2H transaction via GET /v2/h2h/{id}"""
        result = await self._make_request("GET", f"/v2/h2h/{transaction_id}")
        if "amount" in result:
            return {
                "ok": True,
                "amount": result.get("amount", 0),
                "qr": result.get("qr", ""),
            }
        return {"ok": False, "error": result.get("error", "Unknown error")}

    async def get_rates(
        self,
        payment_method: int,
        currency_from: str = "RUB",
        currency_to: str = "USDT",
    ) -> Dict[str, Any]:
        """Get exchange rate via GET /v2/rates/payment_method_rate"""
        path = f"/v2/rates/payment_method_rate?paymentMethod={payment_method}&currencyFrom={currency_from}&currencyTo={currency_to}"
        result = await self._make_request("GET", path)
        if "rate" in result:
            return {
                "ok": True,
                "payment_method": result.get("paymentMethod", payment_method),
                "currency_from": result.get("currencyFrom", currency_from),
                "currency_to": result.get("currencyTo", currency_to),
                "rate": result.get("rate", 0),
                "updated_at": result.get("updatedAt", ""),
            }
        return {"ok": False, "error": result.get("error", "Unknown error")}

    async def get_balance(self) -> Dict[str, Any]:
        """Get merchant balances via GET /v2/balance/all"""
        result = await self._make_request("GET", "/v2/balance/all")
        if isinstance(result, list):
            return {"ok": True, "balances": result}
        return {"ok": False, "error": result.get("error", "Unknown error")}

    def is_configured(self) -> bool:
        """Check if Platega is configured."""
        return bool(self.merchant_id and self.api_secret)

    async def test_connection(self) -> Dict[str, Any]:
        """Test API connection by getting balance."""
        if not self.is_configured():
            return {"ok": False, "message": "Не настроено: укажите MerchantId и Secret"}
        try:
            result = await self.get_balance()
            if result.get("ok"):
                return {"ok": True, "message": "✅ Platega.io подключен"}
            return {"ok": False, "message": f"Ошибка: {result.get('error', 'Неизвестно')}"}
        except Exception as e:
            return {"ok": False, "message": f"Ошибка подключения: {str(e)}"}
