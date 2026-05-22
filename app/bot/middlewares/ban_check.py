from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from app.core.config import config
from app.core.database import AsyncSessionFactory
from app.services.user import UserService
from app.services.bot_settings import BotSettingsService


class BanCheckMiddleware(BaseMiddleware):
    """Block banned users and enforce bot_enabled toggle."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        # Extract user_id from the update
        user_id: int | None = None
        if isinstance(event, Update):
            if event.message:
                user_id = (
                    event.message.from_user.id if event.message.from_user else None
                )
            elif event.callback_query:
                user_id = event.callback_query.from_user.id
            elif event.pre_checkout_query:
                user_id = event.pre_checkout_query.from_user.id

        if user_id is None:
            return await handler(event, data)

        # Admins bypass bot_enabled check
        if user_id in config.telegram.telegram_admin_ids:
            return await handler(event, data)

        async with AsyncSessionFactory() as session:
            settings_svc = BotSettingsService(session)

            # Check bot_enabled
            bot_enabled = await settings_svc.get("bot_enabled")
            if bot_enabled == "0":
                msg = (
                    await settings_svc.get("bot_disabled_message")
                    or "🔧 Бот временно отключён. Попробуйте позже."
                )
                await _reply(event, msg)
                return

            # Check ban
            user = await UserService(session).get_by_id(user_id)
            if user and user.is_banned:
                ban_msg = (
                    await settings_svc.get("ban_message")
                    or "🚫 Ваш аккаунт заблокирован."
                )
                await _reply(event, ban_msg)
                return

        return await handler(event, data)


async def _reply(event: Update, text: str) -> None:
    """Send a message back to the user."""
    try:
        if isinstance(event, Update):
            if event.message:
                await event.message.answer(text)
            elif event.callback_query:
                await event.callback_query.answer(text[:200], show_alert=True)
    except Exception:
        pass
