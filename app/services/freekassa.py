"""
FreeKassa payment service.
API docs: https://docs.freekassa.com/
API base: https://api.fk.life/v1/
Payment form: https://pay.fk.money/
"""

import hashlib
import hmac
import time
import httpx
from typing import Optional
from app.utils.log import log


def _decode_setting(settings: dict, key: str) -> str:
    value = str(settings.get(key) or "").strip()
    if not value:
        return ""

    from app.services.encryption import decrypt_value, is_encrypted

    if is_encrypted(value):
        return decrypt_value(value).strip()
    return value


class FreeKassaService:
    API_URL = "https://api.fk.life/v1"
    PAY_URL = "https://pay.fk.money/"

    ALLOWED_IPS = {
        "168.119.157.136",
        "168.119.60.227",
        "178.154.197.79",
        "51.250.54.238",
    }

    def __init__(
        self,
        shop_id: str,
        api_key: str,
        secret_word_1: str = "",
        secret_word_2: str = "",
    ) -> None:
        self._shop_id = shop_id
        self._api_key = api_key
        self._secret_word_1 = secret_word_1
        self._secret_word_2 = secret_word_2

    # ── Подпись API запросов (HMAC-SHA256) ────────────────────────────────────

    def _sign_api(self, data: dict) -> str:
        """
        Подпись для API: сортируем ключи, конкатенируем значения через |,
        хешируем HMAC-SHA256 с api_key.
        """
        sign_data = {k: v for k, v in data.items() if k != "signature"}

        sorted_vals = "|".join(str(sign_data[k]) for k in sorted(sign_data.keys()))

        log.debug(f"Signature string: {sorted_vals}")
        log.debug(f"API Key: {self._api_key[:5]}...")

        signature = hmac.new(
            self._api_key.encode("utf-8"), sorted_vals.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        log.debug(f"Generated signature: {signature}")
        return signature

    def _make_payload(self, extra: dict) -> dict:
        """Базовый payload с shopId, nonce и signature."""
        nonce = int(time.time())

        payload = {"shopId": int(self._shop_id), "nonce": nonce, **extra}

        payload["signature"] = self._sign_api(payload)

        ordered_payload = {
            "shopId": payload["shopId"],
            "nonce": payload["nonce"],
            **{
                k: v
                for k, v in payload.items()
                if k not in ["shopId", "nonce", "signature"]
            },
            "signature": payload["signature"],
        }

        log.debug(f"Final payload: {ordered_payload}")
        return ordered_payload

    # ── Подпись платёжной формы (MD5) ────────────────────────────────────────

    def sign_payment_form(
        self, amount: float, order_id: str, currency: str = "RUB"
    ) -> str:
        """
        MD5 от "shop_id:amount:secret_word_1:currency:order_id"
        """
        raw = f"{self._shop_id}:{amount}:{self._secret_word_1}:{currency}:{order_id}"
        return hashlib.md5(raw.encode(), usedforsecurity=False).hexdigest()

    def sign_notification(self, merchant_id: str, amount: str, order_id: str) -> str:
        """
        MD5 от "merchant_id:amount:secret_word_2:order_id" — для проверки webhook.
        """
        raw = f"{merchant_id}:{amount}:{self._secret_word_2}:{order_id}"
        return hashlib.md5(raw.encode(), usedforsecurity=False).hexdigest()

    def verify_notification(
        self, merchant_id: str, amount: str, order_id: str, sign: str
    ) -> bool:
        """Проверяет подпись входящего webhook от FreeKassa."""
        expected = self.sign_notification(merchant_id, amount, order_id)
        return hmac.compare_digest(expected, sign.lower())

    # ── Платёжная форма ───────────────────────────────────────────────────────

    def create_payment_url(
        self,
        order_id: str,
        amount: float,
        currency: str = "RUB",
        email: str = "",
        lang: str = "ru",
    ) -> str:
        """Генерирует URL для перенаправления пользователя на оплату."""
        sign = self.sign_payment_form(amount, order_id, currency)
        params = (
            f"m={self._shop_id}&oa={amount}&currency={currency}"
            f"&o={order_id}&s={sign}&lang={lang}"
        )
        if email:
            params += f"&em={email}"
        return f"{self.PAY_URL}?{params}"

    # ── API методы ────────────────────────────────────────────────────────────

    async def _post(self, endpoint: str, extra: dict) -> Optional[dict]:
        url = f"{self.API_URL}/{endpoint}"
        payload = self._make_payload(extra)
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(url, json=payload)
                data = resp.json()
                if data.get("type") == "error":
                    log.error(f"FreeKassa API error [{endpoint}]: {data}")
                return data
        except Exception as e:
            log.error(f"FreeKassa request error [{endpoint}]: {e}")
            return None

    async def get_balance(self) -> Optional[dict]:
        """Получить баланс магазина — используется для проверки подключения."""
        return await self._post("balance", {})

    async def test_connection(self) -> dict:
        """Проверяет доступность API и валидность сохранённых ключей."""
        data = await self.get_balance()
        if not data:
            return {"ok": False, "error": "FreeKassa API не ответила"}
        if data.get("type") == "error":
            return {
                "ok": False,
                "error": data.get("msg")
                or data.get("message")
                or "Ошибка FreeKassa API",
            }
        return {"ok": True, "data": data}

    async def create_order(
        self,
        payment_id: str,
        amount: float,
        currency: str = "RUB",
        currency_id: int = 36,
        email: str = "user@vpn.bot",
        ip: str = "127.0.0.1",
        notification_url: str = "",
        success_url: str = "",
        failure_url: str = "",
    ) -> Optional[dict]:
        """
        Создать заказ через API и получить ссылку на оплату.
        Возвращает dict с полями: orderId, orderHash, location (URL оплаты).
        """
        extra: dict = {
            "paymentId": payment_id,
            "i": currency_id,
            "email": email,
            "ip": ip,
            "amount": amount,
            "currency": currency,
        }
        if notification_url:
            extra["notification_url"] = notification_url
        if success_url:
            extra["success_url"] = success_url
        if failure_url:
            extra["failure_url"] = failure_url
        return await self._post("orders/create", extra)

    async def get_orders(self, payment_id: str) -> Optional[dict]:
        """Получить список заказов по paymentId (номер заказа в нашем магазине)."""
        return await self._post("orders", {"paymentId": payment_id})

    @staticmethod
    def from_settings(settings: dict) -> Optional["FreeKassaService"]:
        shop_id = str(settings.get("freekassa_shop_id") or "").strip()
        api_key = _decode_setting(settings, "freekassa_api_key")
        if not shop_id or not api_key:
            return None
        secret1 = _decode_setting(settings, "freekassa_secret_word_1")
        secret2 = _decode_setting(settings, "freekassa_secret_word_2")
        return FreeKassaService(shop_id, api_key, secret1, secret2)
