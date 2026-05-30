"""Subscriptions (VPN Keys) routes."""

from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.services.plan import PlanService
from app.services.vpn_key import VpnKeyService
from app.services.telegram_notify import TelegramNotifyService
from app.utils.html_utils import escape_html, html_code

from .shared import _require_permission, _toast, _base_ctx, templates

router = APIRouter()


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def subscriptions_page(request: Request, db: AsyncSession = Depends(get_db)):
    admin_info = _require_permission(request, "subscriptions")
    ctx = await _base_ctx(request, db, "subscriptions", admin_info)
    from app.models.vpn_key import VpnKey, VpnKeyStatus
    from sqlalchemy.orm import undefer

    result = await db.execute(
        select(VpnKey)
        .options(
            undefer(VpnKey.download),
            undefer(VpnKey.upload),
        )
        .order_by(
            case(
                (VpnKey.status == VpnKeyStatus.ACTIVE.value, 0),
                (VpnKey.status == VpnKeyStatus.EXPIRED.value, 1),
                else_=2,
            ),
            VpnKey.expires_at.asc(),  # type: ignore[comparison-overload]
            VpnKey.id.desc(),
        )
    )
    subscriptions = list(result.scalars().all())
    await VpnKeyService(db).refresh_traffic_for_keys(subscriptions)
    await db.commit()
    ctx["subscriptions"] = subscriptions
    ctx["plans"] = await PlanService(db).get_all(only_active=True)
    return templates.TemplateResponse(request, "subscriptions.html", ctx)


@router.post("/create", response_class=HTMLResponse)
async def create_subscription(
    request: Request,
    user_id: int = Form(...),
    plan_id: int = Form(...),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "subscriptions.write")
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
            f"🔑 <b>Ваш VPN-ключ готов!</b>\n\nПлан: <b>{escape_html(plan.name)}</b>\n"
            f"📅 Действует: <b>{plan.duration_days} дней</b>\n\n"
            f"{html_code(key.access_url)}",
        )
    resp = Response(status_code=200)
    _toast(resp, f"Подписка «{plan.name}» выдана" if key else "Ошибка создания ключа")
    return resp


@router.post("/create-days", response_class=HTMLResponse)
async def create_subscription_days(
    request: Request,
    user_id: int = Form(...),
    days: int = Form(...),
    name: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "subscriptions.write")
    if days < 1 or days > 3650:
        resp = Response(status_code=400)
        _toast(resp, "Количество дней должно быть от 1 до 3650", "error")
        return resp
    key_name = name.strip() if name else f"Подписка — {days} дн."
    key = await VpnKeyService(db).provision_days(
        user_id=user_id, days=days, name=key_name
    )
    await db.commit()
    if key:
        exp_str = (
            key.expires_at.strftime("%d.%m.%Y") if key.expires_at is not None else "—"
        )
        await TelegramNotifyService().send_message(
            user_id,
            f"🔑 <b>Ваш VPN-ключ готов!</b>\n\nДлительность: <b>{days} дней</b>\n"
            f"📅 Действует до: <b>{exp_str}</b>\n\n{html_code(key.access_url)}\n\n"
            "<i> 🔥 Приятного пользования! </i>",
        )
    resp = Response(status_code=200)
    _toast(resp, "Подписка выдана" if key else "Ошибка создания ключа")
    return resp


@router.post("/{key_id}/extend", response_class=HTMLResponse)
async def extend_subscription(
    key_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "subscriptions.write")
    from app.models.vpn_key import VpnKey
    from sqlalchemy import select

    result = await db.execute(select(VpnKey).where(VpnKey.id == key_id))
    key = result.scalar_one_or_none()
    if not key:
        resp = Response(status_code=404)
        _toast(resp, "Ключ не найден", "error")
        return resp
    days = 30
    if key.plan:
        days = key.plan.duration_days
    key = await VpnKeyService(db).extend(key_id, days)
    await db.commit()
    if key:
        exp_str = key.expires_at.strftime("%d.%m.%Y") if key.expires_at else "—"
        await TelegramNotifyService().send_message(
            key.user_id,
            f"🔄 <b>Подписка продлена!</b>\n📅 Новая дата: <b>{exp_str}</b>\n➕ +{days} дней",
        )
    resp = Response(status_code=200)
    _toast(resp, "Подписка продлена" if key else "Ошибка продления")
    return resp


@router.post("/{key_id}/cancel", response_class=HTMLResponse)
async def cancel_subscription(
    key_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "subscriptions.write")
    from app.models.vpn_key import VpnKey, VpnKeyStatus
    from sqlalchemy import select

    result = await db.execute(select(VpnKey).where(VpnKey.id == key_id))
    key = result.scalar_one_or_none()
    if not key:
        resp = Response(status_code=404)
        _toast(resp, "Ключ не найден", "error")
        return resp
    if key:
        key.status = VpnKeyStatus.EXPIRED.value
        await db.commit()
        await TelegramNotifyService().send_message(
            key.user_id,
            "⚠️ <b>Подписка остановлена администратором.</b>\n\n"
            "Если это произошло по ошибке, напишите в поддержку.",
        )
    resp = Response(status_code=200)
    _toast(resp, "Подписка отменена")
    return resp


@router.post("/expire-outdated", response_class=HTMLResponse)
async def expire_outdated_subscriptions(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "subscriptions.write")
    count = await VpnKeyService(db).expire_outdated()
    await db.commit()
    resp = Response(status_code=200)
    _toast(resp, f"Истекших подписок обработано: {count}")
    return resp
