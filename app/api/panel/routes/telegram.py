"""Telegram bot settings and payment system configuration routes."""
import json as _json
import re

from fastapi import APIRouter, Depends, Form, File, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.core.config import config
from app.services.branding_asset import BrandingAssetService
from app.services.bot_settings import BotSettingsService
from app.services.telegram_notify import TelegramNotifyService

from .shared import (
    _require_permission, _toast, _base_ctx, templates, _ALL_BUTTONS, _DEFAULT_LAYOUT,
)

router = APIRouter()


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def telegram_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    ctx = await _base_ctx(request, db, "telegram")
    ctx["bot_info"] = await TelegramNotifyService().get_bot_info()
    ctx["admin_ids"] = config.telegram.telegram_admin_ids

    svc = BotSettingsService(db)
    settings = await svc.get_all()
    settings["custom_logo"] = await BrandingAssetService(db).get_logo_url()
    ctx["bot_settings"] = settings
    ctx["config"] = config

    ctx["all_buttons"] = _ALL_BUTTONS
    ctx["default_layout"] = _DEFAULT_LAYOUT
    raw = await svc.get("keyboard_layout")
    try:
        ctx["layout"] = _json.loads(raw) if raw else _DEFAULT_LAYOUT
    except Exception:
        ctx["layout"] = _DEFAULT_LAYOUT

    yk_shop = (await svc.get("yookassa_shop_id_override") or "").strip()
    yk_secret = (await svc.get("yookassa_secret_key_override") or "").strip()
    cb_token = (await svc.get("cryptobot_token") or "").strip()
    fk_shop = (await svc.get("freekassa_shop_id") or "").strip()
    fk_api_key = (await svc.get("freekassa_api_key") or "").strip()
    ak_shop = (await svc.get("aikassa_shop_id") or "").strip()
    pg_merchant = (await svc.get("platega_merchant_id") or "").strip()
    pg_secret = (await svc.get("platega_secret") or "").strip()
    pp_token = (await svc.get("paypalych_api_token") or "").strip()
    pp_merchant_id = (await svc.get("paypalych_merchant_id") or "").strip()
    pp_merchant_secret = (await svc.get("paypalych_merchant_secret") or "").strip()
    fk_secret1 = (await svc.get("freekassa_secret_word_1") or "").strip()
    fk_secret2 = (await svc.get("freekassa_secret_word_2") or "").strip()

    def _toggle(key):
        return settings.get(key) == "1"

    ctx["ps"] = {
        "platega_enabled": _toggle("ps_platega_enabled"),
        "platega_configured": bool(pg_merchant),
        "platega_toggle": _toggle("ps_platega_enabled"),
        "platega_merchant_id": pg_merchant,
        "platega_secret_set": bool(pg_secret),
        "yookassa_enabled": _toggle("ps_yookassa_enabled"),
        "yookassa_configured": bool(yk_shop and yk_secret),
        "yookassa_toggle": _toggle("ps_yookassa_enabled"),
        "yookassa_shop_id": yk_shop,
        "yookassa_secret_set": bool(yk_secret),
        "freekassa_enabled": _toggle("ps_freekassa_enabled"),
        "freekassa_configured": bool(fk_shop and fk_api_key),
        "freekassa_shop_id": fk_shop,
        "freekassa_secret1_set": bool(fk_secret1),
        "freekassa_secret2_set": bool(fk_secret2),
        "aikassa_enabled": _toggle("ps_aikassa_enabled"),
        "aikassa_configured": bool(ak_shop),
        "aikassa_shop_id": ak_shop,
        "paypalych_enabled": _toggle("ps_paypalych_enabled"),
        "paypalych_configured": bool(pp_token),
        "paypalych_merchant_configured": bool(pp_merchant_id and pp_merchant_secret),
        "paypalych_toggle": _toggle("ps_paypalych_enabled"),
        "paypalych_merchant_id": pp_merchant_id,
        "paypalych_secret_set": bool(pp_merchant_secret),
        "cryptobot_enabled": _toggle("ps_cryptobot_enabled"),
        "cryptobot_configured": bool(cb_token),
        "cryptobot_toggle": _toggle("ps_cryptobot_enabled"),
        "stars_enabled": _toggle("ps_stars_enabled"),
        "stars_configured": True,
        "sbp_enabled": _toggle("ps_sbp_enabled"),
    }

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

    return JSONResponse({"ok": True, "message": "ЮКасса сохранена", "configured": enabled, "enabled": enabled})


