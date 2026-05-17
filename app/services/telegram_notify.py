from typing import Optional
import httpx

from app.core.config import config
from app.utils.log import log


class TelegramNotifyService:
    def __init__(self) -> None:
        self._token = config.telegram.telegram_bot_token.get_secret_value()
        self._base = f"https://api.telegram.org/bot{self._token}"
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=10)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def send_message(
        self,
        chat_id: int,
        text: str,
        parse_mode: str = "HTML",
        disable_notification: bool = False,
    ) -> bool:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_notification": disable_notification,
        }
        try:
            client = await self._get_client()
            resp = await client.post(f"{self._base}/sendMessage", json=payload)
            if resp.status_code == 200:
                return True
            log.warning("Telegram send failed for %s: %s", chat_id, resp.text)
            return False
        except Exception as e:
            log.error("Telegram notify error for %s: %s", chat_id, e)
            return False

    async def send_photo(
        self,
        chat_id: int,
        photo: str,
        caption: str,
        parse_mode: str = "HTML",
        disable_notification: bool = False,
    ) -> bool:
        payload = {
            "chat_id": chat_id,
            "photo": photo,
            "caption": caption,
            "parse_mode": parse_mode,
            "disable_notification": disable_notification,
        }
        try:
            client = await self._get_client()
            resp = await client.post(f"{self._base}/sendPhoto", json=payload)
            if resp.status_code == 200:
                return True
            log.warning("Telegram photo send failed for %s: %s", chat_id, resp.text)
            return False
        except Exception as e:
            log.error("Telegram notify photo error for %s: %s", chat_id, e)
            return False

    async def broadcast(
        self,
        user_ids: list[int],
        text: str,
        parse_mode: str = "HTML",
    ) -> tuple[int, int]:
        """Returns (sent_count, failed_count)."""
        sent, failed = 0, 0
        for uid in user_ids:
            ok = await self.send_message(uid, text, parse_mode)
            if ok:
                sent += 1
            else:
                failed += 1
        return sent, failed

    async def get_bot_info(self) -> Optional[dict]:
        try:
            client = await self._get_client()
            resp = await client.get(f"{self._base}/getMe")
            if resp.status_code == 200:
                return resp.json().get("result")
        except Exception as e:
            log.error("getMe failed: %s", e)
        return None
