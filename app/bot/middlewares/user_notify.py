"""
Middleware: уведомляет пользователя о важных событиях при каждом взаимодействии.
- Просроченные подписки (показывает один раз в 24 часа)
- Неоплаченные pending платежи старше 5 минут
"""
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from app.core.database import AsyncSessionFactory
from app.utils.log import log

# Кэш: user_id -> timestamp последнего уведомления (чтобы не спамить)
_notified_expired: dict[int, float] = {}
_notified_pending: dict[int, float] = {}
_bg_tasks: set[asyncio.Task] = set()
_BG_SEM = asyncio.Semaphore(50)

_EXPIRED_COOLDOWN = 86400   # 24 часа между уведомлениями о просрочке
_PENDING_COOLDOWN = 300     # 5 минут между уведомлениями о pending


class UserNotifyMiddleware(BaseMiddleware):
    """Проверяет состояние подписок и платежей пользователя при каждом апдейте."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user_id: int | None = None
        if isinstance(event, Update):
            if event.message:
                user_id = event.message.from_user.id if event.message.from_user else None
            elif event.callback_query:
                user_id = event.callback_query.from_user.id

        if user_id:
            for coro in (_update_last_seen(user_id), _check_and_notify(user_id)):
                if _BG_SEM.locked():
                    break
                task = asyncio.create_task(_coro_wrapper(coro))
                _bg_tasks.add(task)
                task.add_done_callback(_bg_tasks.discard)

        return await handler(event, data)


async def _coro_wrapper(coro):
    async with _BG_SEM:
        try:
            await coro
        except Exception:
            pass


async def _update_last_seen(user_id: int) -> None:
    from datetime import datetime, timezone
    from sqlalchemy import update
    from app.models.user import User
    try:
        async with AsyncSessionFactory() as session:
            await session.execute(
                update(User)
                .where(User.id == user_id)
                .values(last_seen=datetime.now(timezone.utc))
            )
            await session.commit()
    except Exception as e:
        log.debug(f"[last_seen] error for user {user_id}: {e}")


async def _check_and_notify(user_id: int) -> None:
    try:
        await _notify_expired_keys(user_id)
        await _notify_pending_payments(user_id)
    except Exception as e:
        log.debug(f"[user_notify] error for user {user_id}: {e}")


async def _notify_expired_keys(user_id: int) -> None:
    """Уведомляет о просроченных подписках (раз в 24 часа)."""
    import time
    now = time.time()
    last = _notified_expired.get(user_id, 0)
    if now - last < _EXPIRED_COOLDOWN:
        return

    from sqlalchemy import select
    from app.models.vpn_key import VpnKey, VpnKeyStatus
    from app.services.telegram_notify import TelegramNotifyService
    from app.services.user import UserService
    from app.services.bot_settings import BotSettingsService
    from app.services.i18n import t, get_lang

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(VpnKey).where(
                VpnKey.user_id == user_id,
                VpnKey.status == VpnKeyStatus.EXPIRED.value,
            )
        )
        expired_keys = result.scalars().all()
        if not expired_keys:
            return

        user = await UserService(session).get_by_id(user_id)
        if not user or user.is_banned:
            return
        settings = await BotSettingsService(session).get_all()
        user_lang = user.language if user and user.language else None
        lang = get_lang(settings, user_lang)
        count = len(expired_keys)  # читаем count пока сессия открыта

    _notified_expired[user_id] = now
    msgs = {
        "ru": f"⏰ <b>У вас {count} просроченных подписок</b>\n\nОбновите подписку чтобы восстановить доступ к VPN.",
        "en": f"⏰ <b>You have {count} expired subscription(s)</b>\n\nRenew your subscription to restore VPN access.",
        "fa": f"⏰ <b>شما {count} اشتراک منقضی شده دارید</b>\n\nاشتراک خود را تمدید کنید.",
    }
    await TelegramNotifyService().send_message(user_id, msgs.get(lang, msgs["ru"]))


async def _notify_pending_payments(user_id: int) -> None:
    """Уведомляет о зависших pending платежах (раз в 5 минут)."""
    import time
    now = time.time()
    last = _notified_pending.get(user_id, 0)
    if now - last < _PENDING_COOLDOWN:
        return

    from sqlalchemy import select
    from app.models.payment import Payment, PaymentStatus
    from app.services.telegram_notify import TelegramNotifyService
    from app.services.user import UserService
    from app.services.bot_settings import BotSettingsService
    from app.services.i18n import get_lang

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)

    async with AsyncSessionFactory() as session:
        result = await session.execute(
            select(Payment).where(
                Payment.user_id == user_id,
                Payment.status == PaymentStatus.PENDING.value,
                Payment.created_at <= cutoff,
            )
        )
        pending = result.scalars().all()
        if not pending:
            return

        user = await UserService(session).get_by_id(user_id)
        # Не уведомляем забаненных
        if not user or user.is_banned:
            return
        settings = await BotSettingsService(session).get_all()
        user_lang = user.language if user and user.language else None
        lang = get_lang(settings, user_lang)

    _notified_pending[user_id] = now

    msgs = {
        "ru": "⏳ <b>У вас есть незавершённый платёж</b>\n\nЕсли вы уже оплатили — нажмите «Проверить оплату». Если нет — платёж будет автоматически отменён через 15 минут.",
        "en": "⏳ <b>You have a pending payment</b>\n\nIf you already paid — press 'Check payment'. Otherwise it will be cancelled in 15 minutes.",
        "fa": "⏳ <b>شما یک پرداخت در انتظار دارید</b>\n\nاگر پرداخت کرده‌اید، «بررسی پرداخت» را فشار دهید. در غیر این صورت در 15 دقیقه لغو می‌شود.",
    }
    await TelegramNotifyService().send_message(user_id, msgs.get(lang, msgs["ru"]))
