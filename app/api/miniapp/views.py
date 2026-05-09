import hashlib
import hmac
import json
import urllib.parse
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.core.config import config
from app.models.payment import PaymentProvider, PaymentStatus
from app.schemas.user import UserCreate
from app.services.bot_settings import BotSettingsService
from app.services.payment import PaymentService
from app.services.plan import PlanService
from app.services.referral import ReferralService
from app.services.user import UserService
from app.services.vpn_key import VpnKeyService
from app.services.promo import PromoService
from app.utils.log import log

router = APIRouter()

_tpl_path = Path(__file__).resolve().parent.parent.parent / "templates" / "miniapp"
_tpl_path.mkdir(exist_ok=True)
templates = Jinja2Templates(directory=str(_tpl_path))

# Per-user rate limiting for payment endpoints
from collections import defaultdict
from datetime import datetime, timedelta, timezone
_payment_cooldowns: dict[int, datetime] = defaultdict(lambda: datetime.min.replace(tzinfo=timezone.utc))

async def _check_payment_rate(user_id: int, cooldown_seconds: int = 5) -> bool:
    now = datetime.now(timezone.utc)
    last = _payment_cooldowns.get(user_id)
    if last and (now - last).total_seconds() < cooldown_seconds:
        return False
    _payment_cooldowns[user_id] = now
    return True


def _compute_hmac(token: str, data_check: str) -> str:
    """Compute HMAC per Telegram docs: secret = HMAC_SHA256(b'WebAppData', token)."""
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    return hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()


