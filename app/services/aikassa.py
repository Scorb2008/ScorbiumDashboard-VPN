"""
AiKassa payment service.
Docs: https://aikassa.ru/wiki
"""
import httpx
from typing import Optional
from app.utils.log import log


class AiKassaService:
    BASE_URL = "https://aikassa.ru/api/v1"

    def __init__(self, shop_id: str, token: str) -> None:
        self._shop_id = shop_id
        self._token = token
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    async def _request(self, method: str, endpoint: str, **kwargs) -> Optional[dict]:
        url = f"{self.BASE_URL}/{endpoint}"
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.request(method, url, headers=self._headers, **kwargs)
                if resp.status_code == 401:
                    log.error("AiKassa: unauthorized (invalid token)")
                    return None
                return resp.json()
        except Exception as e:
            log.error(f"AiKassa request error: {e}")
            return None

    async def get_shop_info(self) -> Optional[dict]:
        """Получить информацию о магазине — используется для проверки подключения."""
        return await self._request("GET", f"shops/{self._shop_id}")

    async def create_invoice(
        self,
        order_id: str,
        amount: float,
        description: str = "VPN Subscription",
        currency: str = "RUB",
    ) -> Optional[dict]:
        payload = {
            "shopId": self._shop_id,
            "orderId": order_id,
            "amount": amount,
            "currency": currency,
            "description": description,
        }
        return await self._request("POST", "invoices", json=payload)

    async def get_invoice(self, invoice_id: str) -> Optional[dict]:
        """Fetch invoice status from AiKassa before trusting a webhook."""
        return await self._request("GET", f"invoices/{invoice_id}")

    @staticmethod
    def from_settings(settings: dict) -> Optional["AiKassaService"]:
        shop_id = (settings.get("aikassa_shop_id") or "").strip()
        token = (settings.get("aikassa_token") or "").strip()
        if not shop_id or not token:
            return None
        return AiKassaService(shop_id, token)
