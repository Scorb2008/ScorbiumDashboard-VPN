"""User management routes."""

from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.schemas.user import UserRead
from app.services.bot_settings import BotSettingsService
from app.services.plan import PlanService
from app.services.telegram_notify import TelegramNotifyService
from app.services.user import UserService
from app.services.vpn_key import VpnKeyService

from .shared import _base_ctx, _require_permission, _toast, _to_detail, templates

router = APIRouter()


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def users_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "users.read")
    ctx = await _base_ctx(request, db, "users")
    from app.services.user import UserService

    raw = await UserService(db).get_all(limit=200)
    ctx["users"] = [_to_detail(u) for u in raw]
    ctx["plans"] = await PlanService(db).get_all(only_active=True)
    return templates.TemplateResponse(request, "users.html", ctx)


@router.get("/search", response_class=HTMLResponse)
async def users_search(
    request: Request, q: str = "", db: AsyncSession = Depends(get_db)
):
    _require_permission(request, "users.read")
    from app.services.user import UserService

    raw = await UserService(db).get_all(limit=200)
    q = q.lower()
    filtered = [
        u
        for u in raw
        if q in (u.full_name or "").lower() or q in (u.username or "").lower()
    ]
    return templates.TemplateResponse(
        request,
        "partials/users_rows.html",
        {"request": request, "users": [_to_detail(u) for u in filtered]},
    )


