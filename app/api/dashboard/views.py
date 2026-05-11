from datetime import datetime, timezone
from pathlib import Path

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
from app.services.promo import PromoService
from app.services.support import SupportService
from app.services.referral import ReferralService
from app.services.bot_settings import BotSettingsService
from app.models.payment import PaymentProvider
from app.utils.log import log

from .auth import get_dashboard_user, try_miniapp_auth, set_session_cookie

router = APIRouter()

_tpl_path = Path(__file__).resolve().parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(_tpl_path))


async def _require_user(request: Request, db: AsyncSession):
    user = await get_dashboard_user(request, db)
    if user:
        return user
    user = await try_miniapp_auth(request, db)
    if user:
        return user
    return None


def _is_mini_app(request: Request) -> bool:
    return bool(request.headers.get("X-Telegram-Init-Data", ""))


@router.get("/dashboard", response_class=HTMLResponse)
@router.get("/dashboard/", response_class=HTMLResponse)
async def dashboard_index(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _require_user(request, db)
    if user:
        if user.is_banned:
            return templates.TemplateResponse("dashboard/login.html", {
                "request": request, "app_name": config.web.app_name, "settings": {},
                "error": "Аккаунт заблокирован", "is_mini_app": _is_mini_app(request),
            })
        svc = await BotSettingsService(db).get_all()
        plans = await PlanService(db).get_all(only_active=True)
        keys = await VpnKeyService(db).get_active_for_user(user.id)
        return templates.TemplateResponse("dashboard/index.html", {
            "request": request, "app_name": config.web.app_name,
            "app_version": config.web.app_version,
            "user": user, "plans": plans, "keys": keys,
            "settings": svc, "now": datetime.now(timezone.utc),
            "is_mini_app": _is_mini_app(request),
        })

    svc = await BotSettingsService(db).get_all()
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    if init_data:
        return templates.TemplateResponse("dashboard/login.html", {
            "request": request, "app_name": config.web.app_name, "settings": svc,
            "error": "Ошибка авторизации. Попробуйте заново.",
            "is_mini_app": True,
        })
    return templates.TemplateResponse("dashboard/login.html", {
        "request": request, "app_name": config.web.app_name, "settings": svc,
        "error": None, "is_mini_app": False,
    })


@router.get("/dashboard/profile", response_class=HTMLResponse)
async def dashboard_profile(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _require_user(request, db)
    if not user:
        return RedirectResponse(url="/dashboard/", status_code=302)
    keys = await VpnKeyService(db).get_all_for_user(user.id)
    payments = await PaymentService(db).get_all(user_id=user.id, limit=50)
    referrals = await ReferralService(db).count_referrals(user.id)
    return templates.TemplateResponse("dashboard/profile.html", {
        "request": request, "app_name": config.web.app_name,
        "user": user, "keys": keys, "payments": payments,
        "referrals_count": referrals, "now": datetime.now(timezone.utc),
        "is_mini_app": _is_mini_app(request),
    })


@router.get("/dashboard/keys", response_class=HTMLResponse)
async def dashboard_keys(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _require_user(request, db)
    if not user:
        return RedirectResponse(url="/dashboard/", status_code=302)
    keys = await VpnKeyService(db).get_all_for_user(user.id)
    return templates.TemplateResponse("dashboard/keys.html", {
        "request": request, "app_name": config.web.app_name,
        "user": user, "keys": keys, "now": datetime.now(timezone.utc),
        "is_mini_app": _is_mini_app(request),
    })


@router.get("/dashboard/plans", response_class=HTMLResponse)
async def dashboard_plans(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _require_user(request, db)
    if not user:
        return RedirectResponse(url="/dashboard/", status_code=302)
    plans = await PlanService(db).get_all(only_active=True)
    settings = await BotSettingsService(db).get_all()
    return templates.TemplateResponse("dashboard/plans.html", {
        "request": request, "app_name": config.web.app_name,
        "user": user, "plans": plans, "settings": settings,
        "is_mini_app": _is_mini_app(request),
    })


@router.get("/dashboard/balance", response_class=HTMLResponse)
async def dashboard_balance(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _require_user(request, db)
    if not user:
        return RedirectResponse(url="/dashboard/", status_code=302)
    payments = await PaymentService(db).get_all(user_id=user.id, limit=100)
    settings = await BotSettingsService(db).get_all()
    return templates.TemplateResponse("dashboard/balance.html", {
        "request": request, "app_name": config.web.app_name,
        "user": user, "payments": payments, "settings": settings,
        "is_mini_app": _is_mini_app(request),
    })


@router.get("/dashboard/promo", response_class=HTMLResponse)
async def dashboard_promo(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _require_user(request, db)
    if not user:
        return RedirectResponse(url="/dashboard/", status_code=302)
    return templates.TemplateResponse("dashboard/promo.html", {
        "request": request, "app_name": config.web.app_name,
        "user": user, "is_mini_app": _is_mini_app(request),
    })


@router.post("/dashboard/promo/apply")
async def dashboard_promo_apply(
    request: Request, code: str = Form(""), db: AsyncSession = Depends(get_db),
):
    user = await _require_user(request, db)
    if not user:
        return JSONResponse({"ok": False, "message": "Not authenticated"}, status_code=401)
    try:
        promo = await PromoService(db).apply(code.strip(), user.id)
        if promo:
            return JSONResponse({"ok": True, "message": f"Промокод активирован! Скидка: {promo.value}"})
        return JSONResponse({"ok": False, "message": "Промокод не найден или истёк"}, status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)


@router.get("/dashboard/support", response_class=HTMLResponse)
async def dashboard_support(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _require_user(request, db)
    if not user:
        return RedirectResponse(url="/dashboard/", status_code=302)
    tickets = await SupportService(db).get_for_user(user.id)
    return templates.TemplateResponse("dashboard/support.html", {
        "request": request, "app_name": config.web.app_name,
        "user": user, "tickets": tickets, "is_mini_app": _is_mini_app(request),
    })


@router.post("/dashboard/support/create")
async def dashboard_support_create(
    request: Request, subject: str = Form(""), text: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    user = await _require_user(request, db)
    if not user:
        return JSONResponse({"ok": False, "message": "Not authenticated"}, status_code=401)
    if not subject.strip() or not text.strip():
        return JSONResponse({"ok": False, "message": "Заполните тему и сообщение"}, status_code=400)
    ticket = await SupportService(db).create_ticket(user.id, subject.strip(), text.strip())
    return JSONResponse({"ok": True, "ticket_id": ticket.id})


@router.get("/dashboard/support/{ticket_id}", response_class=HTMLResponse)
async def dashboard_support_ticket(
    request: Request, ticket_id: int, db: AsyncSession = Depends(get_db),
):
    user = await _require_user(request, db)
    if not user:
        return RedirectResponse(url="/dashboard/", status_code=302)
    ticket = await SupportService(db).get_by_id(ticket_id)
    if not ticket or ticket.user_id != user.id:
        return RedirectResponse(url="/dashboard/support", status_code=302)
    return templates.TemplateResponse("dashboard/support_ticket.html", {
        "request": request, "app_name": config.web.app_name,
        "user": user, "ticket": ticket, "is_mini_app": _is_mini_app(request),
    })


@router.post("/dashboard/support/{ticket_id}/message")
async def dashboard_support_message(
    request: Request, ticket_id: int, text: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    user = await _require_user(request, db)
    if not user:
        return JSONResponse({"ok": False, "message": "Not authenticated"}, status_code=401)
    if not text.strip():
        return JSONResponse({"ok": False, "message": "Сообщение не может быть пустым"}, status_code=400)
    await SupportService(db).add_message(ticket_id, user.id, text.strip())
    return JSONResponse({"ok": True})


@router.get("/dashboard/referrals", response_class=HTMLResponse)
async def dashboard_referrals(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _require_user(request, db)
    if not user:
        return RedirectResponse(url="/dashboard/", status_code=302)
    refs = await ReferralService(db).get_for_user(user.id)
    top = await ReferralService(db).get_top(limit=20)
    settings = await BotSettingsService(db).get_all()
    return templates.TemplateResponse("dashboard/referrals.html", {
        "request": request, "app_name": config.web.app_name,
        "user": user, "referrals": refs, "top_referrers": top,
        "settings": settings, "is_mini_app": _is_mini_app(request),
    })


@router.get("/dashboard/servers", response_class=HTMLResponse)
async def dashboard_servers(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _require_user(request, db)
    if not user:
        return RedirectResponse(url="/dashboard/", status_code=302)
    try:
        from app.services.pasarguard.pasarguard import get_vpn_panel
        hosts = await get_vpn_panel().get_hosts()
    except Exception:
        hosts = []
    return templates.TemplateResponse("dashboard/servers.html", {
        "request": request, "app_name": config.web.app_name,
        "user": user, "hosts": hosts, "is_mini_app": _is_mini_app(request),
    })


@router.get("/dashboard/guides", response_class=HTMLResponse)
async def dashboard_guides(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _require_user(request, db)
    if not user:
        return RedirectResponse(url="/dashboard/", status_code=302)
    settings = await BotSettingsService(db).get_all()
    return templates.TemplateResponse("dashboard/guides.html", {
        "request": request, "app_name": config.web.app_name,
        "user": user, "settings": settings, "is_mini_app": _is_mini_app(request),
    })


@router.get("/dashboard/language", response_class=HTMLResponse)
async def dashboard_language(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _require_user(request, db)
    if not user:
        return RedirectResponse(url="/dashboard/", status_code=302)
    return templates.TemplateResponse("dashboard/language.html", {
        "request": request, "app_name": config.web.app_name,
        "user": user, "is_mini_app": _is_mini_app(request),
    })


@router.post("/dashboard/language")
async def dashboard_language_set(
    request: Request, lang: str = Form("ru"), db: AsyncSession = Depends(get_db),
):
    user = await _require_user(request, db)
    if not user:
        return JSONResponse({"ok": False, "message": "Not authenticated"}, status_code=401)
    if lang not in ("ru", "en", "fa"):
        lang = "ru"
    from app.schemas.user import UserUpdate
    await UserService(db).update(user.id, UserUpdate(language=lang))
    return JSONResponse({"ok": True, "language": lang})


@router.get("/dashboard/trial", response_class=HTMLResponse)
async def dashboard_trial(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _require_user(request, db)
    if not user:
        return RedirectResponse(url="/dashboard/", status_code=302)
    settings = await BotSettingsService(db).get_all()
    trial_enabled = settings.get("trial_enabled", "0") == "1"
    trial_days = int(settings.get("trial_days", "3"))
    has_keys = len(await VpnKeyService(db).get_all_for_user(user.id)) > 0
    return templates.TemplateResponse("dashboard/trial.html", {
        "request": request, "app_name": config.web.app_name,
        "user": user, "trial_enabled": trial_enabled,
        "trial_days": trial_days, "has_keys": has_keys,
        "is_mini_app": _is_mini_app(request),
    })


@router.post("/dashboard/trial/activate")
async def dashboard_trial_activate(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _require_user(request, db)
    if not user:
        return JSONResponse({"ok": False, "message": "Not authenticated"}, status_code=401)
    settings = await BotSettingsService(db).get_all()
    if settings.get("trial_enabled", "0") != "1":
        return JSONResponse({"ok": False, "message": "Пробный период отключён"}, status_code=400)
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


@router.post("/dashboard/pay/balance")
async def dashboard_pay_balance(
    request: Request, plan_id: int = Form(0), db: AsyncSession = Depends(get_db),
):
    user = await _require_user(request, db)
    if not user:
        return JSONResponse({"ok": False, "message": "Not authenticated"}, status_code=401)
    plan = await PlanService(db).get_by_id(plan_id)
    if not plan or not plan.is_active:
        return JSONResponse({"ok": False, "message": "Тариф не найден"}, status_code=404)
    if user.balance < plan.price:
        return JSONResponse({"ok": False, "message": "Недостаточно средств"}, status_code=400)
    payment = await PaymentService(db).create_pending(user.id, plan, PaymentProvider.BALANCE)
    await UserService(db).deduct_balance(user.id, plan.price)
    confirmed = await PaymentService(db).confirm(payment.id, f"balance_{payment.id}")
    if not confirmed:
        return JSONResponse({"ok": False, "message": "Ошибка при оплате"}, status_code=500)
    key = await VpnKeyService(db).provision(user.id, plan)
    if key:
        return JSONResponse({"ok": True, "message": "Подписка оформлена!", "access_url": key.access_url})
    return JSONResponse({"ok": False, "message": "Ошибка при создании ключа"}, status_code=500)


@router.get("/dashboard/logout")
async def dashboard_logout():
    resp = RedirectResponse(url="/dashboard/", status_code=302)
    resp.delete_cookie("dashboard_session")
    return resp
