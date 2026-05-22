"""
Middleware: проверяет подписку пользователя на обязательный канал.
Fail-closed: если проверка не прошла или упала с ошибкой — блокируем.
Администраторы бота проходят без проверки.
"""

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware, Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram.types import TelegramObject, Update, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.core.config import config
from app.core.database import AsyncSessionFactory
from app.services.bot_settings import BotSettingsService
from app.utils.log import log

_SUBSCRIBED_STATUSES = {"member", "administrator", "creator"}

_BLOCK_TEXT = (
    "📢 <b>Для использования бота необходимо подписаться на {channel_name}.</b>\n\n"
    "После подписки нажмите кнопку «✅ Я подписался»."
)


async def _send_subscribe_prompt(
    event: Update, channel_name: str, channel_link: str
) -> None:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📢 Подписаться", url=channel_link))
    builder.row(
        InlineKeyboardButton(text="✅ Я подписался", callback_data="channel:check")
    )
    kb = builder.as_markup()
    text = _BLOCK_TEXT.format(channel_name=channel_name)
    try:
        if event.message:
            await event.message.answer(text, reply_markup=kb, parse_mode="HTML")
        elif event.callback_query:
            if event.callback_query.data == "channel:check":
                await event.callback_query.answer(
                    "❌ Вы ещё не подписались на канал.", show_alert=True
                )
            else:
                try:
                    await event.callback_query.message.edit_text(
                        text, reply_markup=kb, parse_mode="HTML"
                    )
                except Exception:
                    await event.callback_query.answer(
                        "📢 Сначала подпишитесь на канал.", show_alert=True
                    )
    except Exception as e:
        log.warning(f"[channel_check] Failed to send prompt: {e}")


class ChannelCheckMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if not isinstance(event, Update):
            return await handler(event, data)

        # Extract user_id
        user_id: int | None = None
        if event.message:
            user_id = event.message.from_user.id if event.message.from_user else None
        elif event.callback_query:
            user_id = event.callback_query.from_user.id

        if user_id is None:
            return await handler(event, data)

        # Admins always pass
        if user_id in config.telegram.telegram_admin_ids:
            return await handler(event, data)

        # Load channel setting from DB
        async with AsyncSessionFactory() as session:
            svc = BotSettingsService(session)
            channel_id_raw = await svc.get("required_channel_id")
            channel_name = (
                await svc.get("required_channel_name") or ""
            ).strip() or "наш канал"

        # No channel configured — pass through
        if not channel_id_raw or not channel_id_raw.strip():
            return await handler(event, data)

        channel_id_str = channel_id_raw.strip()

        # Поддерживаем username (@channel) и числовой ID (-100...)
        if channel_id_str.startswith("@"):
            channel_id = channel_id_str  # передаём как строку в get_chat_member
            channel_link = f"https://t.me/{channel_id_str.lstrip('@')}"
        else:
            try:
                channel_id = int(channel_id_str)
            except ValueError:
                log.warning(
                    f"[channel_check] Invalid channel_id value: {channel_id_str!r}"
                )
                return await handler(event, data)
            clean_id = str(channel_id).lstrip("-").removeprefix("100")
            channel_link = f"https://t.me/c/{clean_id}"

        # Get bot instance from aiogram data
        bot: Bot | None = data.get("bot")
        if bot is None:
            log.error(
                "[channel_check] Bot instance not found in middleware data — check registration"
            )
            # Fail closed
            await _send_subscribe_prompt(event, channel_name, channel_link)
            return

        # Check membership — FAIL CLOSED on any error
        is_subscribed = False
        try:
            member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
            is_subscribed = member.status in _SUBSCRIBED_STATUSES
            log.debug(
                f"[channel_check] user={user_id} channel={channel_id} status={member.status}"
            )
        except TelegramForbiddenError:
            log.error(
                f"[channel_check] Bot is not an admin/member of channel {channel_id}. "
                "Add the bot to the channel as admin to enable membership checks."
            )
            # Fail closed — block until fixed
            is_subscribed = False
        except TelegramBadRequest as e:
            log.warning(f"[channel_check] BadRequest for channel {channel_id}: {e}")
            is_subscribed = False
        except Exception as e:
            log.warning(
                f"[channel_check] Unexpected error checking channel {channel_id}: {e}"
            )
            is_subscribed = False

        if is_subscribed:
            return await handler(event, data)

        # Block — show subscribe prompt
        await _send_subscribe_prompt(event, channel_name, channel_link)
        return