@router.get("/{user_id}", response_class=HTMLResponse)
async def user_detail_page(
    user_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    from sqlalchemy import select
    from app.models.payment import Payment
    from app.models.vpn_key import VpnKey

    admin_info = _require_permission(request, "users.read")
    ctx = await _base_ctx(request, db, "users", admin_info)
    from app.services.user import UserService

    user = await UserService(db).get_by_id(user_id)
    if not user:
        resp = Response(status_code=404)
        _toast(resp, "Пользователь не найден", "error")
        return resp
    keys_result = await db.execute(
        select(VpnKey).where(VpnKey.user_id == user_id).order_by(VpnKey.id.desc())
    )
    pays_result = await db.execute(
        select(Payment)
        .where(Payment.user_id == user_id)
        .order_by(Payment.created_at.desc())
    )
    ctx["user"] = UserRead.model_validate(user)
    ctx["subscriptions"] = list(keys_result.scalars().all())
    ctx["payments"] = list(pays_result.scalars().all())
    ctx["plans"] = await PlanService(db).get_all(only_active=True)
    return templates.TemplateResponse(request, "user_detail.html", ctx)


@router.post("/{user_id}/deduct-balance", response_class=HTMLResponse)
async def deduct_balance(
    user_id: int,
    request: Request,
    amount: Decimal = Form(...),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "users.write")
    if amount <= 0:
        resp = Response(status_code=400)
        _toast(resp, "Сумма должна быть больше нуля", "error")
        return resp
    from app.services.user import UserService

    user = await UserService(db).deduct_balance(user_id, amount)
    if not user:
        resp = Response(status_code=400)
        _toast(resp, "Недостаточно средств на балансе", "error")
        return resp
    await db.commit()
    notify = TelegramNotifyService()
    await notify.send_message(user_id, f"💸 С вашего баланса списано <b>{amount} ₽</b>")
    resp = Response(status_code=200)
    _toast(resp, f"Снято {amount} ₽ с баланса")
    return resp


@router.post("/{user_id}/ban", response_class=HTMLResponse)
async def ban_user_view(
    user_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    from app.core.config import config

    _require_permission(request, "users.write")
    if user_id in config.telegram.telegram_admin_ids:
        resp = Response(status_code=400)
        _toast(resp, "Нельзя забанить администратора", "error")
        return resp
    from app.services.user import UserService

    user = await UserService(db).ban(user_id)
    if not user:
        resp = Response(status_code=404)
        _toast(resp, "Пользователь не найден", "error")
        return resp
    await db.commit()
    ban_msg = (
        await BotSettingsService(db).get("ban_message")
        or "🚫 Ваш аккаунт заблокирован."
    )
    await TelegramNotifyService().send_message(user_id, ban_msg)
    resp = templates.TemplateResponse(
        request,
        "partials/users_rows.html",
        {"request": request, "users": [_to_detail(user)]},
    )
    _toast(resp, "Пользователь заблокирован")
    return resp


@router.post("/{user_id}/unban", response_class=HTMLResponse)
async def unban_user_view(
    user_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    from app.services.user import UserService

    _require_permission(request, "users.write")
    user = await UserService(db).unban(user_id)
    if not user:
        resp = Response(status_code=404)
        _toast(resp, "Пользователь не найден", "error")
        return resp
    await db.commit()
    unban_msg = (
        await BotSettingsService(db).get("unban_message")
        or "✅ Ваш аккаунт разблокирован. Добро пожаловать обратно!"
    )
    await TelegramNotifyService().send_message(user_id, unban_msg)
    resp = templates.TemplateResponse(
        request,
        "partials/users_rows.html",
        {"request": request, "users": [_to_detail(user)]},
    )
    _toast(resp, "Пользователь разблокирован")
    return resp


@router.post("/{user_id}/gift-subscription", response_class=HTMLResponse)
async def gift_subscription(
    user_id: int,
    request: Request,
    plan_id: int = Form(...),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "users.write")
    plan = await PlanService(db).get_by_id(plan_id)
    if not plan:
        resp = Response(status_code=404)
        _toast(resp, "Тариф не найден", "error")
        return resp
    key = await VpnKeyService(db).provision(user_id=user_id, plan=plan)
    await db.commit()
    if key:
        await TelegramNotifyService().send_message(
            user_id,
            f"🎁 <b>Вам подарена подписка!</b>\n\nПлан: <b>{plan.name}</b> ({plan.duration_days} дней)\n\n"
            f"🔑 <b>Ссылка:</b>\n<code>{key.access_url}</code>\n\n"
            "<i> 🔥 Приятного пользования! </i>",
        )
    resp = Response(status_code=200)
    _toast(
        resp,
        f"Подписка «{plan.name}» подарена"
        if key
        else "Ошибка создания ключа в Marzban",
        "success" if key else "error",
    )
    return resp


@router.post("/{user_id}/gift-days", response_class=HTMLResponse)
async def gift_days(
    user_id: int,
    request: Request,
    days: int = Form(...),
    name: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "users.write")
    if days < 1 or days > 3650:
        resp = Response(status_code=400)
        _toast(resp, "Количество дней должно быть от 1 до 3650", "error")
        return resp
    key_name = name.strip() if name else f"Подарок — {days} дн."
    key = await VpnKeyService(db).provision_days(
        user_id=user_id, days=days, name=key_name
    )
    await db.commit()
    if key:
        exp_str = key.expires_at.strftime("%d.%m.%Y") if key.expires_at else "—"
        await TelegramNotifyService().send_message(
            user_id,
            f"🎁 <b>Вам подарена подписка!</b>\n\n"
            f"Длительность: <b>{days} дней</b>\n"
            f"Действует до: <b>{exp_str}</b>\n\n"
            f"🔑 <b>Ссылка:</b>\n<code>{key.access_url}</code>\n\n"
            "<i> 🔥 Приятного пользования! </i>",
        )
    resp = Response(status_code=200)
    _toast(
        resp,
        f"Подписка на {days} дней подарена"
        if key
        else "Ошибка создания ключа в Marzban",
        "success" if key else "error",
    )
    return resp


@router.post("/{user_id}/add-balance", response_class=HTMLResponse)
async def add_balance(
    user_id: int,
    request: Request,
    amount: Decimal = Form(...),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "users.write")
    if amount <= 0:
        resp = Response(status_code=400)
        _toast(resp, "Сумма должна быть больше нуля", "error")
        return resp
    await UserService(db).add_balance(user_id, amount)
    notify = TelegramNotifyService()
    await notify.send_message(user_id, f"💰 На ваш баланс зачислено <b>{amount} ₽</b>")
    resp = Response(status_code=200)
    _toast(resp, f"Баланс пополнен на {amount} ₽")
    return resp


@router.post("/bulk")
async def bulk_users_action(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "users.write")
    body = await request.json()
    action = body.get("action", "")
    user_ids = body.get("user_ids", [])

    if not user_ids or not isinstance(user_ids, list):
        return JSONResponse(
            {"ok": False, "message": "Нет выбранных пользователей"}, status_code=400
        )

    from app.core.config import config

    if action == "ban":
        done = 0
        for uid in user_ids:
            if uid in config.telegram.telegram_admin_ids:
                continue
            user = await UserService(db).ban(uid)
            if user:
                done += 1
        await db.commit()
        return JSONResponse({"ok": True, "message": f"Забанено: {done}"})
    elif action == "unban":
        done = 0
        for uid in user_ids:
            user = await UserService(db).unban(uid)
            if user:
                done += 1
        await db.commit()
        return JSONResponse({"ok": True, "message": f"Разбанено: {done}"})
    else:
        return JSONResponse(
            {"ok": False, "message": "Неизвестное действие"}, status_code=400
        )


@router.post("/bulk-balance")
async def bulk_balance_action(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "users.write")
    body = await request.json()
    user_ids = body.get("user_ids", [])
    amount = body.get("amount")

    if not user_ids or not amount:
        return JSONResponse({"ok": False, "message": "Нет данных"}, status_code=400)

    try:
        amount = Decimal(str(amount))
        if amount <= 0:
            raise ValueError
    except Exception:
        return JSONResponse({"ok": False, "message": "Неверная сумма"}, status_code=400)

    done = 0
    for uid in user_ids:
        user = await UserService(db).add_balance(uid, amount)
        if user:
            done += 1
            await TelegramNotifyService().send_message(
                uid, f"💰 На ваш баланс зачислено <b>{amount} ₽</b>"
            )
    await db.commit()
    return JSONResponse(
        {"ok": True, "message": f"Пополнено {done} пользователей на {amount} ₽"}
    )


@router.post("/bulk-gift")
async def bulk_gift_action(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "users.write")
    body = await request.json()
    user_ids = body.get("user_ids", [])
    plan_id = body.get("plan_id")

    if not user_ids or not plan_id:
        return JSONResponse({"ok": False, "message": "Нет данных"}, status_code=400)

    plan = await PlanService(db).get_by_id(int(plan_id))
    if not plan:
        return JSONResponse(
            {"ok": False, "message": "Тариф не найден"}, status_code=404
        )

    done = 0
    for uid in user_ids:
        key = await VpnKeyService(db).provision(user_id=uid, plan=plan)
        if key:
            done += 1
            await TelegramNotifyService().send_message(
                uid,
                f"🎁 <b>Вам подарена подписка!</b>\n\nПлан: <b>{plan.name}</b> ({plan.duration_days} дней)\n\n"
                f"🔑 <b>Ссылка:</b>\n<code>{key.access_url}</code>\n\n"
                "<i> 🔥 Приятного пользования! </i>",
            )
    await db.commit()
    return JSONResponse(
        {"ok": True, "message": f"🎁 Подарено {done} подписок «{plan.name}»"}
    )