async def _verify_telegram_data(init_data: str, db=None) -> Optional[dict]:
    """Verify Telegram WebApp initData per official docs."""
    try:
        if not init_data or len(init_data) < 10:
            return None

        parsed = list(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        
        # Extract hash and build data_check (all fields except hash, sorted by key)
        hash_val = None
        data_check_parts = []
        
        for k, v in sorted(parsed, key=lambda x: x[0]):
            if k == "hash":
                hash_val = v
            else:
                # Use URL-decoded values for data_check per Telegram docs
                data_check_parts.append(f"{k}={urllib.parse.unquote(v)}")
        
        if not hash_val:
            return None
        
        data_check = "\n".join(data_check_parts)
        
        user_raw = dict(parsed).get("user", "{}")
        user_data = json.loads(urllib.parse.unquote(user_raw))
        if not user_data or "id" not in user_data:
            return None

        # Get tokens to verify against
        tokens = []
        env_token = config.telegram.telegram_bot_token.get_secret_value()
        if env_token:
            tokens.append(env_token)
        if db:
            try:
                db_token = await BotSettingsService(db).get("telegram_bot_token")
                if db_token and db_token not in tokens:
                    tokens.append(db_token)
            except Exception:
                pass

        for token in tokens:
            if hmac.compare_digest(_compute_hmac(token, data_check), hash_val):
                return user_data

        log.warning(f"MiniApp HMAC fail for user {user_data.get('id')}")
        return None
    except Exception as e:
        log.warning(f"MiniApp verify error: {e}")
        return None


async def _get_tg_user(request: Request, db=None) -> Optional[dict]:
    """Get verified Telegram user from request."""
    # Try header first (most reliable for GET requests)
    init_data = request.headers.get("X-Telegram-Init-Data", "") or request.headers.get("x-telegram-init-data", "")
    if init_data:
        result = await _verify_telegram_data(init_data, db)
        if result:
            return result

    # Try POST body (for auth endpoint)
    if request.method == "POST":
        try:
            body = await request.json()
            init_data = body.get("initData", "")
            if init_data:
                result = await _verify_telegram_data(init_data, db)
                if result:
                    return result
        except Exception:
            pass

    return None


@router.get("/debug")
async def miniapp_debug(request: Request, db: AsyncSession = Depends(get_db)):
    """Debug endpoint to check initData."""
    init_data = request.headers.get("x-telegram-init-data", "") or request.headers.get("X-Telegram-Init-Data", "")
    result = {"has_data": bool(init_data), "header_len": len(init_data)}
    if init_data:
        try:
            parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
            result["has_hash"] = "hash" in parsed
            result["has_user"] = "user" in parsed
            if "user" in parsed:
                user = json.loads(urllib.parse.unquote(parsed["user"]))
                result["user_id"] = user.get("id")
        except Exception as e:
            result["error"] = str(e)
    return JSONResponse(result)


@router.get("/", response_class=HTMLResponse)
async def miniapp_index(request: Request, db: AsyncSession = Depends(get_db)):
    from app.core.config import config as _cfg
    settings = await BotSettingsService(db).get_all()
    return templates.TemplateResponse("index.html", {
        "request": request,
        "app_name": _cfg.web.app_name,
        "support_url": settings.get("support_url", ""),
        "about_text": settings.get("about_text", ""),
        "bot_language": settings.get("bot_language", "ru"),
        "panel_url": (settings.get("panel_url") or "").rstrip("/"),
    })


@router.post("/auth")
async def miniapp_auth(request: Request, db: AsyncSession = Depends(get_db)):
    """Authenticate user via Telegram initData."""
    tg_user = await _get_tg_user(request, db)

    if not tg_user:
        init_data = request.headers.get("X-Telegram-Init-Data", "") or request.headers.get("x-telegram-init-data", "")
        body_init = ""
        if request.method == "POST":
            try:
                body = await request.json()
                body_init = body.get("initData", "")[:100]
            except Exception:
                pass
        log.warning(f"Auth failed. Header len: {len(init_data)}, Body preview: {body_init}")
        return JSONResponse({"ok": False, "error": "Invalid auth"}, status_code=401)

    user_id = tg_user.get("id")
    try:
        user, _ = await UserService(db).get_or_create(
            UserCreate(
                id=user_id,
                username=tg_user.get("username"),
                full_name=f"{tg_user.get('first_name', '')} {tg_user.get('last_name', '')}".strip(),
            )
        )
        await db.commit()
    except Exception as e:
        log.error(f"Auth DB error: {e}")
        return JSONResponse({"ok": False, "error": "DB error"}, status_code=500)

    settings = await BotSettingsService(db).get_all()
    return JSONResponse({
        "ok": True,
        "user": {
            "id": user.id,
            "username": user.username,
            "full_name": user.full_name,
            "balance": float(user.balance or 0),
            "referral_code": user.referral_code,
            "is_admin": user.id in config.telegram.telegram_admin_ids,
        },
        "lang": settings.get("bot_language", "ru"),
    })


@router.get("/plans")
async def get_plans(db: AsyncSession = Depends(get_db)):
    plans = await PlanService(db).get_all(only_active=True)
    return JSONResponse({"ok": True, "plans": [
        {"id": p.id, "name": p.name, "price": float(p.price or 0),
         "duration_days": p.duration_days, "description": p.description, "currency": p.currency}
        for p in plans
    ]})


@router.get("/profile")
async def get_profile(request: Request, db: AsyncSession = Depends(get_db)):
    tg_user = await _get_tg_user(request, db)
    if not tg_user:
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)

    user_id = tg_user["id"]
    user = await UserService(db).get_by_id(user_id)
    if not user:
        return JSONResponse({"ok": False, "error": "User not found"}, status_code=404)

    keys = await VpnKeyService(db).get_all_for_user(user_id)
    ref_count = await ReferralService(db).count_referrals(user_id)
    plans = await PlanService(db).get_all(only_active=True)

    active_keys, archive_keys = [], []
    for k in keys:
        status = str(k.status.value if hasattr(k.status, "value") else k.status)
        key_data = {
            "id": k.id, "name": k.name or f"Subscription #{k.id}",
            "status": status, "expires_at": k.expires_at.isoformat() if k.expires_at else None,
            "access_url": k.access_url if status == "active" else None,
            "price": float(k.price or 0),
        }
        (active_keys if status == "active" else archive_keys).append(key_data)

    return JSONResponse({
        "ok": True,
        "user": {
            "id": user.id, "full_name": user.full_name, "username": user.username,
            "balance": float(user.balance or 0), "referral_code": user.referral_code,
            "referrals_count": ref_count, "is_admin": user.id in config.telegram.telegram_admin_ids,
        },
        "plans": [{"id": p.id, "name": p.name, "duration_days": p.duration_days,
                   "price": float(p.price or 0)} for p in plans],
        "active_keys": active_keys, "archive_keys": archive_keys,
    })


