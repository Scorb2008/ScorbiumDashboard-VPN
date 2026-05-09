"""Telegram bot settings and payment system configuration routes."""
import json as _json
import re
from typing import Optional

from fastapi import APIRouter, Depends, Form, File, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.core.config import config
from app.models.admin import Admin, AdminRole
from app.services.bot_settings import BotSettingsService
from app.services.telegram_notify import TelegramNotifyService

from .shared import (
    _require_permission, _toast, _base_ctx, _time, templates, _ALL_BUTTONS, _DEFAULT_LAYOUT,
)

router = APIRouter()

import io
import qrcode
import base64


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def telegram_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    ctx = await _base_ctx(request, db, "telegram")
    ctx["bot_info"] = await TelegramNotifyService().get_bot_info()
    ctx["admin_ids"] = config.telegram.telegram_admin_ids
    ctx["bot_settings"] = await BotSettingsService(db).get_all()

    ctx["all_buttons"] = _ALL_BUTTONS
    ctx["default_layout"] = _DEFAULT_LAYOUT
    raw = await BotSettingsService(db).get("keyboard_layout")
    try:
        ctx["layout"] = _json.loads(raw) if raw else _DEFAULT_LAYOUT
    except Exception:
        ctx["layout"] = _DEFAULT_LAYOUT

    svc = BotSettingsService(db)
    yk_shop = await svc.get("yookassa_shop_id_override") or ""
    yk_key_set = bool(await svc.get("yookassa_secret_key_override"))
    cb_token_set = bool((await svc.get("cryptobot_token") or "").strip())

    ctx["ps_yookassa_configured"] = bool(yk_shop and yk_key_set)
    ctx["ps_cryptobot_configured"] = cb_token_set
    ctx["ps_stars_enabled"] = (await svc.get("ps_stars_enabled") or "0") == "1"
    ctx["ps_freekassa_configured"] = bool(
        (await svc.get("freekassa_shop_id") or "").strip()
        and (await svc.get("freekassa_api_key") or "").strip()
    )
    ctx["ps_aikassa_configured"] = bool((await svc.get("aikassa_shop_id") or "").strip())
    ctx["ps_platega_configured"] = bool((await svc.get("platega_merchant_id") or "").strip())
    ctx["ps_paypalych_configured"] = bool((await svc.get("paypalych_api_token") or "").strip())

    return templates.TemplateResponse("telegram.html", ctx)


@router.post("/payment-systems/yookassa")
async def ps_save_yookassa(request: Request, db: AsyncSession = Depends(get_db)):
    """Save YooKassa settings to bot_settings. All data via ORM — SQL injection impossible."""
    _require_permission(request, "system")
    form = await request.form()
    shop_id_raw = str(form.get("yookassa_shop_id", "")).strip()
    secret_key_raw = str(form.get("yookassa_secret_key", "")).strip()

    svc = BotSettingsService(db)

    if shop_id_raw:
        if not re.fullmatch(r"\d{5,8}", shop_id_raw):
            return JSONResponse(
                {"ok": False, "message": "Shop ID: 5-8 цифр"}, status_code=400
            )
        await svc.set("yookassa_shop_id_override", shop_id_raw)

    if secret_key_raw:
        if len(secret_key_raw) < 10:
            return JSONResponse(
                {"ok": False, "message": "Secret Key слишком короткий (мин. 10 символов)"},
                status_code=400,
            )
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", secret_key_raw):
            return JSONResponse(
                {"ok": False, "message": "Secret Key содержит недопустимые символы"},
                status_code=400,
            )
        await svc.set("yookassa_secret_key_override", secret_key_raw)

    await db.commit()

    saved_shop = await svc.get("yookassa_shop_id_override") or ""
    saved_key = bool(await svc.get("yookassa_secret_key_override"))
    enabled = bool(saved_shop and saved_key)

    return JSONResponse({"ok": True, "message": "ЮКасса сохранена", "enabled": enabled})


