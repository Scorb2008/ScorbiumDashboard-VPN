import asyncio
from datetime import datetime, timezone
from typing import Dict, Optional

from app.core.config import config
from app.services.telegram_notify import TelegramNotifyService
from app.utils.log import log


class ServiceAlertManager:
    """Send Telegram alerts when system metrics exceed thresholds."""

    def __init__(self):
        self._notify = TelegramNotifyService()
        self._last_alerts: Dict[str, float] = {}
        self._cooldown = 1800 

    async def check_metrics_and_alert(self, metrics: dict) -> None:
        """Check metrics and send alerts if thresholds exceeded."""
        now = datetime.now(timezone.utc).timestamp()

        if metrics['cpu'] > 90:
            await self._send_alert(
                "CPU", f"🔥 CPU overload: {metrics['cpu']}%", now
            )

        if metrics['ram']['percent'] > 90:
            await self._send_alert(
                "RAM",
                f"💾 RAM critical: {metrics['ram']['percent']}% ({metrics['ram']['used']} GB)",
                now
            )

        if metrics['disk']['percent'] > 90:
            await self._send_alert(
                "Disk",
                f"💿 Disk critical: {metrics['disk']['percent']}% ({metrics['disk']['used']} GB)",
                now
            )

    async def check_service_health(self, service_name: str, is_healthy: bool) -> None:
        """Alert on service status change."""
        now = datetime.now(timezone.utc).timestamp()

        if not is_healthy:
            await self._send_alert(
                f"Service_{service_name}",
                f"🔴 Service DOWN: {service_name}",
                now
            )
        else:
            await self._send_alert(
                f"Service_{service_name}_up",
                f"✅ Service UP: {service_name}",
                now,
                cooldown=300 
            )

    async def _send_alert(self, key: str, message: str, timestamp: float, cooldown: Optional[float] = None) -> None:
        """Send alert with cooldown."""
        cooldown = cooldown or self._cooldown

        last = self._last_alerts.get(key, 0)
        if timestamp - last < cooldown:
            return

        self._last_alerts[key] = timestamp

        text = f"{message}\n\n"
        text += f"🕐 {datetime.now(timezone.utc).strftime('%H:%M UTC')}"

        for admin_id in config.telegram.telegram_admin_ids:
            try:
                await self._notify.send_message(admin_id, text)
            except Exception as e:
                log.warning(f"Alert send failed to {admin_id}: {e}")

alert_manager = ServiceAlertManager()