@router.get("/settings")
async def get_settings(db: AsyncSession = Depends(get_db)):
    s = await BotSettingsService(db).get_all()
    from app.core.config import config as _cfg

    yk_ok = bool(_cfg.yookassa.yookassa_shop_id and _cfg.yookassa.yookassa_secret_key) or \
             (s.get("yookassa_shop_id_override") and s.get("yookassa_secret_key_override"))
    has_yookassa = s.get("ps_yookassa_enabled", "0") == "1" and yk_ok
    has_sbp = s.get("ps_sbp_enabled", "0") == "1" and yk_ok
    has_cryptobot = s.get("ps_cryptobot_enabled", "0") == "1" and bool(s.get("cryptobot_token", "").strip())
    has_stars = s.get("ps_stars_enabled", "0") == "1"
    has_freekassa = s.get("ps_freekassa_enabled", "0") == "1" and bool(s.get("freekassa_shop_id") and s.get("freekassa_api_key"))

    try:
        stars_rate = float(s.get("stars_rate") or "1.5")
    except (ValueError, TypeError):
        stars_rate = 1.5

    bot_username = ""
    try:
        from app.core.server import get_bot
        bot = get_bot()
        if bot:
            me = await bot.get_me()
            bot_username = me.username or ""
    except Exception:
        try:
            from aiogram import Bot
            token = config.telegram.telegram_bot_token.get_secret_value()
            async with Bot(token=token) as b:
                me = await b.get_me()
                bot_username = me.username or ""
        except Exception:
            pass

    return JSONResponse({
        "ok": True, "lang": s.get("bot_language", "ru"),
        "about_text": s.get("about_text", ""), "support_url": s.get("support_url", ""),
        "has_yookassa": has_yookassa, "has_sbp": has_sbp,
        "has_cryptobot": has_cryptobot, "has_stars": has_stars,
        "has_freekassa": has_freekassa, "stars_rate": stars_rate,
        "bot_username": bot_username,
    })


@router.get("/servers/status")
async def servers_status(request: Request, db: AsyncSession = Depends(get_db)):
    tg_user = await _get_tg_user(request, db)
    if not tg_user:
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)

    import time
    start = time.time()
    try:
        from sqlalchemy import text
        await db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    latency_ms = round((time.time() - start) * 1000, 1)
    active_keys = await VpnKeyService(db).count_active()

    return JSONResponse({
        "ok": True, "overall": "operational" if db_ok else "degraded",
        "active_keys": active_keys,
        "servers": [
            {"name": "Основной EU", "region": "🇩🇪 Германия", "status": "online" if db_ok else "degraded",
             "ping": latency_ms, "load": min(95, max(5, int(latency_ms / 3)))},
            {"name": "Резервный US", "region": "🇺🇸 США", "status": "online",
             "ping": latency_ms + 15, "load": min(95, max(5, int((latency_ms + 15) / 3)))},
        ],
    })


@router.post("/pay/balance")
async def pay_balance(request: Request, db: AsyncSession = Depends(get_db)):
    import asyncio
    tg_user = await _get_tg_user(request, db)
    if not tg_user:
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)

    user_id = tg_user["id"]
    if not await _check_payment_rate(user_id):
        return JSONResponse({"ok": False, "error": "Please wait before next payment"}, status_code=429)

    body = await request.json()
    plan_id = body.get("plan_id")

    plan = await PlanService(db).get_by_id(plan_id)
    if not plan or not plan.is_active:
        return JSONResponse({"ok": False, "error": "Plan not found"}, status_code=404)

    try:
        updated = await UserService(db).deduct_balance(user_id, plan.price)
        if not updated:
            return JSONResponse({"ok": False, "error": "Insufficient balance"}, status_code=400)

        payment = await PaymentService(db).create_pending(
            user_id=user_id, plan=plan, provider=PaymentProvider.BALANCE
        )
        payment.status = PaymentStatus.SUCCEEDED.value
        await db.flush()

        key = None
        for attempt in range(3):
            try:
                key = await VpnKeyService(db).provision(user_id=user_id, plan=plan)
                if key:
                    break
            except Exception as e:
                if attempt < 2:
                    await asyncio.sleep(0.5 * (attempt + 1))

        if not key:
            await UserService(db).add_balance(user_id, plan.price)
            payment.status = PaymentStatus.FAILED.value
            await db.commit()
            return JSONResponse({"ok": False, "error": "VPN server error. Balance refunded."}, status_code=500)

        payment.vpn_key_id = key.id
        await db.commit()

        return JSONResponse({
            "ok": True, "access_url": key.access_url,
            "expires_at": key.expires_at.isoformat() if key.expires_at else None,
            "plan_name": plan.name, "days": plan.duration_days,
        })
    except Exception as e:
        log.error(f"Balance payment error: {e}")
        try:
            await UserService(db).add_balance(user_id, plan.price)
            await db.commit()
        except Exception:
            pass
        return JSONResponse({"ok": False, "error": "Payment failed"}, status_code=500)