@router.post("/payment-systems/yookassa/test")
async def ps_test_yookassa(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    svc = BotSettingsService(db)
    shop_id_str = await svc.get("yookassa_shop_id_override") or ""
    secret_key = await svc.get("yookassa_secret_key_override") or ""

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
        {"ok": True, "message": "CryptoBot токен сохранён", "configured": True, "enabled": True}
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
    return JSONResponse({"ok": True, "message": "FreeKassa сохранена", "configured": bool(shop_id and api_key)})


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
    return JSONResponse({"ok": True, "message": "AiKassa сохранена", "configured": bool(shop_id and token)})


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
    merchant_id = str(form.get("paypalych_merchant_id", "")).strip()
    secret = str(form.get("paypalych_secret", "")).strip()

    svc = BotSettingsService(db)
    if token:
        await svc.set("paypalych_api_token", token)
    if merchant_id:
        await svc.set("paypalych_merchant_id", merchant_id)
    if secret:
        await svc.set("paypalych_merchant_secret", secret)
    await db.commit()

    api_token = (await svc.get("paypalych_api_token") or "").strip()
    mid = (await svc.get("paypalych_merchant_id") or "").strip()
    msec = (await svc.get("paypalych_merchant_secret") or "").strip()
    enabled = (await svc.get("ps_paypalych_enabled")) == "1"
    return JSONResponse({
        "ok": True,
        "message": "PayPalych сохранён",
        "configured": bool(api_token),
        "merchant_configured": bool(mid and msec),
        "enabled": enabled,
    })


@router.post("/payment-systems/paypalych/test")
async def ps_test_paypalych(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    svc = BotSettingsService(db)
    token = (await svc.get("paypalych_api_token") or "").strip()
    merchant_id = (await svc.get("paypalych_merchant_id") or "").strip()
    merchant_secret = (await svc.get("paypalych_merchant_secret") or "").strip()

    messages = []
    any_ok = False

    if token:
        try:
            from app.services.paypalych import PayPalychService
            pp = PayPalychService(token)
            result = await pp.test_connection()
            if result.get("ok"):
                messages.append("✅ API: подключён")
                any_ok = True
            else:
                messages.append(f"❌ API: {result.get('message', 'Ошибка')}")
        except Exception as e:
            messages.append(f"❌ API: {str(e)}")

    if merchant_id and merchant_secret:
        messages.append("✅ Merchant: данные сохранены")
        any_ok = True
    elif merchant_id or merchant_secret:
        messages.append("❌ Merchant: заполните оба поля (ID + Secret)")

    if not messages:
        return JSONResponse({"ok": False, "message": "Ничего не настроено"}, status_code=400)

    return JSONResponse({"ok": any_ok, "message": " | ".join(messages)})


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
    return JSONResponse({"ok": True, "message": "Platega сохранена", "configured": bool(merchant_id and secret)})


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


@router.post("/test-marzban", response_class=HTMLResponse)
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


@router.get("/groups", response_class=HTMLResponse)
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


@router.post("/groups", response_class=HTMLResponse)
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


@router.post("/photo/upload")
async def upload_photo(
    request: Request,
    photo_type: str = Form(...),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "system")
    allowed = {"photo_welcome", "photo_buy", "photo_my_keys", "photo_balance",
               "photo_about", "photo_support", "photo_profile", "photo_language",
               "photo_trial", "photo_connect", "photo_referrals", "photo_status"}
    if photo_type not in allowed:
        return JSONResponse({"ok": False, "message": "Invalid photo type"}, status_code=400)

    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        return JSONResponse({"ok": False, "message": "File too large (max 5MB)"}, status_code=400)

    import base64
    b64 = base64.b64encode(content).decode()
    await BotSettingsService(db).set(photo_type, b64)
    await db.commit()
    return JSONResponse({"ok": True, "message": "Photo uploaded"})


@router.post("/photo/clear")
async def clear_photo(
    request: Request,
    photo_type: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "system")
    allowed = {"photo_welcome", "photo_buy", "photo_my_keys", "photo_balance",
               "photo_about", "photo_support", "photo_profile", "photo_language",
               "photo_trial", "photo_connect", "photo_referrals", "photo_status"}
    if photo_type not in allowed:
        return JSONResponse({"ok": False, "message": "Invalid photo type"}, status_code=400)
    await BotSettingsService(db).set(photo_type, "")
    await db.commit()
    return JSONResponse({"ok": True})


@router.post("/bot-toggle", response_class=HTMLResponse)
async def bot_toggle(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "system")
    svc = BotSettingsService(db)
    current = await svc.get("bot_enabled")
    new_val = "0" if current == "1" else "1"
    await svc.set("bot_enabled", new_val)
    await db.commit()
    return HTMLResponse("")


@router.post("/bot-settings", response_class=HTMLResponse)
async def save_bot_settings(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "system")
    svc = BotSettingsService(db)
    form = await request.form()
    for key, value in form.multi_items():
        if key == "trial_enabled_hidden":
            continue
        await svc.set(key, str(value))
    await db.commit()
    return HTMLResponse("")


@router.post("/send", response_class=HTMLResponse)
async def send_direct_message(
    request: Request,
    chat_id: int = Form(...),
    text: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "system")
    notify = TelegramNotifyService()
    ok = await notify.send_message(chat_id, text)
    if ok:
        _toast(Response(), "Сообщение отправлено")
    else:
        _toast(Response(), "Ошибка отправки", "error")
    return HTMLResponse("")


@router.post("/lang-strings", response_class=HTMLResponse)
async def save_lang_strings(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "system")
    svc = BotSettingsService(db)
    form = await request.form()
    lang = str(form.get("lang", ""))
    for key, value in form.multi_items():
        if key == "lang":
            continue
        await svc.set(f"i18n_{lang}_{key}", str(value))
    await db.commit()
    return HTMLResponse("")