@router.post("/payment-systems/yookassa/test")
async def ps_test_yookassa(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    svc = BotSettingsService(db)
    shop_id_str = await svc.get("yookassa_shop_id_override") or ""
    secret_key = await svc.get("yookassa_secret_key_override") or ""

    if not shop_id_str or not secret_key:
        if (
            config.yookassa
            and config.yookassa.yookassa_shop_id
            and config.yookassa.yookassa_secret_key
        ):
            shop_id_str = str(config.yookassa.yookassa_shop_id)
            secret_key = config.yookassa.yookassa_secret_key.get_secret_value()

    if not shop_id_str or not secret_key:
        return JSONResponse(
            {"ok": False, "message": "ЮКасса не настроена"}, status_code=400
        )

    try:
        import yookassa as _yk
        _yk.Configuration.account_id = int(shop_id_str)
        _yk.Configuration.secret_key = secret_key
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.yookassa.ru/v3/payments",
                params={"limit": 1},
                auth=(shop_id_str, secret_key),
            )
        if resp.status_code in (200, 401):
            if resp.status_code == 401:
                return JSONResponse(
                    {"ok": False, "message": "Неверные учётные данные ЮКассы"},
                    status_code=400,
                )
            return JSONResponse(
                {
                    "ok": True,
                    "message": f"✅ ЮКасса подключена (shop_id: {shop_id_str})",
                }
            )
        return JSONResponse(
            {"ok": False, "message": f"Ошибка API: {resp.status_code}"}, status_code=400
        )
    except Exception as e:
        from app.utils.log import log
        log.error("YooKassa test error: %s", e)
        return JSONResponse(
            {"ok": False, "message": "Ошибка подключения к ЮКассе"}, status_code=400
        )


