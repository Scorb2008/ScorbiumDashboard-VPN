import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.core.config import config
from app.services.user import UserService
from app.services.plan import PlanService
from app.services.vpn_key import VpnKeyService
from app.services.payment import PaymentService
from app.services.payment_fulfillment import PaymentFulfillmentService
from app.services.promo import PromoService
from app.services.support import SupportService
from app.services.referral import ReferralService
from app.services.bot_settings import BotSettingsService
from app.services.branding_asset import BrandingAssetService
from app.services.yookassa import YookassaService
from app.models.payment import PaymentProvider, PaymentStatus
from app.models.promo import PromoType
from app.utils.log import log

from .auth import (
    _is_secure_request,
    get_cabinet_user,
    get_telegram_init_data,
    is_telegram_miniapp_request,
    set_session_cookie,
    try_miniapp_auth,
)

router = APIRouter()

_tpl_path = Path(__file__).resolve().parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(_tpl_path))
templates.env.globals["is_admin_user"] = lambda u: bool(u and u.id in config.telegram.telegram_admin_ids)
_MONEY_STEP = Decimal("0.01")


async def _require_user(request: Request, db: AsyncSession):
    user = await get_cabinet_user(request, db)
    if user:
        return user
    user = await try_miniapp_auth(request, db)
    if user:
        return user
    return None


async def _require_active_user(request: Request, db: AsyncSession):
    user = await _require_user(request, db)
    if user and not user.is_banned:
        return user
    return None


def _is_mini_app(request: Request) -> bool:
    return is_telegram_miniapp_request(request)


def _cabinet_redirect_url(request: Request, path: str = "/cabinet/") -> str:
    if not _is_mini_app(request):
        return path
    params = {"miniapp": "1"}
    init_data = get_telegram_init_data(request)
    if init_data:
        params["tg_init_data"] = init_data
    query = urlencode(params)
    return f"{path}?{query}" if query else path


def _persist_cabinet_session(request: Request, response, user) -> None:
    if user and not request.cookies.get("cabinet_session"):
        set_session_cookie(response, user.id, secure=_is_secure_request(request))


def _normalize_money(value) -> Decimal:
    return Decimal(str(value)).quantize(_MONEY_STEP, rounding=ROUND_HALF_UP)


def _request_origin(request: Request) -> str:
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    scheme = (
        forwarded_proto.split(",")[0].strip().lower()
        if forwarded_proto
        else request.url.scheme
    )
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{scheme}://{host}"


def _absolute_cabinet_url(request: Request, path: str, **params) -> str:
    query = urlencode({
        key: str(value)
        for key, value in params.items()
        if value not in (None, "")
    })
    return f"{_request_origin(request)}{path}" + (f"?{query}" if query else "")


def _payment_meta_dict(payment) -> dict:
    if not payment or not payment.meta:
        return {}
    try:
        data = json.loads(payment.meta)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


async def _cabinet_branding_context(db: AsyncSession) -> dict:
    return {
        "custom_logo": await BrandingAssetService(db).get_logo_url(),
    }


async def _resolve_discount_promo(
    db: AsyncSession,
    *,
    user_id: int,
    plan_id: int,
    promo_code: str,
):
    code = promo_code.strip().upper()
    if not code:
        return None, None
    validation = await PromoService(db).validate_for_user(
        code,
        user_id=user_id,
        promo_type=PromoType.DISCOUNT.value,
        plan_id=plan_id,
    )
    if not validation.promo:
        return None, validation.message or "Промокод недействителен"
    return validation.promo, None


def _apply_discount(amount, promo) -> tuple[Decimal, Decimal]:
    original_amount = _normalize_money(amount)
    if not promo:
        return original_amount, Decimal("0.00")

    discount_percent = Decimal(str(promo.value))
    if discount_percent < 0:
        discount_percent = Decimal("0")
    if discount_percent > 100:
        discount_percent = Decimal("100")

    discount_amount = (
        original_amount * discount_percent / Decimal("100")
    ).quantize(_MONEY_STEP, rounding=ROUND_HALF_UP)
    final_amount = (original_amount - discount_amount).quantize(
        _MONEY_STEP, rounding=ROUND_HALF_UP
    )
    if final_amount < 0:
        final_amount = Decimal("0.00")
    return final_amount, discount_amount


async def _get_days_promo_target_key(db: AsyncSession, user_id: int):
    key_svc = VpnKeyService(db)
    active_keys = await key_svc.get_active_for_user(user_id)
    if active_keys:
        return active_keys[0]
    all_keys = await key_svc.get_all_for_user(user_id)
    return all_keys[0] if all_keys else None


