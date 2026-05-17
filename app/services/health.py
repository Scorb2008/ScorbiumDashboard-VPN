"""Health check service — monitors all external dependencies."""
import asyncio
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.core.config import config
from app.core.database import AsyncSessionFactory
from app.services.telegram_notify import TelegramNotifyService


class ServiceStatus:
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"
    INACTIVE = "inactive"


class HealthEntry:
    def __init__(self, name: str):
        self.name = name
        self.status = ServiceStatus.DOWN
        self.latency_ms = 0
        self.message = ""
        self.checked_at = datetime.now(timezone.utc)

    def ok(self, latency_ms: float = 0, message: str = "OK"):
        self.status = ServiceStatus.HEALTHY
        self.latency_ms = latency_ms
        self.message = message
        self.checked_at = datetime.now(timezone.utc)

    def warn(self, latency_ms: float = 0, message: str = ""):
        self.status = ServiceStatus.DEGRADED
        self.latency_ms = latency_ms
        self.message = message
        self.checked_at = datetime.now(timezone.utc)

    def fail(self, message: str = ""):
        self.status = ServiceStatus.DOWN
        self.latency_ms = 0
        self.message = message
        self.checked_at = datetime.now(timezone.utc)

    def skip(self, message: str = "Отключена"):
        self.status = ServiceStatus.INACTIVE
        self.latency_ms = 0
        self.message = message
        self.checked_at = datetime.now(timezone.utc)


