"""
PayPal'ych (pal24.pro) payment service integration.
Docs: https://docs.paypalych.io/
Auth: Bearer token in Authorization header
"""

import json
import http.client
import os
from typing import Any, Dict, Optional
from urllib.parse import urlencode


class PayPalychService:
    """Service for PayPal'ych API integration via pal24.pro."""

    def __init__(self, api_token: Optional[str] = None):
        self.api_token = api_token or os.getenv("PAYPALYCH_API_TOKEN", "")
        self.base_url = "pal24.pro"

    def _get_headers(self, content_type: Optional[str] = None) -> Dict[str, str]:
        """Return request headers with Bearer auth."""
        headers = {
            "Authorization": f"Bearer {self.api_token}",
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    async def _make_request(
        self,
        method: str,
        path: str,
        body: Optional[Dict] = None,
        content_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Make HTTP request to PayPal'ych API."""
        import asyncio

        conn = None
        try:
            conn = http.client.HTTPSConnection(self.base_url, timeout=15)
            if body and content_type == "application/x-www-form-urlencoded":
                payload = urlencode(body, doseq=True)
            else:
                payload = json.dumps(body) if body else ""
            headers = self._get_headers(content_type)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, lambda: conn.request(method, path, payload, headers) or None
            )
            response = await loop.run_in_executor(None, conn.getresponse)
            data = await loop.run_in_executor(None, response.read)
            data = data.decode("utf-8")
            if not data:
                return {"ok": False, "error": f"HTTP {response.status}: empty response"}
            try:
                result = json.loads(data)
            except json.JSONDecodeError:
                return {"ok": False, "error": f"HTTP {response.status}: {data[:500]}"}
            if response.status >= 400:
                return {
                    "ok": False,
                    "error": result.get("error")
                    or result.get("message")
                    or f"HTTP {response.status}",
                    "error_key": result.get("error_key", ""),
                    "status_code": response.status,
                    "raw": result,
                }
            return result
        except Exception as e:
            return {"ok": False, "error": str(e)}
        finally:
            if conn:
                conn.close()

    async def create_bill(
        self,
        amount: float,
        shop_id: str,
        order_id: str = "",
        description: str = "",
        bill_type: str = "normal",
        currency_in: str = "RUB",
        custom: str = "",
        payer_pays_commission: int = 1,
        name: str = "",
        ttl: int = 600,
        success_url: str = "",
        fail_url: str = "",
        payment_method: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a bill via POST /api/v1/bill/create
        Returns: success, link_url, link_page_url, bill_id
        """
        body = {
            "amount": amount,
            "shop_id": shop_id,
            "type": bill_type,
            "currency_in": currency_in,
            "payer_pays_commission": payer_pays_commission,
            "ttl": ttl,
        }
        if order_id:
            body["order_id"] = order_id
        if description:
            body["description"] = description
        if custom:
            body["custom"] = custom
        if name:
            body["name"] = name
        if success_url:
            body["success_url"] = success_url
        if fail_url:
            body["fail_url"] = fail_url
        if payment_method:
            body["payment_method"] = payment_method

        result = await self._make_request(
            "POST",
            "/api/v1/bill/create",
            body,
            content_type="application/x-www-form-urlencoded",
        )
        if result.get("success"):
            return {
                "ok": True,
                "bill_id": result.get("bill_id", ""),
                "link_url": result.get("link_url", ""),
                "link_page_url": result.get("link_page_url", ""),
            }
        return {
            "ok": False,
            "error": result.get("error", "Unknown error"),
            "error_key": result.get("error_key", ""),
        }

    async def get_bill_status(self, bill_id: str) -> Dict[str, Any]:
        """Get bill status via GET /api/v1/bill/status"""
        result = await self._make_request("GET", f"/api/v1/bill/status?id={bill_id}")
        if result.get("success"):
            return {
                "ok": True,
                "id": result.get("id", bill_id),
                "status": result.get("status", "NEW"),
                "active": result.get("active", True),
                "bill_type": result.get("type", "NORMAL"),
                "amount": result.get("amount", 0),
                "currency_in": result.get("currency_in", "RUB"),
                "created_at": result.get("created_at", ""),
            }
        return {"ok": False, "error": result.get("error", "Unknown error")}

    async def toggle_bill_activity(self, bill_id: str, active: bool) -> Dict[str, Any]:
        """Toggle bill activity via POST /api/v1/bill/toggle_activity"""
        body = {"id": bill_id, "active": "1" if active else "0"}
        result = await self._make_request(
            "POST",
            "/api/v1/bill/toggle_activity",
            body,
            content_type="application/x-www-form-urlencoded",
        )
        if result.get("success"):
            return {
                "ok": True,
                "id": result.get("id", bill_id),
                "active": result.get("activity", "false") == "true",
                "status": result.get("status", ""),
            }
        return {"ok": False, "error": result.get("error", "Unknown error")}

    async def get_balance(self) -> Dict[str, Any]:
        """Get merchant balance via GET /api/v1/merchant/balance"""
        result = await self._make_request("GET", "/api/v1/merchant/balance")
        if result.get("success"):
            balances = result.get("balances", [])
            return {"ok": True, "balances": balances}
        return {"ok": False, "error": result.get("error", "Unknown error")}

    async def get_payment_status(self, payment_id: str) -> Dict[str, Any]:
        """Get payment status via GET /api/v1/payment/status"""
        result = await self._make_request(
            "GET", f"/api/v1/payment/status?id={payment_id}"
        )
        if result.get("success"):
            return {
                "ok": True,
                "id": result.get("id", payment_id),
                "bill_id": result.get("bill_id", ""),
                "status": result.get("status", "NEW"),
                "amount": result.get("amount", 0),
                "commission": result.get("commission", 0),
                "account_amount": result.get("account_amount"),
                "account_currency_code": result.get("account_currency_code", ""),
                "currency_in": result.get("currency_in", "RUB"),
                "created_at": result.get("created_at", ""),
            }
        return {"ok": False, "error": result.get("error", "Unknown error")}

    def is_configured(self) -> bool:
        """Check if PayPalych is configured."""
        return bool(self.api_token)

    @staticmethod
    def from_settings(settings: dict) -> Optional["PayPalychService"]:
        token = str(settings.get("paypalych_api_token") or "").strip()
        if token:
            from app.services.encryption import decrypt_value, is_encrypted

            if is_encrypted(token):
                token = decrypt_value(token).strip()
        if not token:
            return None
        return PayPalychService(token)

    async def test_connection(self) -> Dict[str, Any]:
        """Test API connection by getting balance."""
        if not self.is_configured():
            return {"ok": False, "message": "Не настроено: укажите API токен"}
        try:
            result = await self.get_balance()
            if result.get("ok"):
                balances = result.get("balances", [])
                rub_balance = next(
                    (
                        b.get("balance_available", 0)
                        for b in balances
                        if b.get("currency") == "RUB"
                    ),
                    0,
                )
                return {
                    "ok": True,
                    "message": f"✅ PayPalych.io подключен. Баланс: {rub_balance} ₽",
                }
            return {
                "ok": False,
                "message": f"Ошибка: {result.get('error', 'Неизвестно')}",
            }
        except Exception as e:
            return {"ok": False, "message": f"Ошибка подключения: {str(e)}"}