async def _finalize_subscription_payment(db: AsyncSession, payment, external_id: str):
    meta = _payment_meta_dict(payment)
    plan_id = int(meta.get("plan_id", 0) or 0)
    extend_key_id = int(meta.get("extend_key_id", 0) or 0) or None
    if not plan_id:
        return payment, None, "Не удалось определить тариф для платежа"

    plan = await PlanService(db).get_by_id(plan_id)
    if not plan:
        return payment, None, "Тариф не найден"

    confirmation = await PaymentService(db).confirm_once(payment.id, external_id)
    payment = confirmation.payment
    if not payment:
        return None, None, "Платёж не найден"

    fulfillment = PaymentFulfillmentService(db)
    if extend_key_id:
        delivery = await fulfillment.extend_subscription_once(
            payment.id, payment.user_id, extend_key_id, plan
        )
    else:
        delivery = await fulfillment.provision_subscription_once(
            payment.id, payment.user_id, plan
        )
    return payment, delivery.key, None


async def _create_cabinet_yookassa_payment(
    request: Request,
    db: AsyncSession,
    *,
    user,
    plan,
    promo_code: str,
    provider: PaymentProvider,
    payment_method: str | None,
    success_message: str,
):
    promo = None
    if promo_code.strip():
        promo, promo_error = await _resolve_discount_promo(
            db,
            user_id=user.id,
            plan_id=plan.id,
            promo_code=promo_code,
        )
        if promo_error:
            return JSONResponse({"ok": False, "message": promo_error}, status_code=400)

    charged_amount, discount_amount = _apply_discount(plan.price, promo)

    try:
        payment_provider = (
            PaymentProvider.BALANCE if charged_amount == Decimal("0.00") else provider
        )
        payment_meta = {
            "plan_id": plan.id,
            "kind": "cabinet_plan_purchase",
            "original_amount": str(_normalize_money(plan.price)),
            "final_amount": str(charged_amount),
        }
        if promo:
            payment_meta["promo_code"] = promo.code
            payment_meta["discount_value"] = str(promo.value)
            payment_meta["discount_amount"] = str(discount_amount)

        payment = await PaymentService(db).create_pending(
            user.id,
            plan,
            payment_provider,
            amount=charged_amount,
            meta=json.dumps(payment_meta),
        )

        if promo:
            consumed = await PromoService(db).consume(promo, user.id, plan_id=plan.id)
            if not consumed:
                await db.rollback()
                return JSONResponse({"ok": False, "message": "Промокод уже использован"}, status_code=400)

        if charged_amount == Decimal("0.00"):
            confirmed = await PaymentService(db).confirm_once(payment.id, f"promo_free_{payment.id}")
            if not confirmed.payment:
                await db.rollback()
                return JSONResponse({"ok": False, "message": "Ошибка при оформлении"}, status_code=500)
            _, key, payment_error = await _finalize_subscription_payment(
                db, payment, f"promo_free_{payment.id}"
            )
            if payment_error or not key:
                await db.rollback()
                return JSONResponse({"ok": False, "message": "Ошибка при создании ключа"}, status_code=500)
            await db.commit()
            return JSONResponse({
                "ok": True,
                "status": "succeeded",
                "message": "Промокод полностью покрыл стоимость тарифа",
                "access_url": key.access_url,
                "redirect": "/cabinet/keys",
            })

        yk = await YookassaService.create()
        return_url = _absolute_cabinet_url(request, "/cabinet/plans", payment_id=payment.id)
        if payment_method == "sbp":
            yk_payment = await yk.create_sbp_payment(
                amount=charged_amount,
                description=f"VPN подписка — {plan.name}",
                return_url=return_url,
                metadata={"payment_id": str(payment.id), "plan_id": str(plan.id)},
            )
        else:
            yk_payment = await yk.create_payment(
                amount=charged_amount,
                description=f"VPN подписка — {plan.name}",
                return_url=return_url,
                metadata={"payment_id": str(payment.id), "plan_id": str(plan.id)},
            )
        payment.external_id = yk_payment.id
        await db.commit()

        return JSONResponse({
            "ok": True,
            "status": "pending",
            "message": success_message,
            "payment_id": payment.id,
            "payment_url": yk_payment.confirmation.confirmation_url,
            "return_url": return_url,
        })
    except Exception as e:
        log.error("Cabinet Yookassa payment error (%s): %s", provider.value, e)
        await db.rollback()
        return JSONResponse({"ok": False, "message": "Не удалось создать платёж"}, status_code=502)


