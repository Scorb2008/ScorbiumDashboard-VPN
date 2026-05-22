"""VPN keys management routes (revoke, extend, delete, sync)."""

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.api.dependencies import get_db
from app.models.vpn_key import VpnKey
from app.services.vpn_key import VpnKeyService
from app.services.telegram_notify import TelegramNotifyService
from app.services.pasarguard.pasarguard import get_vpn_panel

from .shared import _require_permission, _toast, _base_ctx, templates

router = APIRouter()


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def vpn_page(request: Request, db: AsyncSession = Depends(get_db)):
    admin_info = _require_permission(request, "vpn")
    ctx = await _base_ctx(request, db, "vpn", admin_info)
    result = await db.execute(
        select(VpnKey).order_by(VpnKey.created_at.desc()).limit(200)
    )
    ctx["keys"] = list(result.scalars().all())
    return templates.TemplateResponse("vpn.html", ctx)


@router.post("/{key_id}/revoke", response_class=HTMLResponse)
async def revoke_key(
    key_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "vpn.write")
    try:
        await get_vpn_panel().revoke_user_subscription(str(key_id))
        resp = Response(status_code=200)
        _toast(resp, "Подписка отозвана в панели VPN")
    except Exception as e:
        resp = Response(status_code=400)
        _toast(resp, f"Ошибка: {str(e)}", "error")
    return resp


@router.post("/{key_id}/extend", response_class=HTMLResponse)
async def extend_key(
    key_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "vpn.write")
    result = await db.execute(select(VpnKey).where(VpnKey.id == key_id))
    key = result.scalar_one_or_none()
    if not key:
        resp = Response(status_code=404)
        _toast(resp, "Ключ не найден", "error")
        return resp
    days = 30
    key = await VpnKeyService(db).extend(key_id, days)
    await db.commit()
    if key:
        exp_str = key.expires_at.strftime("%d.%m.%Y") if key.expires_at else "—"
        await TelegramNotifyService().send_message(
            key.user_id,
            f"🔄 <b>Подписка продлена!</b>\n📅 Новая дата: <b>{exp_str}</b>",
        )
    resp = Response(status_code=200)
    _toast(resp, "Срок продлен" if key else "Ошибка продления")
    return resp


@router.post("/{key_id}/delete", response_class=HTMLResponse)
async def delete_key(
    key_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "vpn.write")
    result = await db.execute(select(VpnKey).where(VpnKey.id == key_id))
    key = result.scalar_one_or_none()
    if not key:
        resp = Response(status_code=404)
        _toast(resp, "Ключ не найден", "error")
        return resp
    await db.delete(key)
    await db.commit()
    resp = HTMLResponse("")
    _toast(resp, "Ключ удалён")
    return resp


@router.post("/sync", response_class=HTMLResponse)
async def sync_vpn(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "vpn.write")
    try:
        from app.tasks.vpn_tasks import sync_keys_from_marzban

        await sync_keys_from_marzban()
        resp = Response(status_code=200)
        _toast(resp, "Синхронизация завершена")
    except Exception as e:
        resp = Response(status_code=400)
        _toast(resp, f"Ошибка синхронизации: {str(e)}", "error")
    return resp
