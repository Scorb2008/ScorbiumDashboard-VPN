from typing import Any, Optional
import time
import asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import config
from app.models.bot_settings import BotSettings
from app.utils.log import log

_SENSITIVE_KEYS = {
    "yookassa_secret_key_override",
    "cryptobot_token",
    "freekassa_api_key",
    "freekassa_secret_word_1",
    "freekassa_secret_word_2",
    "aikassa_token",
    "bot_token",
    "telegram_bot_token",
    "platega_merchant_id",
    "platega_secret",
    "paypalych_api_token",
}

_CACHE_TTL = 300
_cache: Optional[dict] = None
_cache_ts: float = 0
_cache_lock = asyncio.Lock()
DEFAULTS = {
    "welcome_message": "👋 Привет, {name}!\n\nЭто VPN-бот. Выбери действие:",
    "btn_my_keys": "🔑 Мои ключи",
    "btn_buy": "💳 Купить подписку",
    "btn_support": "💬 Поддержка",
    "btn_balance": "💰 Баланс",
    "btn_promo": "🎁 Промокод",
    "support_url": "",
    "referral_bonus_days": "3",
    "referral_bonus_type": "days",
    "referral_bonus_value": "3",
    "payment_success_message": "✅ Оплата прошла успешно!\n\nВаш VPN-ключ готов. Нажмите «Мои ключи».",
    "ban_message": "🚫 Ваш аккаунт заблокирован. Обратитесь в поддержку.",
    "bot_disabled_message": "🔧 Бот временно отключён. Попробуйте позже.",
    "subscription_issued_message": "🔑 Ваш VPN-ключ выдан!\n\nНажмите «Мои ключи» для просмотра.",
    "subscription_cancelled_message": "❌ Ваша подписка была отменена.",
    "referral_welcome_message": "🎉 По вашей реферальной ссылке зарегистрировался новый пользователь!\n\nВам начислен бонус.",
    "unban_message": "✅ Ваш аккаунт разблокирован. Добро пожаловать обратно!",
    "bot_enabled": "1",
    "about_text": "",
    "vpn_group_ids": "",
    "required_channel_id": "",
    "required_channel_name": "",
    # ── Фото для разделов бота ────────────────────────────────────────────────
    "photo_welcome": "",
    "photo_buy": "",
    "photo_my_keys": "",
    "photo_balance": "",
    "photo_about": "",
    "photo_support": "",
    "photo_profile": "",
    "photo_language": "",
    "photo_trial": "",
    "panel_url": "",
    "cabinet_url": "",
    "admin_panel_url": "",
    "keyboard_layout": "",
    "bot_language": "ru",
    "cryptobot_token": "",
    "stars_rate": "1.5",
    # ── Платёжные системы — включение/отключение ──────────────────────────
    "ps_yookassa_enabled": "0",
    "ps_cryptobot_enabled": "0",
    "ps_stars_enabled": "1",
    "ps_freekassa_enabled": "0",
    "ps_aikassa_enabled": "0",
    "ps_platega_enabled": "0",
    "ps_paypalych_enabled": "0",
    "ps_sbp_enabled": "0",
    # ── FreeKassa ─────────────────────────────────────────────────────────────
    "freekassa_shop_id": "",
    "freekassa_api_key": "",
    "freekassa_secret_word_1": "",
    "freekassa_secret_word_2": "",
    # ── AiKassa ───────────────────────────────────────────────────────────────
    "aikassa_shop_id": "",
    "aikassa_token": "",
    # ── Пробный период ────────────────────────────────────────────────────────
    "trial_enabled": "0",
    "trial_days": "3",
    "trial_label": "🎁 Пробный период ({days} дн.)",
    # ── Уведомления об истечении подписки ─────────────────────────────────────
    "notify_expiry_enabled": "1",
    "notify_expiry_days": "7,3,1",
    "notify_expiry_message": "⚠️ <b>Подписка истекает через {days} дн.!</b>\n\n📦 {name}\n📅 Дата истечения: <b>{date}</b>\n\nПродлите подписку чтобы не потерять доступ.",
    # ── Стили inline кнопок ───────────────────────────────────────────────────
    "btn_style_buy": "success",
    "btn_style_my_keys": "primary",
    "btn_style_support": "",
    "btn_style_balance": "",
    "btn_style_promo": "",
    "btn_style_back": "",
    "btn_style_profile": "",
    "btn_style_connect": "",
    "btn_style_about": "",
    "btn_style_servers": "",
    "btn_style_top_referrers": "",
    "btn_style_status": "",
    "btn_style_language": "",
    # ── Maintenance Mode ───────────────────────────────────────────────────────
    "maintenance_mode": "0",
    "maintenance_message": "⛔️ Ведутся технические работы. Напишите через час.",
    # ── Traffic Abuse Analysis ─────────────────────────────────────────
    "traffic_abuse_threshold_gb": "100",
    "traffic_abuse_speed_limit_mbps": "10",
    # ── Custom emoji ID для кнопок (Premium) ─────────────────────────────────
    "btn_emoji_buy": "",
    "btn_emoji_my_keys": "",
    "btn_emoji_support": "",
    "btn_emoji_balance": "",
    "btn_emoji_promo": "",
    "btn_emoji_profile": "",
    "btn_emoji_connect": "",
    "btn_emoji_about": "",
    "btn_emoji_servers": "",
    "btn_emoji_top_referrers": "",
    "btn_emoji_status": "",
    "btn_emoji_language": "",
    # ── Уведомления о мониторинге ─────────────────────────────────────────────
    "notify_monitoring_enabled": "1",
    "notify_svc_database": "1",
    "notify_svc_telegram_bot": "1",
    "notify_svc_vpn_panel": "1",
    "notify_svc_yookassa": "0",
    "notify_svc_cryptobot": "0",
    "notify_cooldown_seconds": "300",
    "notify_on_degraded": "0",
    # ── Telegram Chat ID для уведомлений ──────────────────────────────────────
    "notify_chat_ids": "",
}


class BotSettingsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, key: str) -> Optional[str]:
        all_settings = await self.get_all()
        value = all_settings.get(key)
        if value and key in _SENSITIVE_KEYS:
            from app.services.encryption import decrypt_value, is_encrypted

            if is_encrypted(value):
                return decrypt_value(value)
        return value

    async def get_all(self) -> dict:
        global _cache, _cache_ts, _cache_lock
        now = time.time()
        async with _cache_lock:
            if _cache is not None and (now - _cache_ts) < _CACHE_TTL:
                return _cache

            result = await self.session.execute(select(BotSettings))
            rows = {r.key: r.value for r in result.scalars().all()}

            merged = dict(DEFAULTS)
            merged.update(rows)
            _cache = merged
            _cache_ts = now
            return merged

    async def set(self, key: str, value: str) -> None:
        global _cache, _cache_ts, _cache_lock
        if key in _SENSITIVE_KEYS and value:
            from app.services.encryption import encrypt_value, is_encrypted

            if not is_encrypted(value):
                value = encrypt_value(value)

        result = await self.session.execute(
            select(BotSettings).where(BotSettings.key == key)
        )
        row = result.scalar_one_or_none()
        if row:
            row.value = value
        else:
            self.session.add(BotSettings(key=key, value=value))
        await self.session.flush()
        async with _cache_lock:
            _cache = None
            _cache_ts = 0

    async def set_raw(self, key: str, value: str) -> None:
        """Set a value without encryption (for internal use)."""
        global _cache, _cache_ts, _cache_lock
        result = await self.session.execute(
            select(BotSettings).where(BotSettings.key == key)
        )
        row = result.scalar_one_or_none()
        if row:
            row.value = value
        else:
            self.session.add(BotSettings(key=key, value=value))
        await self.session.flush()
        async with _cache_lock:
            _cache = None
            _cache_ts = 0

    async def is_encrypted_in_db(self, key: str) -> bool:
        """Check if a value is stored encrypted in the DB."""
        result = await self.session.execute(
            select(BotSettings).where(BotSettings.key == key)
        )
        row = result.scalar_one_or_none()
        if not row or not row.value:
            return False
        from app.services.encryption import is_encrypted

        return is_encrypted(row.value)

    async def set_many(self, data: dict) -> None:
        global _cache, _cache_ts, _cache_lock
        for key, value in data.items():
            await self.set(key, value)
        async with _cache_lock:
            _cache = None
            _cache_ts = 0

    async def is_maintenance_mode(self) -> bool:
        value = await self.get("maintenance_mode")
        return value == "1"

    async def set_maintenance_mode(self, enabled: bool) -> None:
        await self.set("maintenance_mode", "1" if enabled else "0")

    async def get_traffic_abuse_threshold(self) -> int:
        value = await self.get("traffic_abuse_threshold_gb")
        return int(value) if value else 500

    async def get_traffic_abuse_speed_limit(self) -> int:
        value = await self.get("traffic_abuse_speed_limit_mbps")
        return int(value) if value else 10