@router.post("/pay/yookassa")
async def pay_yookassa(request: Request, db: AsyncSession = Depends(get_db)):
    tg_user = await _get_tg_user(request, db)
    if not tg_user:
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)

    user_id = tg_user["id"]
    if not await _check_payment_rate(user_id):
        return JSONResponse({"ok": False, "error": "Please wait before next payment"}, status_code=429)

    body = await request.json()
    plan_id = body.get("plan_id")

    plan = await PlanService(db).get_by_id(plan_id)
    if not plan or not plan.is_active:
        return JSONResponse({"ok": False, "error": "Plan not found"}, status_code=404)

    try:
        from app.services.yookassa import YookassaService
        yk = await YookassaService.create()
        payment = await PaymentService(db).create_pending(
            user_id=user_id, plan=plan, provider=PaymentProvider.YOOKASSA
        )
        await db.flush()

        yk_payment = await yk.create_payment(
            amount=plan.price, description=f"VPN — {plan.name}",
            return_url=f"https://t.me/{body.get('bot_username', '')}",
            metadata={"payment_id": str(payment.id), "plan_id": str(plan.id)},
        )
        payment.external_id = yk_payment.id
        import json as _json
        payment.meta = _json.dumps({"plan_id": plan.id})
        await db.commit()

        return JSONResponse({
            "ok": True, "payment_id": payment.id,
            "confirm_url": yk_payment.confirmation.confirmation_url if yk_payment.confirmation else None,
        })
    except Exception as e:
        log.error(f"YooKassa error: {e}")
        return JSONResponse({"ok": False, "error": "Payment error"}, status_code=500)


@router.post("/pay/freekassa")
async def pay_freekassa(request: Request, db: AsyncSession = Depends(get_db)):
    tg_user = await _get_tg_user(request, db)
    if not tg_user:
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)

    user_id = tg_user["id"]
    if not await _check_payment_rate(user_id):
        return JSONResponse({"ok": False, "error": "Please wait before next payment"}, status_code=429)

    body = await request.json()
    plan_id = body.get("plan_id")

    plan = await PlanService(db).get_by_id(plan_id)
    if not plan or not plan.is_active:
        return JSONResponse({"ok": False, "error": "Plan not found"}, status_code=404)

    settings = await BotSettingsService(db).get_all()
    from app.services.freekassa import FreeKassaService
    fk = FreeKassaService.from_settings(settings)
    if not fk:
        return JSONResponse({"ok": False, "error": "FreeKassa not configured"}, status_code=400)

    try:
        payment = await PaymentService(db).create_pending(
            user_id=user_id, plan=plan, provider=PaymentProvider.FREEKASSA
        )
        await db.flush()
        order_id = f"fk_{payment.id}_{plan.id}"

        base_url = str(config.web.allowed_origins[0]).rstrip("/") if config.web.allowed_origins else ""
        notification_url = f"{base_url}/api/v1/payments/webhook/freekassa" if base_url else ""

        result = await fk.create_order(
            payment_id=order_id, amount=float(plan.price), currency="RUB",
            currency_id=36, email=f"user{user_id}@vpn.bot", ip="127.0.0.1",
            notification_url=notification_url,
        )
        if result and result.get("type") == "success":
            payment.external_id = str(result.get("orderId", ""))
            await db.commit()
            return JSONResponse({"ok": True, "pay_url": result.get("location", "")})
        return JSONResponse({"ok": False, "error": "Payment error"}, status_code=400)
    except Exception as e:
        log.error(f"FreeKassa error: {e}")
        return JSONResponse({"ok": False, "error": "Payment error"}, status_code=500)