class HealthService:
    """Checks all services and caches results."""

    def __init__(self):
        self._entries: dict[str, HealthEntry] = {}
        self._last_check = 0
        self._cache_ttl = 30  # seconds
        self._alert_cooldowns: dict[str, float] = {}
        self._alert_cooldown = 300  # 5 min between same alerts

    async def check_all(self) -> dict[str, HealthEntry]:
        """Run all health checks concurrently."""
        now = time.time()
        if now - self._last_check < self._cache_ttl and self._entries:
            return self._entries

        checks = [
            ("database", self._check_db),
            ("telegram_bot", self._check_telegram),
            ("vpn_panel", self._check_vpn_panel),
        ]

        # Add individual payment system checks
        payment_checks = [
            ("payment_yookassa", self._check_yookassa),
            ("payment_cryptobot", self._check_cryptobot),
            ("payment_freekassa", self._check_freekassa),
        ]
        checks.extend(payment_checks)

        # Run all checks concurrently with individual timeouts
        async def _safe_check(name, fn):
            entry = HealthEntry(name)
            try:
                await asyncio.wait_for(fn(entry), timeout=10.0)
            except asyncio.TimeoutError:
                entry.fail("Timeout (>10s)")
            except Exception as e:
                entry.fail(str(e))
            return name, entry

        results = await asyncio.gather(
            *[_safe_check(name, fn) for name, fn in checks],
            return_exceptions=False,
        )
        self._entries = dict(results)
        self._last_check = now
        return self._entries

    async def get_entry(self, name: str) -> Optional[HealthEntry]:
        entries = await self.check_all()
        return entries.get(name)

    def is_healthy(self) -> bool:
        critical = {"database", "telegram_bot"}
        for name, entry in self._entries.items():
            if name in critical and entry.status != ServiceStatus.HEALTHY:
                return False
        return True

    async def send_alerts(self):
        """Send Telegram alerts for down services, respecting notification settings."""
        from app.core.database import AsyncSessionFactory
        from app.services.bot_settings import BotSettingsService

        async with AsyncSessionFactory() as session:
            settings = BotSettingsService(session)
            if not (await settings.get("notify_monitoring_enabled")) == "1":
                return

            cooldown_sec = int((await settings.get("notify_cooldown_seconds")) or "300")
            notify_on_degraded = (await settings.get("notify_on_degraded")) == "1"
            chat_ids_raw = await settings.get("notify_chat_ids")
            lang = (await settings.get("bot_language")) or "ru"
            notify_svc = {
                "database": (await settings.get("notify_svc_database")) == "1",
                "telegram_bot": (await settings.get("notify_svc_telegram_bot")) == "1",
                "vpn_panel": (await settings.get("notify_svc_vpn_panel")) == "1",
                "payment_yookassa": (await settings.get("notify_svc_yookassa")) == "1",
                "payment_cryptobot": (await settings.get("notify_svc_cryptobot")) == "1",
                "payment_freekassa": (await settings.get("notify_svc_freekassa")) == "1",
            }

        if chat_ids_raw and chat_ids_raw.strip():
            try:
                target_ids = [int(x.strip()) for x in chat_ids_raw.split(",") if x.strip()]
            except ValueError:
                target_ids = config.telegram.telegram_admin_ids
        else:
            target_ids = config.telegram.telegram_admin_ids

        notify = TelegramNotifyService()
        now = time.time()

        tz_offsets = {"ru": 3, "fa": 3.5, "en": -5}
        tz = timezone(timedelta(hours=tz_offsets.get(lang, 3)))

        for name, entry in self._entries.items():
            if entry.status == ServiceStatus.DOWN or (notify_on_degraded and entry.status == ServiceStatus.DEGRADED):
                if not notify_svc.get(name, True):
                    continue
                cooldown = self._alert_cooldowns.get(name, 0)
                if now - cooldown < cooldown_sec:
                    continue
                self._alert_cooldowns[name] = now

                emoji = "🚨" if entry.status == ServiceStatus.DOWN else "⚠️"
                label = "недоступен" if entry.status == ServiceStatus.DOWN else "работает с перебоями"
                msg = (
                    f"{emoji} <b>Сервис {label}: {name}</b>\n\n"
                    f"Ошибка: {entry.message}\n"
                    f"Время: {entry.checked_at.astimezone(tz).strftime('%H:%M:%S')}"
                )
                for chat_id in target_ids:
                    try:
                        await notify.send_message(chat_id, msg)
                    except Exception:
                        pass

    # ── Individual checks ──

    async def _check_db(self, entry: HealthEntry):
        from sqlalchemy import text
        start = time.time()
        async with AsyncSessionFactory() as session:
            result = await session.execute(text("SELECT 1"))
            val = result.scalar()
        latency = (time.time() - start) * 1000
        if val == 1:
            if latency > 500:
                entry.warn(latency, f"Slow response: {latency:.0f}ms")
            else:
                entry.ok(latency)
        else:
            entry.fail("Unexpected result")

    async def _check_telegram(self, entry: HealthEntry):
        from aiogram import Bot
        from aiogram.enums import ParseMode
        from aiogram.client.default import DefaultBotProperties

        start = time.time()
        bot = Bot(
            token=config.telegram.telegram_bot_token.get_secret_value(),
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        try:
            me = await bot.get_me()
            latency = (time.time() - start) * 1000
            entry.ok(latency, f"Bot: @{me.username}")
        except Exception as e:
            entry.fail(str(e))
        finally:
            await bot.session.close()

    async def _check_vpn_panel(self, entry: HealthEntry):
        """Check Pasarguard/Marzban VPN panel via actual API client."""
        try:
            from app.services.pasarguard.pasarguard import PasarguardService

            start = time.time()
            panel = PasarguardService()
            ok = await panel.validate_connection()
            latency = (time.time() - start) * 1000
            if ok:
                entry.ok(latency, "VPN панель подключена")
            else:
                entry.fail("Не удалось подключиться")
        except Exception as e:
            entry.fail(str(e))

    async def _check_yookassa(self, entry: HealthEntry):
        import httpx
        from app.core.database import AsyncSessionFactory
        from app.services.bot_settings import BotSettingsService

        async with AsyncSessionFactory() as session:
            svc = BotSettingsService(session)
            enabled = (await svc.get("ps_yookassa_enabled") or "0") == "1"
            shop_id = (await svc.get("yookassa_shop_id_override") or "").strip()
            secret = (await svc.get("yookassa_secret_key_override") or "").strip()

        if not enabled:
            entry.skip("Отключена")
            return
        if not shop_id or not secret:
            entry.warn(0, "Включена, но не настроена")
            return
        start = time.time()
        async with httpx.AsyncClient(timeout=5) as client:
            try:
                resp = await client.get(
                    "https://api.yookassa.ru/v3/me",
                    auth=(str(shop_id), secret),
                )
                latency = (time.time() - start) * 1000
                if resp.status_code in (200, 401):
                    entry.ok(latency, "API доступен")
                else:
                    entry.warn(latency, f"Status {resp.status_code}")
            except Exception as e:
                entry.fail(str(e))

    async def _check_cryptobot(self, entry: HealthEntry):
        from app.core.database import AsyncSessionFactory
        from app.services.bot_settings import BotSettingsService

        async with AsyncSessionFactory() as session:
            settings = BotSettingsService(session)
            enabled = (await settings.get("ps_cryptobot_enabled") or "0") == "1"
            token = await settings.get("cryptobot_token")
        if not enabled:
            entry.skip("Отключен")
            return
        if not token:
            entry.warn(0, "Включен, но не настроен")
            return
        import httpx

        start = time.time()
        async with httpx.AsyncClient(timeout=5) as client:
            try:
                resp = await client.post(
                    "https://pay.crypt.bot/api/getMe",
                    headers={"Crypto-Pay-API-Token": token},
                )
                latency = (time.time() - start) * 1000
                if resp.status_code == 200:
                    entry.ok(latency)
                else:
                    entry.warn(latency, f"Status {resp.status_code}")
            except Exception as e:
                entry.fail(str(e))

    async def _check_freekassa(self, entry: HealthEntry):
        from app.core.database import AsyncSessionFactory
        from app.services.bot_settings import BotSettingsService

        async with AsyncSessionFactory() as session:
            settings = BotSettingsService(session)
            enabled = (await settings.get("ps_freekassa_enabled") or "0") == "1"
            shop_id = await settings.get("freekassa_shop_id") or ""
            api_key = await settings.get("freekassa_api_key") or ""
        if not enabled:
            entry.skip("Отключена")
            return
        if not shop_id or not api_key:
            entry.warn(0, "Включена, но не настроена")
            return
        import httpx

        start = time.time()
        async with httpx.AsyncClient(timeout=5) as client:
            try:
                resp = await client.get("https://api.freekassa.com", timeout=5)
                latency = (time.time() - start) * 1000
                if resp.status_code < 500:
                    entry.ok(latency, "API доступен")
                else:
                    entry.warn(latency, f"Status {resp.status_code}")
            except Exception as e:
                entry.fail(str(e))


health_service = HealthService()
