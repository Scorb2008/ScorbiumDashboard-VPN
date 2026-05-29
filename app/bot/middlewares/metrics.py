"""Bot metrics middleware — tracks handler times, message rates, online users."""

import time
import asyncio
from collections import deque
from contextlib import suppress
from typing import Any, Callable, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, Update

from app.services.metrics import (
    bot_messages_received_total,
    bot_handler_duration_seconds,
    bot_users_online,
    bot_messages_per_second,
)


# Track message timestamps for per-second calculation
_message_timestamps: deque[float] = deque(maxlen=10000)

# Track last-seen users for online count
_last_seen: dict[int, float] = {}

# Track unique users seen in last 5 min
_online_window = 300  # 5 minutes


def get_messages_per_second() -> float:
    """Calculate messages per second over the last 60 seconds."""
    now = time.time()
    cutoff = now - 60
    # Remove old timestamps
    while _message_timestamps and _message_timestamps[0] < cutoff:
        _message_timestamps.popleft()
    count = len(_message_timestamps)
    return count / 60.0


def get_online_users() -> int:
    """Count users active in the last 5 minutes."""
    now = time.time()
    cutoff = now - _online_window
    return sum(1 for t in _last_seen.values() if t > cutoff)


def update_online_metrics():
    """Update gauges with current values."""
    bot_messages_per_second.set(get_messages_per_second())
    bot_users_online.set(get_online_users())


class BotMetricsMiddleware(BaseMiddleware):
    """Middleware that records bot metrics for Prometheus."""

    async def __call__(
        self,
        handler: Callable[[Update, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        # Metrics should never break update processing.
        with suppress(Exception):
            command = self._get_command(event)
            if command:
                bot_messages_received_total.labels(command=command).inc()
                _message_timestamps.append(time.time())

        with suppress(Exception):
            user = self._get_user(event)
            if user:
                _last_seen[user.id] = time.time()

        # Time handler execution
        handler_name = handler.__name__ if hasattr(handler, "__name__") else "unknown"
        start = time.time()
        try:
            result = await handler(event, data)
            return result
        finally:
            with suppress(Exception):
                duration = time.time() - start
                bot_handler_duration_seconds.labels(handler=handler_name).observe(
                    duration
                )

    @staticmethod
    def _get_user(event: Update):
        """Extract user from any update type."""
        for attr in (
            "message",
            "edited_message",
            "business_message",
            "edited_business_message",
            "callback_query",
            "inline_query",
            "chosen_inline_result",
            "my_chat_member",
            "chat_member",
            "pre_checkout_query",
            "shipping_query",
        ):
            obj = getattr(event, attr, None)
            user = getattr(obj, "from_user", None)
            if user:
                return user

        poll_answer = getattr(event, "poll_answer", None)
        if poll_answer and poll_answer.user:
            return poll_answer.user

        business_connection = getattr(event, "business_connection", None)
        if business_connection and getattr(business_connection, "user", None):
            return business_connection.user

        return None

    @staticmethod
    def _get_command(event: Update) -> str | None:
        message = BotMetricsMiddleware._get_message_event(event)
        if message and message.text:
            text = message.text.strip()
            if text.startswith("/"):
                cmd = text.split()[0].split("@")[0]
                return cmd
            return "text"
        if event.callback_query:
            return "callback"
        if event.inline_query:
            return "inline"
        return None

    @staticmethod
    def _get_message_event(event: Update) -> Message | None:
        for attr in (
            "message",
            "edited_message",
            "business_message",
            "edited_business_message",
        ):
            obj = getattr(event, attr, None)
            if isinstance(obj, Message):
                return obj

        callback_query = getattr(event, "callback_query", None)
        if isinstance(callback_query, CallbackQuery) and callback_query.message:
            return callback_query.message

        return None


class BotMetricsLoop:
    """Background loop that periodically updates online metrics."""

    @staticmethod
    async def run():
        while True:
            await asyncio.sleep(30)
            update_online_metrics()