@router.post("/payment-systems/cryptobot")
async def ps_save_cryptobot(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    form = await request.form()
    token_raw = str(form.get("cryptobot_token", "")).strip()

    if not token_raw:
        return JSONResponse(
            {"ok": False, "message": "Токен не указан"}, status_code=400
        )

    if not re.fullmatch(r"\d+:[A-Za-z0-9_\-]+", token_raw):
        return JSONResponse(
            {
                "ok": False,
                "message": "Неверный формат токена (ожидается: 12345:AAA...)",
            },
            status_code=400,
        )

    svc = BotSettingsService(db)
    await svc.set("cryptobot_token", token_raw)
    await db.commit()

    return JSONResponse(
        {"ok": True, "message": "CryptoBot токен сохранён", "enabled": True}
    )


@router.post("/payment-systems/cryptobot/test")
async def ps_test_cryptobot(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    svc = BotSettingsService(db)
    token = (await svc.get("cryptobot_token") or "").strip()

    if not token:
        return JSONResponse(
            {"ok": False, "message": "CryptoBot не настроен"}, status_code=400
        )

    try:
        from app.services.cryptobot import CryptoBotService
        crypto = CryptoBotService(token)
        info = await crypto.get_me()
        if info:
            name = info.get("name", "")
            app_id = info.get("app_id", "")
            return JSONResponse(
                {
                    "ok": True,
                    "message": f"✅ CryptoBot подключён: {name} (ID: {app_id})",
                }
            )
        return JSONResponse(
            {"ok": False, "message": "Не удалось получить данные от CryptoBot"},
            status_code=400,
        )
    except Exception as e:
        from app.utils.log import log
        log.error("CryptoBot test error: %s", e)
        return JSONResponse(
            {"ok": False, "message": "Ошибка подключения к CryptoBot"}, status_code=400
        )


@router.post("/payment-systems/toggle")
async def ps_toggle(request: Request, db: AsyncSession = Depends(get_db)):
    """Enables/disables a payment system. Stores flag in bot_settings."""
    _require_permission(request, "system")
    _ALLOWED_TOGGLE_KEYS = frozenset(
        [
            "ps_yookassa_enabled",
            "ps_cryptobot_enabled",
            "ps_freekassa_enabled",
            "ps_aikassa_enabled",
            "ps_stars_enabled",
            "ps_platega_enabled",
            "ps_paypalych_enabled",
            "ps_sbp_enabled",
        ]
    )

    form = await request.form()
    key = form.get("key", "")
    value = form.get("value", "0")

    if key not in _ALLOWED_TOGGLE_KEYS:
        return JSONResponse({"error": "Invalid key"}, status_code=400)

    await BotSettingsService(db).set(key, value)
    await db.commit()

    # Clear health service cooldowns on settings change
    from app.services.health import health_service
    health_service._alert_cooldowns.clear()

    return JSONResponse({"ok": True})


@router.post("/payment-systems/freekassa")
async def ps_save_freekassa(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    form = await request.form()
    shop_id = str(form.get("freekassa_shop_id", "")).strip()
    api_key = str(form.get("freekassa_api_key", "")).strip()
    word1 = str(form.get("freekassa_secret_word_1", "")).strip()
    word2 = str(form.get("freekassa_secret_word_2", "")).strip()

    svc = BotSettingsService(db)
    if shop_id:
        await svc.set("freekassa_shop_id", shop_id)
    if api_key:
        await svc.set("freekassa_api_key", api_key)
    if word1:
        await svc.set("freekassa_secret_word_1", word1)
    if word2:
        await svc.set("freekassa_secret_word_2", word2)
    await db.commit()
    return JSONResponse({"ok": True, "message": "FreeKassa сохранена"})


@router.post("/payment-systems/freekassa/test")
async def ps_test_freekassa(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    svc = BotSettingsService(db)
    shop_id = (await svc.get("freekassa_shop_id") or "").strip()
    api_key = (await svc.get("freekassa_api_key") or "").strip()

    if not shop_id or not api_key:
        return JSONResponse(
            {"ok": False, "message": "FreeKassa не настроена"}, status_code=400
        )

    try:
        from app.services.freekassa import FreeKassaService
        fk = FreeKassaService(shop_id, api_key)
        result = await fk.test_connection()
        if result.get("ok"):
            return JSONResponse({"ok": True, "message": "✅ FreeKassa подключена"})
        return JSONResponse(
            {"ok": False, "message": result.get("error", "Ошибка")}, status_code=400
        )
    except Exception as e:
        return JSONResponse(
            {"ok": False, "message": f"Ошибка подключения: {str(e)}"}, status_code=400
        )


@router.post("/payment-systems/aikassa")
async def ps_save_aikassa(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    form = await request.form()
    shop_id = str(form.get("aikassa_shop_id", "")).strip()
    token = str(form.get("aikassa_token", "")).strip()

    svc = BotSettingsService(db)
    if shop_id:
        await svc.set("aikassa_shop_id", shop_id)
    if token:
        await svc.set("aikassa_token", token)
    await db.commit()
    return JSONResponse({"ok": True, "message": "AiKassa сохранена"})


@router.post("/payment-systems/aikassa/test")
async def ps_test_aikassa(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    svc = BotSettingsService(db)
    shop_id = (await svc.get("aikassa_shop_id") or "").strip()
    token = (await svc.get("aikassa_token") or "").strip()

    if not shop_id or not token:
        return JSONResponse(
            {"ok": False, "message": "AiKassa не настроена"}, status_code=400
        )

    try:
        from app.services.aikassa import AiKassaService
        ak = AiKassaService(shop_id, token)
        info = await ak.get_shop_info()
        if info:
            return JSONResponse({"ok": True, "message": "✅ AiKassa подключена"})
        return JSONResponse(
            {"ok": False, "message": "Ошибка проверки"}, status_code=400
        )
    except Exception as e:
        return JSONResponse(
            {"ok": False, "message": f"Ошибка подключения: {str(e)}"}, status_code=400
        )


@router.post("/payment-systems/paypalych")
async def ps_save_paypalych(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    form = await request.form()
    token = str(form.get("paypalych_api_token", "")).strip()

    svc = BotSettingsService(db)
    if token:
        await svc.set("paypalych_api_token", token)
    await db.commit()
    return JSONResponse({"ok": True, "message": "PayPalych сохранён"})


@router.post("/payment-systems/paypalych/test")
async def ps_test_paypalych(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    svc = BotSettingsService(db)
    token = (await svc.get("paypalych_api_token") or "").strip()

    if not token:
        return JSONResponse(
            {"ok": False, "message": "PayPalych не настроен"}, status_code=400
        )

    try:
        from app.services.paypalych import PayPalychService
        pp = PayPalychService(token)
        result = await pp.test_connection()
        if result.get("ok"):
            return JSONResponse({"ok": True, "message": "✅ PayPalych подключён"})
        return JSONResponse(
            {"ok": False, "message": result.get("error", "Ошибка")}, status_code=400
        )
    except Exception as e:
        return JSONResponse(
            {"ok": False, "message": f"Ошибка подключения: {str(e)}"}, status_code=400
        )


@router.post("/payment-systems/platega")
async def ps_save_platega(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    form = await request.form()
    merchant_id = str(form.get("platega_merchant_id", "")).strip()
    secret = str(form.get("platega_secret", "")).strip()

    svc = BotSettingsService(db)
    if merchant_id:
        await svc.set("platega_merchant_id", merchant_id)
    if secret:
        await svc.set("platega_secret", secret)
    await db.commit()
    return JSONResponse({"ok": True, "message": "Platega сохранена"})


@router.post("/payment-systems/platega/test")
async def ps_test_platega(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    svc = BotSettingsService(db)
    merchant_id = (await svc.get("platega_merchant_id") or "").strip()
    secret = (await svc.get("platega_secret") or "").strip()

    if not merchant_id or not secret:
        return JSONResponse(
            {"ok": False, "message": "Platega не настроена"}, status_code=400
        )

    try:
        from app.services.platega import PlategaService
        pl = PlategaService(merchant_id, secret)
        result = await pl.test_connection()
        if result.get("ok"):
            return JSONResponse({"ok": True, "message": "✅ Platega подключена"})
        return JSONResponse(
            {"ok": False, "message": result.get("error", "Ошибка")}, status_code=400
        )
    except Exception as e:
        return JSONResponse(
            {"ok": False, "message": f"Ошибка подключения: {str(e)}"}, status_code=400
        )


@router.post("/payment-systems/stars-rate")
async def ps_save_stars_rate(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    form = await request.form()
    rate = str(form.get("stars_rate", "1.5")).strip()
    try:
        rate_val = float(rate)
        if rate_val <= 0:
            raise ValueError
    except ValueError:
        return JSONResponse(
            {"ok": False, "message": "Неверный курс"}, status_code=400
        )
    await BotSettingsService(db).set("stars_rate", rate)
    await db.commit()
    return JSONResponse({"ok": True, "message": "Курс Stars сохранён"})


@router.post("/telegram/test-marzban", response_class=HTMLResponse)
async def test_marzban(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    try:
        from app.services.pasarguard.pasarguard import get_vpn_panel
        ok = await get_vpn_panel().validate_connection()
        if ok:
            _toast(Response(), "✅ Подключение к Marzban/Pasarguard успешно")
        else:
            _toast(Response(), "❌ Не удалось подключиться к Marzban", "error")
    except Exception as e:
        _toast(Response(), f"❌ Ошибка: {str(e)[:100]}", "error")
    return HTMLResponse("")


@router.get("/telegram/groups", response_class=HTMLResponse)
async def telegram_groups_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    ctx = await _base_ctx(request, db, "telegram")
    try:
        from app.services.pasarguard.pasarguard import get_vpn_panel
        groups = await get_vpn_panel().get_groups()
        ctx["groups"] = groups
    except Exception:
        ctx["groups"] = []
    return templates.TemplateResponse("telegram_groups.html", ctx)


@router.post("/telegram/groups", response_class=HTMLResponse)
async def save_telegram_groups(
    request: Request,
    group_ids: str = Form(""),
    group_name: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "system")
    svc = BotSettingsService(db)
    await svc.set("vpn_group_ids", group_ids.strip())
    await svc.set("required_channel_name", group_name.strip())
    await db.commit()
    resp = Response(status_code=200)
    _toast(resp, "Настройки групп сохранены")
    return resp


@router.post("/telegram/photo/upload")
async def upload_photo(
    request: Request,
    photo_type: str = Form(...),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "system")
    allowed = {"photo_welcome", "photo_buy", "photo_my_keys", "photo_balance",
               "photo_about", "photo_support", "photo_profile", "photo_language"}
    if photo_type not in allowed:
        return JSONResponse({"ok": False, "message": "Invalid photo type"}, status_code=400)

    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        return JSONResponse({"ok": False, "message": "File too large (max 5MB)"}, status_code=400)

    import base64
    b64 = base64.b64encode(content).decode()
    await BotSettingsService(db).set(f"photo_{photo_type}", b64)
    await db.commit()
    return JSONResponse({"ok": True, "message": "Photo uploaded"})


@router.post("/telegram/miniapp", response_class=HTMLResponse)
async def save_miniapp_settings(
    request: Request,
    miniapp_url: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "system")
    await BotSettingsService(db).set("panel_url", miniapp_url.strip())
    await db.commit()
    resp = Response(status_code=200)
    _toast(resp, "Настройки Mini App сохранены")
    return resp


@router.post("/telegram/photo/clear")
async def clear_photo(
    request: Request,
    photo_type: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "system")
    await BotSettingsService(db).set(f"photo_{photo_type}", "")
    await db.commit()
    return JSONResponse({"ok": True})
