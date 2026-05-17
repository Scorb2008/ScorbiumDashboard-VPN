"""
Anti-spam / rate-limit middleware for the Telegram bot.
Uses a simple in-memory token bucket: each user gets N tokens,
refilled every REFILL_INTERVAL seconds.
"""
import time
from collections import defaultdict
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject

# ── Config ────────────────────────────────────────────────────────────────────
MAX_TOKENS = 10          # max burst
REFILL_RATE = 5          # tokens per second
REFILL_INTERVAL = 1.0    # check interval
CALLBACK_COST = 1        # tokens per callback
MESSAGE_COST = 2         # tokens per message (heavier)
BLOCK_DURATION = 30      # seconds to block after exhaustion

# ── Admin command flood protection ───────────────────────────────────────────
ADMIN_COMMANDS = {"/ban", "/unban", "/promo", "/addbalance", "/givekey", "/admin"}
ADMIN_RATE_PER_MINUTE = 20  # max admin commands per minute per admin

# ── Per-command limits (messages per minute) ──────────────────────────────────
COMMAND_LIMITS = {
    "/start": 10,
    "/buy": 15,
    "/keys": 20,
    "/profile": 20,
    "/status": 20,
    "/top": 5,
    "/gift": 5,
    "/extend": 5,
    "/autorenew": 10,
    "/id": 20,
    "/help": 10,
}
DEFAULT_COMMAND_LIMIT = 30  # per minute for unknown commands


class _Bucket:
    __slots__ = ("tokens", "last_refill", "blocked_until", "warned")

    def __init__(self) -> None:
        self.tokens: float = MAX_TOKENS
        self.last_refill: float = time.monotonic()
        self.blocked_until: float = 0.0
        self.warned: bool = False  # отправили ли предупреждение в этот блок

    def refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(MAX_TOKENS, self.tokens + elapsed * REFILL_RATE)
        self.last_refill = now

    def consume(self, cost: int) -> bool:
        """Returns True if allowed, False if rate-limited."""
        now = time.monotonic()
        if now < self.blocked_until:
            return False
        # Блок истёк — сбрасываем флаг предупреждения
        if self.warned:
            self.warned = False
        self.refill()
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        # Exhausted — block user
        self.blocked_until = now + BLOCK_DURATION
        return False


class ThrottleMiddleware(BaseMiddleware):
    """
    Drops updates from users who exceed the rate limit.
    Sends a one-time warning message on first block.
    """

    def __init__(self) -> None:
        self._buckets: dict[int, _Bucket] = defaultdict(_Bucket)
        self._admin_counts: dict[int, list[float]] = defaultdict(list)  # user_id -> [timestamps]
        self._command_counts: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))

    def _get_user_id(self, event: TelegramObject) -> int | None:
        if isinstance(event, (Message, CallbackQuery)):
            return event.from_user.id if event.from_user else None
        return None

    def _get_command(self, event: TelegramObject) -> str | None:
        if isinstance(event, Message) and event.text:
            text = event.text.strip()
            if text.startswith("/"):
                parts = text.split()[0].split("@")
                return parts[0].lower()
        return None

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user_id = self._get_user_id(event)
        if user_id is None:
            return await handler(event, data)

        # Per-command rate limit
        cmd = self._get_command(event)
        if cmd and isinstance(event, Message):
            limit = COMMAND_LIMITS.get(cmd, DEFAULT_COMMAND_LIMIT)
            now = time.monotonic()
            window = self._command_counts[user_id][cmd]
            # Remove entries older than 60 seconds
            cutoff = now - 60
            self._command_counts[user_id][cmd] = [t for t in window if t > cutoff]
            if len(self._command_counts[user_id][cmd]) >= limit:
                try:
                    await event.answer(
                        f"⏳ Команда {cmd} используется слишком часто. Попробуйте через минуту.",
                        disable_notification=True,
                    )
                except Exception:
                    pass
                return  # drop

            self._command_counts[user_id][cmd].append(now)

        # Admin command flood protection
        if cmd and cmd in ADMIN_COMMANDS:
            from app.core.config import config
            if user_id in config.telegram.telegram_admin_ids:
                now = time.monotonic()
                admin_window = [t for t in self._admin_counts[user_id] if t > now - 60]
                if len(admin_window) >= ADMIN_RATE_PER_MINUTE:
                    try:
                        await event.answer("⏳ Подождите — слишком много админ-команд подряд.", disable_notification=True)
                    except Exception:
                        pass
                    return
                self._admin_counts[user_id].append(now)

        # General token bucket
        cost = MESSAGE_COST if isinstance(event, Message) else CALLBACK_COST
        bucket = self._buckets[user_id]

        if not bucket.consume(cost):
            # Предупреждаем только один раз за период блока
            if not bucket.warned:
                bucket.warned = True
                if isinstance(event, Message):
                    try:
                        await event.answer(
                            "⏳ Слишком много запросов. Подождите 30 секунд.",
                            disable_notification=True,
                        )
                    except Exception:
                        pass
                elif isinstance(event, CallbackQuery):
                    try:
                        await event.answer("⏳ Слишком быстро! Подождите.", show_alert=True)
                    except Exception:
                        pass
            return  # drop the update

        return await handler(event, data)
