"""Notification settings & testing routes."""

from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import config
from app.services.bot_settings import BotSettingsService
from app.services.telegram_notify import TelegramNotifyService
from app.api.dependencies import get_db

from .shared import _require_permission, _base_ctx, templates, _NOTIFY_SERVICES

router = APIRouter()


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def notifications_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    ctx = await _base_ctx(request, db, "notifications")
    ctx["settings"] = await BotSettingsService(db).get_all()
    ctx["service_list"] = _NOTIFY_SERVICES
    return templates.TemplateResponse("notifications.html", ctx)


@router.post("/api/setting")
async def update_notification_setting(
    request: Request,
    key: str = Form(...),
    value: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "system")
    allowed_keys = {
        "notify_monitoring_enabled",
        "notify_cooldown_seconds",
        "notify_on_degraded",
        "notify_chat_ids",
        "notify_svc_database",
        "notify_svc_telegram_bot",
        "notify_svc_vpn_panel",
        "notify_svc_yookassa",
        "notify_svc_cryptobot",
        "ps_platega_enabled",
        "ps_paypalych_enabled",
    }
    if key not in allowed_keys:
        return JSONResponse({"error": "Invalid key"}, status_code=400)
    await BotSettingsService(db).set(key, value)
    await db.commit()
    from app.services.health import health_service

    health_service._alert_cooldowns.clear()
    return JSONResponse({"ok": True})


@router.post("/api/test")
async def test_notification(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    notify = TelegramNotifyService()
    admin_ids = config.telegram.telegram_admin_ids
    svc = BotSettingsService(db)
    lang = (await svc.get("bot_language")) or "ru"
    tz_offsets = {"ru": 3, "fa": 3.5, "en": -5}
    tz = timezone(timedelta(hours=tz_offsets.get(lang, 3)))
    msg = (
        "🔔 <b>Тестовое уведомление</b>\n\n"
        "Если вы получили это сообщение — уведомления работают корректно.\n"
        f"Время: {datetime.now(timezone.utc).astimezone(tz).strftime('%H:%M:%S')}"
    )
    sent = 0
    for admin_id in admin_ids:
        try:
            await notify.send_message(admin_id, msg)
            sent += 1
        except Exception:
            pass
    return JSONResponse({"ok": True, "sent_to": sent})