def canonical_site_url(path: str) -> str:
    site_url = (config.web.site_url or "").strip().rstrip("/")
    if not site_url:
        return ""
    normalized_path = path if path.startswith("/") else f"/{path}"
    return f"{site_url}{normalized_path}"


def _deployment_url_paths() -> dict[str, str]:
    panel_root = getattr(config.web, "panel_root", "/panel/")
    return {
        "panel_url": panel_root,
        "admin_panel_url": panel_root,
        "cabinet_url": "/cabinet/",
    }


async def sync_deployment_url_settings(
    session: AsyncSession,
    *,
    overwrite_existing: bool = False,
) -> dict[str, str]:
    """Synchronize deployment-specific URLs with current SITE_URL.

    These URLs are environment-specific and should not keep stale domains
    after restoring a database backup from another server.
    """
    svc = BotSettingsService(session)
    applied: dict[str, str] = {}

    for key, path in _deployment_url_paths().items():
        target = canonical_site_url(path)
        if not target:
            continue

        current = ((await svc.get(key)) or "").strip()
        if not overwrite_existing and current:
            continue
        if current == target:
            continue

        await svc.set(key, target)
        applied[key] = target

    return applied


async def reset_bot_settings_cache() -> None:
    """Clear the process-wide bot settings cache after external DB changes."""
    global _cache, _cache_ts, _cache_lock
    async with _cache_lock:
        _cache = None
        _cache_ts = 0


async def create_traffic_analysis_service() -> Any:
    from app.services.pasarguard.pasarguard import get_vpn_panel

    class TrafficAnalysisService:
        def __init__(self):
            self._panel = get_vpn_panel()

        async def get_all_users_with_traffic(self) -> dict:
            """Get all users with their traffic statistics."""
            all_users = []
            offset = 0
            limit = 200

            try:
                while True:
                    data = await self._panel.get_users(offset=offset, limit=limit)
                    users = data.get("users", [])
                    if not users:
                        break

                    for u in users:
                        username = u.get("username", "")
                        download = u.get("download", 0)
                        upload = u.get("upload", 0)
                        upload_total = upload + download
                        upload_gb = upload_total / (1024**3)
                        all_users.append(
                            {
                                "username": username,
                                "download": download,
                                "upload": upload,
                                "total_gb": upload_gb,
                                "email": u.get("email", ""),
                                "status": u.get("status", ""),
                            }
                        )

                    offset += limit
            except Exception as e:
                log.warning(f"Failed to get users with traffic: {e}")
                return {"users": all_users, "error": str(e)}

            return {"users": all_users}

        async def get_user_traffic(self, username: str) -> Optional[dict]:
            """Get traffic for a specific user."""
            try:
                user = await self._panel.get_user(username)
                if not user:
                    return None
                download = user.get("download", 0)
                upload = user.get("upload", 0)
                return {
                    "username": username,
                    "download": download,
                    "upload": upload,
                    "total_gb": (upload + download) / (1024**3),
                    "status": user.get("status", ""),
                }
            except Exception as e:
                log.warning(f"Failed to get traffic for user {username}: {e}")
                return None

        async def apply_speed_limit(self, username: str, speed_mbps: int) -> bool:
            """Apply speed limit to a user (in Mbps)."""
            try:
                await self._panel.modify_user(
                    username,
                    **{"speed_limit": speed_mbps * 1024 * 1024 // 8},
                )
                return True
            except Exception:
                return False

    return TrafficAnalysisService()
