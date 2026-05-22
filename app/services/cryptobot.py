import httpx
from typing import Optional
from app.utils.log import log


class CryptoBotService:
    BASE_URL = "https://pay.crypt.bot/api"

    def __init__(self, token: str) -> None:
        self._token = token
        self._headers = {"Crypto-Pay-API-Token": token}

    async def _request(self, method: str, endpoint: str, **kwargs) -> Optional[dict]:
        url = f"{self.BASE_URL}/{endpoint}"
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.request(
                    method, url, headers=self._headers, **kwargs
                )
                data = resp.json()
                if data.get("ok"):
                    return data.get("result")
                log.error(f"CryptoBot error: {data.get('error')}")
                return None
        except Exception as e:
            log.error(f"CryptoBot request error: {e}")
            return None

    async def get_me(self) -> Optional[dict]:
        return await self._request("GET", "getMe")

    async def create_invoice(
        self,
        amount: float,
        currency: str = "USDT",
        description: str = "VPN Subscription",
        payload: str = "",
        expires_in: int = 3600,
    ) -> Optional[dict]:
        """
        Создать счёт для оплаты.
        Возвращает dict с полями: invoice_id, pay_url, status
        """
        params = {
            "asset": currency,
            "amount": str(round(amount, 2)),
            "description": description,
            "payload": payload,
            "expires_in": expires_in,
        }
        return await self._request("POST", "createInvoice", json=params)

    async def get_invoice(self, invoice_id: int) -> Optional[dict]:
        """Получить статус счёта."""
        result = await self._request(
            "GET", "getInvoices", params={"invoice_ids": str(invoice_id)}
        )
        if result and result.get("items"):
            return result["items"][0]
        return None

    async def get_exchange_rates(self) -> Optional[list]:
        """Получить курсы валют."""
        return await self._request("GET", "getExchangeRates")

    async def rub_to_usdt(self, rub_amount: float) -> float:
        """Конвертировать рубли в USDT по текущему курсу."""
        rates = await self.get_exchange_rates()
        if not rates:
            # Fallback: примерный курс
            return round(rub_amount / 90, 2)
        for rate in rates:
            if rate.get("source") == "RUB" and rate.get("target") == "USDT":
                try:
                    rate_val = float(rate.get("rate", 90))
                    return round(rub_amount / rate_val, 2)
                except Exception:
                    pass
        return round(rub_amount / 90, 2)

    @staticmethod
    def from_settings(settings: dict) -> Optional["CryptoBotService"]:
        """Создать сервис из bot_settings. Возвращает None если токен не задан."""
        token = settings.get("cryptobot_token", "").strip()
        if not token:
            return None
        return CryptoBotService(token)
