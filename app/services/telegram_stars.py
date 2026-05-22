from aiogram import Bot
from aiogram.types import LabeledPrice

from app.utils.log import log


class TelegramStarsService:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def send_invoice(
        self,
        chat_id: int,
        title: str,
        description: str,
        payload: str,
        stars_amount: int,
    ) -> bool:
        try:
            await self.bot.send_invoice(
                chat_id=chat_id,
                title=title,
                description=description,
                payload=payload,
                currency="XTR",
                prices=[LabeledPrice(label=title, amount=stars_amount)],
            )
            return True
        except Exception as e:
            log.error(f"Stars invoice error for {chat_id}: {e}")
            return False

    @staticmethod
    def rub_to_stars(rub_amount: float, rate: float = 1.5) -> int:
        if rate <= 0:
            rate = 1.5
        return max(1, round(rub_amount / rate))

    @staticmethod
    async def get_rate(session) -> float:
        from app.services.bot_settings import BotSettingsService

        val = await BotSettingsService(session).get("stars_rate")
        try:
            return float(val) if val else 1.5
        except (ValueError, TypeError):
            return 1.5