async def _ensure_bot_username(db: AsyncSession, settings: dict) -> str:
    bu = settings.get("bot_username", "")
    if bu:
        return bu
    env_bu = (config.telegram.telegram_bot_username or "").strip()
    if env_bu:
        try:
            await BotSettingsService(db).set("bot_username", env_bu)
            await db.commit()
        except Exception as e:
            log.warning("Failed to persist TELEGRAM_BOT_USERNAME: {}", e)
        return env_bu
    log.info("bot_username not in DB — fetching from Telegram API...")
    try:
        import httpx
        token = config.telegram.telegram_bot_token.get_secret_value()
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://api.telegram.org/bot{token}/getMe")
            if r.status_code == 200:
                bu = (r.json().get("result", {}) or {}).get("username", "")
                if bu:
                    await BotSettingsService(db).set("bot_username", bu)
                    await db.commit()
                    log.info("bot_username fetched from API: @{}", bu)
                else:
                    log.warning("Telegram API getMe returned empty username")
            else:
                log.warning("Telegram API getMe failed: HTTP {}", r.status_code)
        return bu
    except Exception as e:
        log.warning("Failed to fetch bot_username from Telegram API: {}", e)
        return ""


@router.get("/cabinet", response_class=HTMLResponse)
@router.get("/cabinet/", response_class=HTMLResponse)
async def cabinet_index(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _require_user(request, db)
    if user:
        if user.is_banned:
            return templates.TemplateResponse("cabinet/login.html", {
                "request": request, "app_name": config.web.app_name, "settings": {},
                "error": "Аккаунт заблокирован", "is_mini_app": _is_mini_app(request),
                "telegram_client_id": config.telegram.telegram_client_id,
                **(await _cabinet_branding_context(db)),
            })
        svc = await BotSettingsService(db).get_all()
        plans = await PlanService(db).get_all(only_active=True)
        key_service = VpnKeyService(db)
        keys = await key_service.get_active_for_user(user.id)
        await key_service.refresh_traffic_for_keys(keys)
        response = templates.TemplateResponse("cabinet/index.html", {
            "request": request, "app_name": config.web.app_name,
            "app_version": config.web.app_version,
            "user": user, "plans": plans, "keys": keys,
            "settings": svc, "now": datetime.now(timezone.utc),
            "is_mini_app": _is_mini_app(request),
            **(await _cabinet_branding_context(db)),
        })
        _persist_cabinet_session(request, response, user)
        return response

    svc = await BotSettingsService(db).get_all()
    bot_username = await _ensure_bot_username(db, svc)
    if bot_username:
        svc["bot_username"] = bot_username
    return templates.TemplateResponse("cabinet/login.html", {
        "request": request, "app_name": config.web.app_name, "settings": svc,
        "error": None, "is_mini_app": _is_mini_app(request),
        "telegram_client_id": config.telegram.telegram_client_id,
        **(await _cabinet_branding_context(db)),
    })


@router.get("/cabinet/profile", response_class=HTMLResponse)
async def cabinet_profile(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _require_active_user(request, db)
    if not user:
        return RedirectResponse(url=_cabinet_redirect_url(request), status_code=302)
    key_service = VpnKeyService(db)
    keys = await key_service.get_all_for_user(user.id)
    await key_service.refresh_traffic_for_keys(keys)
    payments = await PaymentService(db).get_all(user_id=user.id, limit=50)
    referrals = await ReferralService(db).count_referrals(user.id)
    response = templates.TemplateResponse("cabinet/profile.html", {
        "request": request, "app_name": config.web.app_name,
        "user": user, "keys": keys, "payments": payments,
        "referrals_count": referrals, "now": datetime.now(timezone.utc),
        "is_mini_app": _is_mini_app(request),
        **(await _cabinet_branding_context(db)),
    })
    _persist_cabinet_session(request, response, user)
    return response


@router.get("/cabinet/keys", response_class=HTMLResponse)
async def cabinet_keys(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _require_active_user(request, db)
    if not user:
        return RedirectResponse(url=_cabinet_redirect_url(request), status_code=302)
    key_service = VpnKeyService(db)
    keys = await key_service.get_all_for_user(user.id)
    await key_service.refresh_traffic_for_keys(keys)
    response = templates.TemplateResponse("cabinet/keys.html", {
        "request": request, "app_name": config.web.app_name,
        "user": user, "keys": keys, "now": datetime.now(timezone.utc),
        "is_mini_app": _is_mini_app(request),
        **(await _cabinet_branding_context(db)),
    })
    _persist_cabinet_session(request, response, user)
    return response


@router.get("/cabinet/plans", response_class=HTMLResponse)
async def cabinet_plans(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _require_active_user(request, db)
    if not user:
        return RedirectResponse(url=_cabinet_redirect_url(request), status_code=302)
    plans = await PlanService(db).get_all(only_active=True)
    settings = await BotSettingsService(db).get_all()
    response = templates.TemplateResponse("cabinet/plans.html", {
        "request": request, "app_name": config.web.app_name,
        "user": user, "plans": plans, "settings": settings,
        "is_mini_app": _is_mini_app(request),
        **(await _cabinet_branding_context(db)),
    })
    _persist_cabinet_session(request, response, user)
    return response


@router.get("/cabinet/balance", response_class=HTMLResponse)
async def cabinet_balance(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _require_active_user(request, db)
    if not user:
        return RedirectResponse(url=_cabinet_redirect_url(request), status_code=302)
    payments = await PaymentService(db).get_all(user_id=user.id, limit=100)
    settings = await BotSettingsService(db).get_all()
    response = templates.TemplateResponse("cabinet/balance.html", {
        "request": request, "app_name": config.web.app_name,
        "user": user, "payments": payments, "settings": settings,
        "is_mini_app": _is_mini_app(request),
        **(await _cabinet_branding_context(db)),
    })
    _persist_cabinet_session(request, response, user)
    return response


@router.get("/cabinet/promo", response_class=HTMLResponse)
async def cabinet_promo(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _require_active_user(request, db)
    if not user:
        return RedirectResponse(url=_cabinet_redirect_url(request), status_code=302)
    response = templates.TemplateResponse("cabinet/promo.html", {
        "request": request, "app_name": config.web.app_name,
        "user": user, "is_mini_app": _is_mini_app(request),
        **(await _cabinet_branding_context(db)),
    })
    _persist_cabinet_session(request, response, user)
    return response


@router.post("/cabinet/promo/apply")
async def cabinet_promo_apply(
    request: Request, code: str = Form(""), db: AsyncSession = Depends(get_db),
):
    user = await _require_active_user(request, db)
    if not user:
        return JSONResponse({"ok": False, "message": "Not authenticated"}, status_code=401)
    try:
        promo_service = PromoService(db)
        validation = await promo_service.validate_for_user(code.strip(), user.id)
        promo = validation.promo
        if not promo:
            return JSONResponse(
                {"ok": False, "message": validation.message or "Промокод недействителен"},
                status_code=400,
            )

        promo_type = str(promo.promo_type)
        if promo_type == PromoType.DISCOUNT.value:
            return JSONResponse({
                "ok": True,
                "message": f"Скидка {promo.value}% готова. Примените промокод при покупке тарифа.",
                "promo_type": promo_type,
                "code": promo.code,
                "value": str(promo.value),
            })

        if promo_type == PromoType.BALANCE.value:
            consumed = await promo_service.consume(promo, user.id)
            if not consumed:
                await db.rollback()
                return JSONResponse({"ok": False, "message": "Промокод уже использован"}, status_code=400)
            await UserService(db).add_balance(user.id, promo.value)
            await db.commit()
            return JSONResponse({
                "ok": True,
                "message": f"На баланс зачислено {promo.value} ₽",
                "promo_type": promo_type,
            })

        target_key = await _get_days_promo_target_key(db, user.id)
        if not target_key:
            return JSONResponse({
                "ok": False,
                "message": "Для промокода на дни нужна хотя бы одна подписка",
            }, status_code=400)

        consumed = await promo_service.consume(promo, user.id)
        if not consumed:
            await db.rollback()
            return JSONResponse({"ok": False, "message": "Промокод уже использован"}, status_code=400)

        updated_key = await VpnKeyService(db).extend(target_key.id, int(Decimal(str(promo.value))))
        if not updated_key:
            await db.rollback()
            return JSONResponse({"ok": False, "message": "Не удалось продлить подписку"}, status_code=500)

        await db.commit()
        return JSONResponse({
            "ok": True,
            "message": f"Подписка продлена на {int(Decimal(str(promo.value)))} дн.",
            "promo_type": promo_type,
            "expires_at": updated_key.expires_at.isoformat() if updated_key.expires_at else None,
        })
    except Exception as e:
        await db.rollback()
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)


@router.get("/cabinet/support", response_class=HTMLResponse)
async def cabinet_support(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _require_active_user(request, db)
    if not user:
        return RedirectResponse(url=_cabinet_redirect_url(request), status_code=302)
    tickets = await SupportService(db).get_for_user(user.id)
    response = templates.TemplateResponse("cabinet/support.html", {
        "request": request, "app_name": config.web.app_name,
        "user": user, "tickets": tickets, "is_mini_app": _is_mini_app(request),
        **(await _cabinet_branding_context(db)),
    })
    _persist_cabinet_session(request, response, user)
    return response


@router.post("/cabinet/support/create")
async def cabinet_support_create(
    request: Request, subject: str = Form(""), text: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    user = await _require_active_user(request, db)
    if not user:
        return JSONResponse({"ok": False, "message": "Not authenticated"}, status_code=401)
    if not subject.strip() or not text.strip():
        return JSONResponse({"ok": False, "message": "Заполните тему и сообщение"}, status_code=400)
    ticket = await SupportService(db).create_ticket(user.id, subject.strip(), text.strip())
    return JSONResponse({"ok": True, "ticket_id": ticket.id})


@router.get("/cabinet/support/{ticket_id}", response_class=HTMLResponse)
async def cabinet_support_ticket(
    request: Request, ticket_id: int, db: AsyncSession = Depends(get_db),
):
    user = await _require_active_user(request, db)
    if not user:
        return RedirectResponse(url=_cabinet_redirect_url(request), status_code=302)
    ticket = await SupportService(db).get_by_id(ticket_id)
    if not ticket or ticket.user_id != user.id:
        return RedirectResponse(url="/cabinet/support", status_code=302)
    response = templates.TemplateResponse("cabinet/support_ticket.html", {
        "request": request, "app_name": config.web.app_name,
        "user": user, "ticket": ticket, "is_mini_app": _is_mini_app(request),
        **(await _cabinet_branding_context(db)),
    })
    _persist_cabinet_session(request, response, user)
    return response


@router.post("/cabinet/support/{ticket_id}/message")
async def cabinet_support_message(
    request: Request, ticket_id: int, text: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    user = await _require_active_user(request, db)
    if not user:
        return JSONResponse({"ok": False, "message": "Not authenticated"}, status_code=401)
    if not text.strip():
        return JSONResponse({"ok": False, "message": "Сообщение не может быть пустым"}, status_code=400)
    ticket = await SupportService(db).get_by_id(ticket_id)
    if not ticket or ticket.user_id != user.id:
        return JSONResponse({"ok": False, "message": "Тикет не найден"}, status_code=404)
    await SupportService(db).add_message(ticket_id, user.id, text.strip())
    return JSONResponse({"ok": True})


@router.get("/cabinet/referrals", response_class=HTMLResponse)
async def cabinet_referrals(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _require_active_user(request, db)
    if not user:
        return RedirectResponse(url=_cabinet_redirect_url(request), status_code=302)
    ref_svc = ReferralService(db)
    refs = await ref_svc.get_for_user(user.id)
    top = await ReferralService(db).get_top(limit=20)
    settings = await BotSettingsService(db).get_all()
    bot_username = await _ensure_bot_username(db, settings)
    if bot_username:
        settings["bot_username"] = bot_username
    bonus_type = settings.get("referral_bonus_type", "days")
    bonus_value = settings.get("referral_bonus_value", "3")
    current_bonus_label = ref_svc.format_bonus_label(
        bonus_type,
        bonus_value,
        lang=user.language or "ru",
    )
    response = templates.TemplateResponse("cabinet/referrals.html", {
        "request": request, "app_name": config.web.app_name,
        "user": user, "referrals": refs, "top_referrers": top,
        "settings": settings, "current_bonus_label": current_bonus_label,
        "referral_service": ref_svc, "is_mini_app": _is_mini_app(request),
        **(await _cabinet_branding_context(db)),
    })
    _persist_cabinet_session(request, response, user)
    return response


@router.get("/cabinet/servers", response_class=HTMLResponse)
async def cabinet_servers(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _require_active_user(request, db)
    if not user:
        return RedirectResponse(url=_cabinet_redirect_url(request), status_code=302)
    try:
        from app.services.pasarguard.pasarguard import get_vpn_panel
        hosts = await get_vpn_panel().get_hosts()
    except Exception:
        hosts = []
    response = templates.TemplateResponse("cabinet/servers.html", {
        "request": request, "app_name": config.web.app_name,
        "user": user, "hosts": hosts, "is_mini_app": _is_mini_app(request),
        **(await _cabinet_branding_context(db)),
    })
    _persist_cabinet_session(request, response, user)
    return response


@router.get("/cabinet/guides", response_class=HTMLResponse)
async def cabinet_guides(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _require_active_user(request, db)
    if not user:
        return RedirectResponse(url=_cabinet_redirect_url(request), status_code=302)
    settings = await BotSettingsService(db).get_all()
    response = templates.TemplateResponse("cabinet/guides.html", {
        "request": request, "app_name": config.web.app_name,
        "user": user, "settings": settings, "is_mini_app": _is_mini_app(request),
        **(await _cabinet_branding_context(db)),
    })
    _persist_cabinet_session(request, response, user)
    return response


@router.get("/cabinet/language", response_class=HTMLResponse)
async def cabinet_language(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _require_active_user(request, db)
    if not user:
        return RedirectResponse(url=_cabinet_redirect_url(request), status_code=302)
    response = templates.TemplateResponse("cabinet/language.html", {
        "request": request, "app_name": config.web.app_name,
        "user": user, "is_mini_app": _is_mini_app(request),
        **(await _cabinet_branding_context(db)),
    })
    _persist_cabinet_session(request, response, user)
    return response


@router.post("/cabinet/language")
async def cabinet_language_set(
    request: Request, lang: str = Form("ru"), db: AsyncSession = Depends(get_db),
):
    user = await _require_active_user(request, db)
    if not user:
        return JSONResponse({"ok": False, "message": "Not authenticated"}, status_code=401)
    if lang not in ("ru", "en", "fa"):
        lang = "ru"
    from app.schemas.user import UserUpdate
    await UserService(db).update(user.id, UserUpdate(language=lang))
    return JSONResponse({"ok": True, "language": lang})


@router.get("/cabinet/trial", response_class=HTMLResponse)
async def cabinet_trial(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _require_active_user(request, db)
    if not user:
        return RedirectResponse(url=_cabinet_redirect_url(request), status_code=302)
    settings = await BotSettingsService(db).get_all()
    trial_enabled = settings.get("trial_enabled", "0") == "1"
    trial_days = int(settings.get("trial_days", "3"))
    has_keys = len(await VpnKeyService(db).get_all_for_user(user.id)) > 0
    response = templates.TemplateResponse("cabinet/trial.html", {
        "request": request, "app_name": config.web.app_name,
        "user": user, "trial_enabled": trial_enabled,
        "trial_days": trial_days, "has_keys": has_keys,
        "is_mini_app": _is_mini_app(request),
        **(await _cabinet_branding_context(db)),
    })
    _persist_cabinet_session(request, response, user)
    return response


@router.post("/cabinet/trial/activate")
async def cabinet_trial_activate(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _require_active_user(request, db)
    if not user:
        return JSONResponse({"ok": False, "message": "Not authenticated"}, status_code=401)
    settings = await BotSettingsService(db).get_all()
    if settings.get("trial_enabled", "0") != "1":
        return JSONResponse({"ok": False, "message": "Пробный период отключён"}, status_code=400)

    from sqlalchemy import select
    from app.models.user import User

    locked = await db.execute(
        select(User).where(User.id == user.id).with_for_update()
    )
    locked_user = locked.scalar_one_or_none()
    if not locked_user or locked_user.is_banned:
        return JSONResponse({"ok": False, "message": "Аккаунт заблокирован"}, status_code=403)

    keys = await VpnKeyService(db).get_all_for_user(user.id)
    if keys:
        return JSONResponse({"ok": False, "message": "Пробный период доступен только новым пользователям"}, status_code=400)

    trial_days = int(settings.get("trial_days", "3"))
    try:
        key = await VpnKeyService(db).provision_days(user.id, trial_days, name="Пробный")
        if key:
            return JSONResponse({"ok": True, "message": f"Пробный доступ на {trial_days} дн. активирован!"})
        return JSONResponse({"ok": False, "message": "Ошибка при создании ключа"}, status_code=500)
    except Exception as e:
        log.error("Trial activation error: %s", e)
        return JSONResponse({"ok": False, "message": "Ошибка сервера"}, status_code=500)


@router.post("/cabinet/pay/balance")
async def cabinet_pay_balance(
    request: Request,
    plan_id: int = Form(0),
    promo_code: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    user = await _require_active_user(request, db)
    if not user:
        return JSONResponse({"ok": False, "message": "Not authenticated"}, status_code=401)
    plan = await PlanService(db).get_by_id(plan_id)
    if not plan or not plan.is_active:
        return JSONResponse({"ok": False, "message": "Тариф не найден"}, status_code=404)

    promo = None
    if promo_code.strip():
        promo, promo_error = await _resolve_discount_promo(
            db,
            user_id=user.id,
            plan_id=plan.id,
            promo_code=promo_code,
        )
        if promo_error:
            return JSONResponse({"ok": False, "message": promo_error}, status_code=400)

    charged_amount, discount_amount = _apply_discount(plan.price, promo)

    from sqlalchemy import select
    from app.models.user import User
    locked = await db.execute(select(User).where(User.id == user.id).with_for_update())
    locked_user = locked.scalar_one_or_none()
    if not locked_user or locked_user.is_banned:
        return JSONResponse({"ok": False, "message": "Аккаунт заблокирован"}, status_code=403)
    if locked_user.balance < charged_amount:
        return JSONResponse({"ok": False, "message": "Недостаточно средств"}, status_code=400)

    try:
        payment_meta = {
            "plan_id": plan.id,
            "kind": "cabinet_plan_purchase",
            "original_amount": str(_normalize_money(plan.price)),
            "final_amount": str(charged_amount),
        }
        if promo:
            payment_meta["promo_code"] = promo.code
            payment_meta["discount_value"] = str(promo.value)
            payment_meta["discount_amount"] = str(discount_amount)

        payment = await PaymentService(db).create_pending(
            user.id,
            plan,
            PaymentProvider.BALANCE,
            amount=charged_amount,
            meta=json.dumps(payment_meta),
        )

        if charged_amount > Decimal("0.00"):
            deducted = await UserService(db).deduct_balance(user.id, charged_amount)
            if not deducted:
                await db.rollback()
                return JSONResponse({"ok": False, "message": "Недостаточно средств"}, status_code=400)

        if promo:
            consumed = await PromoService(db).consume(promo, user.id, plan_id=plan.id)
            if not consumed:
                await db.rollback()
                return JSONResponse({"ok": False, "message": "Промокод уже использован"}, status_code=400)

        confirmed = await PaymentService(db).confirm_once(payment.id, f"balance_{payment.id}")
        if not confirmed.payment:
            await db.rollback()
            return JSONResponse({"ok": False, "message": "Ошибка при оплате"}, status_code=500)

        _, key, payment_error = await _finalize_subscription_payment(
            db, payment, f"balance_{payment.id}"
        )
        if payment_error or not key:
            await db.rollback()
            return JSONResponse({"ok": False, "message": "Ошибка при создании ключа"}, status_code=500)

        await db.commit()
        amount_note = f" за {charged_amount} ₽" if charged_amount > Decimal("0.00") else ""
        return JSONResponse({
            "ok": True,
            "status": "succeeded",
            "message": f"Подписка оформлена{amount_note}",
            "access_url": key.access_url,
            "redirect": "/cabinet/keys",
        })
    except Exception as e:
        log.error("Payment error: %s", e)
        await db.rollback()
        return JSONResponse({"ok": False, "message": "Ошибка сервера"}, status_code=500)


@router.post("/cabinet/pay/yookassa")
async def cabinet_pay_yookassa(
    request: Request,
    plan_id: int = Form(0),
    promo_code: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    user = await _require_active_user(request, db)
    if not user:
        return JSONResponse({"ok": False, "message": "Not authenticated"}, status_code=401)

    plan = await PlanService(db).get_by_id(plan_id)
    if not plan or not plan.is_active:
        return JSONResponse({"ok": False, "message": "Тариф не найден"}, status_code=404)

    return await _create_cabinet_yookassa_payment(
        request,
        db,
        user=user,
        plan=plan,
        promo_code=promo_code,
        provider=PaymentProvider.YOOKASSA,
        payment_method=None,
        success_message="Ссылка на оплату картой создана",
    )


@router.post("/cabinet/pay/yookassa-sbp")
async def cabinet_pay_yookassa_sbp(
    request: Request,
    plan_id: int = Form(0),
    promo_code: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    user = await _require_active_user(request, db)
    if not user:
        return JSONResponse({"ok": False, "message": "Not authenticated"}, status_code=401)

    settings = await BotSettingsService(db).get_all()
    if settings.get("ps_sbp_enabled", "0") != "1":
        return JSONResponse({"ok": False, "message": "Оплата через СБП сейчас недоступна"}, status_code=400)

    plan = await PlanService(db).get_by_id(plan_id)
    if not plan or not plan.is_active:
        return JSONResponse({"ok": False, "message": "Тариф не найден"}, status_code=404)

    return await _create_cabinet_yookassa_payment(
        request,
        db,
        user=user,
        plan=plan,
        promo_code=promo_code,
        provider=PaymentProvider.YOOKASSA_SBP,
        payment_method="sbp",
        success_message="Ссылка на оплату через СБП создана",
    )


@router.post("/cabinet/topup/yookassa")
async def cabinet_topup_yookassa(
    request: Request,
    amount: str = Form("0"),
    db: AsyncSession = Depends(get_db),
):
    user = await _require_active_user(request, db)
    if not user:
        return JSONResponse({"ok": False, "message": "Not authenticated"}, status_code=401)

    try:
        topup_amount = _normalize_money(amount)
    except (InvalidOperation, ValueError):
        return JSONResponse({"ok": False, "message": "Некорректная сумма"}, status_code=400)

    if topup_amount < Decimal("100.00"):
        return JSONResponse({"ok": False, "message": "Минимальная сумма пополнения 100 ₽"}, status_code=400)
    if topup_amount > Decimal("100000.00"):
        return JSONResponse({"ok": False, "message": "Слишком большая сумма пополнения"}, status_code=400)

    try:
        yk = await YookassaService.create()
        payment = await PaymentService(db).create_topup_pending(
            user_id=user.id,
            amount=topup_amount,
            provider=PaymentProvider.YOOKASSA,
            meta=json.dumps({"kind": "cabinet_topup"}),
        )
        return_url = _absolute_cabinet_url(request, "/cabinet/balance", payment_id=payment.id)
        yk_payment = await yk.create_payment(
            amount=topup_amount,
            description=f"Пополнение баланса на {topup_amount} ₽",
            return_url=return_url,
            metadata={"payment_id": str(payment.id)},
        )
        payment.external_id = yk_payment.id
        await db.commit()

        return JSONResponse({
            "ok": True,
            "status": "pending",
            "message": "Ссылка на пополнение создана",
            "payment_id": payment.id,
            "payment_url": yk_payment.confirmation.confirmation_url,
            "return_url": return_url,
        })
    except Exception as e:
        log.error("Yookassa cabinet topup error: %s", e)
        await db.rollback()
        return JSONResponse({"ok": False, "message": "Не удалось создать платёж"}, status_code=502)


@router.get("/cabinet/pay/status/{payment_id}")
@router.post("/cabinet/pay/status/{payment_id}")
async def cabinet_pay_status(
    request: Request, payment_id: int, db: AsyncSession = Depends(get_db),
):
    user = await _require_active_user(request, db)
    if not user:
        return JSONResponse({"ok": False, "message": "Not authenticated"}, status_code=401)

    payment = await PaymentService(db).get_by_id(payment_id)
    if not payment or payment.user_id != user.id:
        return JSONResponse({"ok": False, "message": "Платёж не найден"}, status_code=404)

    if payment.status == PaymentStatus.FAILED.value:
        return JSONResponse({
            "ok": False,
            "status": "failed",
            "message": "Платёж не был завершён",
        })

    if payment.status == PaymentStatus.CANCELLED.value:
        return JSONResponse({
            "ok": False,
            "status": "cancelled",
            "message": "Платёж отменён",
        })

    if payment.status == PaymentStatus.SUCCEEDED.value and payment.payment_type == "topup":
        result = await PaymentFulfillmentService(db).confirm_topup_and_credit_once(
            payment.id,
            payment.external_id or f"cabinet_topup_{payment.id}",
        )
        await db.commit()
        return JSONResponse({
            "ok": True,
            "status": "succeeded",
            "message": "Баланс успешно пополнен",
            "balance": str(result.balance) if result.balance is not None else None,
            "redirect": "/cabinet/balance",
        })

    if payment.status == PaymentStatus.SUCCEEDED.value:
        payment, key, payment_error = await _finalize_subscription_payment(
            db,
            payment,
            payment.external_id or f"cabinet_{payment.id}",
        )
        await db.commit()
        if payment_error:
            return JSONResponse({"ok": False, "status": "failed", "message": payment_error}, status_code=400)
        if key:
            return JSONResponse({
                "ok": True,
                "status": "succeeded",
                "message": "Оплата подтверждена, подписка готова",
                "access_url": key.access_url,
                "redirect": "/cabinet/keys",
            })
        return JSONResponse({
            "ok": True,
            "status": "processing",
            "message": "Оплата получена, ключ ещё подготавливается",
            "redirect": "/cabinet/keys",
        })

    if payment.provider not in (
        PaymentProvider.YOOKASSA.value,
        PaymentProvider.YOOKASSA_SBP.value,
    ) or not payment.external_id:
        return JSONResponse({
            "ok": True,
            "status": payment.status,
            "message": "Платёж ещё ожидает подтверждения",
        })

    try:
        yk = await YookassaService.create()
        yk_payment = await yk.get_payment(payment.external_id)
    except Exception as e:
        log.warning("Cabinet payment status check failed: {}", e)
        return JSONResponse({
            "ok": False,
            "message": "Не удалось проверить статус платежа",
        }, status_code=502)

    if yk_payment.status == "succeeded":
        if payment.payment_type == "topup":
            result = await PaymentFulfillmentService(db).confirm_topup_and_credit_once(
                payment.id,
                yk_payment.id,
            )
            await db.commit()
            return JSONResponse({
                "ok": True,
                "status": "succeeded",
                "message": "Баланс успешно пополнен",
                "balance": str(result.balance) if result.balance is not None else None,
                "redirect": "/cabinet/balance",
            })

        payment, key, payment_error = await _finalize_subscription_payment(
            db,
            payment,
            yk_payment.id,
        )
        await db.commit()
        if payment_error:
            return JSONResponse({"ok": False, "status": "failed", "message": payment_error}, status_code=400)
        if key:
            return JSONResponse({
                "ok": True,
                "status": "succeeded",
                "message": "Оплата подтверждена, подписка готова",
                "access_url": key.access_url,
                "redirect": "/cabinet/keys",
            })
        return JSONResponse({
            "ok": True,
            "status": "processing",
            "message": "Оплата получена, ключ ещё подготавливается",
            "redirect": "/cabinet/keys",
        })

    if yk_payment.status in ("canceled", "expired"):
        await PaymentService(db).fail(payment.id)
        await db.commit()
        return JSONResponse({
            "ok": False,
            "status": "failed",
            "message": "Платёж отменён или истёк",
        })

    return JSONResponse({
        "ok": True,
        "status": "pending",
        "message": "Платёж ещё обрабатывается",
    })


@router.get("/cabinet/logout")
async def cabinet_logout(request: Request, db: AsyncSession = Depends(get_db)):
    token = request.cookies.get("cabinet_session")
    if token:
        from app.utils.security import decode_access_token_full
        payload = decode_access_token_full(token)
        if payload:
            from app.services.token_blacklist import TokenBlacklistService
            jti = payload.get("jti", "")
            sub = payload.get("sub", "")
            bk = TokenBlacklistService(db)
            await bk.blacklist_jti(jti, sub, expires_at=None)
    resp = RedirectResponse(url="/cabinet/", status_code=302)
    resp.delete_cookie("cabinet_session", path="/", secure=_is_secure_request(request), httponly=True, samesite="lax")
    return resp