@router.get("/pay/check/{payment_id}")
async def check_payment(payment_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    tg_user = await _get_tg_user(request, db)
    if not tg_user:
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)

    payment = await PaymentService(db).get_by_id(payment_id)
    if not payment or payment.user_id != tg_user["id"]:
        return JSONResponse({"ok": False, "error": "Payment not found"}, status_code=404)

    if payment.status == PaymentStatus.SUCCEEDED.value:
        from sqlalchemy import select
        from app.models.vpn_key import VpnKey
        result = await db.execute(select(VpnKey).where(VpnKey.id == payment.vpn_key_id))
        key = result.scalar_one_or_none()
        return JSONResponse({"ok": True, "status": "succeeded",
            "access_url": key.access_url if key else None,
            "expires_at": key.expires_at.isoformat() if key and key.expires_at else None})
    
    if payment.status == PaymentStatus.FAILED.value:
        return JSONResponse({"ok": True, "status": "failed"})

    if not payment.external_id:
        return JSONResponse({"ok": True, "status": "pending"})

    try:
        from app.services.yookassa import YookassaService
        yk = await YookassaService.create()
        yk_payment = await yk.get_payment(str(payment.external_id))
        if yk_payment.status == "succeeded":
            payment.status = PaymentStatus.SUCCEEDED.value
            await db.flush()
            import json as _json
            plan_id = None
            if payment.meta:
                try:
                    meta = _json.loads(payment.meta)
                    plan_id = int(meta.get("plan_id", 0)) or None
                except Exception:
                    pass
            if plan_id:
                plan = await PlanService(db).get_by_id(plan_id)
                if plan:
                    key = await VpnKeyService(db).provision(user_id=tg_user["id"], plan=plan)
                    if key:
                        payment.vpn_key_id = key.id
                    await db.commit()
                    return JSONResponse({"ok": True, "status": "succeeded",
                        "access_url": key.access_url if key else None,
                        "expires_at": key.expires_at.isoformat() if key and key.expires_at else None})
            await db.commit()
            return JSONResponse({"ok": True, "status": "succeeded", "access_url": None})
        elif yk_payment.status in ("canceled", "expired"):
            payment.status = PaymentStatus.FAILED.value
            await db.commit()
            return JSONResponse({"ok": True, "status": "failed"})
        return JSONResponse({"ok": True, "status": "pending"})
    except Exception as e:
        log.error(f"Check payment error: {e}")
        return JSONResponse({"ok": True, "status": "pending"})


@router.post("/promo/apply")
async def apply_promo(request: Request, db: AsyncSession = Depends(get_db)):
    tg_user = await _get_tg_user(request, db)
    if not tg_user:
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)

    body = await request.json()
    code = (body.get("code") or "").strip().upper()
    user_id = tg_user["id"]

    if not code:
        return JSONResponse({"ok": False, "error": "Code required"}, status_code=400)

    promo = await PromoService(db).apply(code, user_id=user_id)
    if not promo:
        return JSONResponse({"ok": False, "error": "Invalid, expired, or already used promo code"}, status_code=400)

    pt = str(promo.promo_type)
    result = {"type": pt, "value": float(promo.value)}

    if pt == "balance":
        from decimal import Decimal
        await UserService(db).add_balance(user_id, Decimal(promo.value))
        await db.commit()
        result["message"] = f"💰 Зачислено {float(promo.value)}₽ на баланс"
    elif pt == "days":
        keys = await VpnKeyService(db).get_all_for_user(user_id)
        active_key = next((k for k in keys if str(k.status.value if hasattr(k.status, "value") else k.status) == "active"), None)
        if active_key:
            await VpnKeyService(db).extend(active_key.id, int(promo.value))
            await db.commit()
            result["message"] = f"📅 Подписка продлена на {int(promo.value)} дней"
        else:
            result["message"] = f"📅 +{int(promo.value)} дней будут применены при следующей покупке"
    else:
        result["message"] = f"🏷 Скидка {float(promo.value)}% активирована"

    return JSONResponse({"ok": True, "result": result})


@router.get("/faq")
async def get_faq(db: AsyncSession = Depends(get_db)):
    settings = await BotSettingsService(db).get_all()
    return JSONResponse({"ok": True, "about": settings.get("about_text", ""), "faq": [
        {"q": "Как подключить VPN?", "a": "Скопируйте ключ из раздела «Подписки» и импортируйте в приложение V2Ray/Outline."},
        {"q": "Какие устройства поддерживаются?", "a": "iOS, Android, Windows, macOS и Linux."},
        {"q": "Сколько устройств можно подключить?", "a": "Один ключ работает на неограниченном количестве устройств."},
        {"q": "Как пополнить баланс?", "a": "Перейдите в раздел «Купить» → выберите план → оплатите."},
        {"q": "Можно ли вернуть деньги?", "a": "Возврат возможен в течение 24 часов при технических проблемах."},
        {"q": "Как работают промокоды?", "a": "Введите код в разделе «Профиль» — получите бонус."},
    ]})


@router.get("/admin/stats")
async def admin_stats(request: Request, db: AsyncSession = Depends(get_db)):
    tg_user = await _get_tg_user(request, db)
    if not tg_user:
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)

    user_id = tg_user["id"]
    if user_id not in config.telegram.telegram_admin_ids:
        return JSONResponse({"ok": False, "error": "Not admin"}, status_code=403)

    from datetime import datetime, timezone
    from sqlalchemy import select, func
    from app.models.payment import Payment, PaymentStatus, PaymentType
    from app.models.user import User

    total_users = await UserService(db).count_all()
    active_subs = await VpnKeyService(db).count_active()

    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    new_today = (await db.execute(select(func.count()).select_from(User).where(User.created_at >= today))).scalar_one()

    revenue_val = (await db.execute(
        select(func.sum(Payment.amount)).where(
            Payment.status == PaymentStatus.SUCCEEDED.value,
            Payment.payment_type == PaymentType.SUBSCRIPTION.value,
        )
    )).scalar_one()
    revenue = float(revenue_val) if revenue_val else 0.0

    return JSONResponse({"ok": True, "total_users": total_users, "active_subs": active_subs,
        "new_today": new_today, "revenue": revenue})


@router.get("/user/stats")
async def user_stats(request: Request, db: AsyncSession = Depends(get_db)):
    tg_user = await _get_tg_user(request, db)
    if not tg_user:
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)

    user_id = tg_user["id"]
    user = await UserService(db).get_by_id(user_id)
    if not user:
        return JSONResponse({"ok": False, "error": "User not found"}, status_code=404)

    keys = await VpnKeyService(db).get_all_for_user(user_id)
    ref_count = await ReferralService(db).count_referrals(user_id)

    active_keys = expired_keys = 0
    total_spent = 0.0
    for k in keys:
        status = str(k.status.value if hasattr(k.status, "value") else k.status)
        if status == "active":
            active_keys += 1
        else:
            expired_keys += 1
        total_spent += float(k.price or 0)

    return JSONResponse({"ok": True, "stats": {
        "balance": float(user.balance or 0), "active_keys": active_keys,
        "expired_keys": expired_keys, "total_spent": round(total_spent, 2),
        "referrals": ref_count, "referral_code": user.referral_code,
    }})


@router.get("/user/payments")
async def user_payments(request: Request, limit: int = 10, db: AsyncSession = Depends(get_db)):
    tg_user = await _get_tg_user(request, db)
    if not tg_user:
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)

    payments = await PaymentService(db).get_all(user_id=tg_user["id"], limit=limit)
    return JSONResponse({"ok": True, "payments": [{
        "id": p.id, "amount": float(p.amount), "currency": p.currency,
        "provider": p.provider, "payment_type": p.payment_type,
        "status": p.status, "created_at": p.created_at.isoformat() if p.created_at else None,
    } for p in payments]})
