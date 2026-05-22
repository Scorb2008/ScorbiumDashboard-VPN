"""Support tickets routes."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.api.dependencies import get_db
from app.models.support import (
    SupportTicket,
    TicketStatus,
    TicketPriority,
    TicketMessage,
)
from app.services.telegram_notify import TelegramNotifyService

from .shared import _require_permission, _toast, _base_ctx, _render_messages, templates

router = APIRouter()


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def support_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    status: str | None = None,
):
    admin_info = _require_permission(request, "support")
    ctx = await _base_ctx(request, db, "support", admin_info)
    query = select(SupportTicket).order_by(SupportTicket.created_at.desc()).limit(100)
    if status in ("open", "in_progress", "closed"):
        query = query.where(SupportTicket.status == status)
    result = await db.execute(query)
    ctx["tickets"] = list(result.scalars().all())
    ctx["current_status"] = status or ""
    return templates.TemplateResponse("support.html", ctx)


@router.get("/{ticket_id}", response_class=HTMLResponse)
async def ticket_detail(
    ticket_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    status: str | None = None,
):
    admin_info = _require_permission(request, "support")
    ctx = await _base_ctx(request, db, "support", admin_info)
    query = select(SupportTicket).order_by(SupportTicket.created_at.desc()).limit(100)
    if status in ("open", "in_progress", "closed"):
        query = query.where(SupportTicket.status == status)
        ctx["current_status"] = status
    else:
        ctx["current_status"] = ""
    result = await db.execute(query)
    ctx["tickets"] = list(result.scalars().all())

    result = await db.execute(
        select(SupportTicket).where(SupportTicket.id == ticket_id)
    )
    ticket = result.scalar_one_or_none()
    if not ticket:
        return templates.TemplateResponse("support.html", {**ctx, "ticket": None})
    ctx["ticket"] = ticket
    ctx["selected_id"] = ticket.id
    return templates.TemplateResponse("support.html", ctx)


@router.post("/{ticket_id}/reply", response_class=HTMLResponse)
async def reply_ticket(
    ticket_id: int,
    request: Request,
    text: str = Form(...),
    notify_user: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "support.write")
    cleaned_text = text.strip()
    if not cleaned_text:
        resp = Response(status_code=400)
        _toast(resp, "Сообщение не может быть пустым", "error")
        return resp

    result = await db.execute(
        select(SupportTicket).where(SupportTicket.id == ticket_id)
    )
    ticket = result.scalar_one_or_none()
    if not ticket:
        resp = Response(status_code=404)
        _toast(resp, "Тикет не найден", "error")
        return resp

    dedupe_cutoff = datetime.now(timezone.utc) - timedelta(seconds=15)
    duplicate_query = await db.execute(
        select(TicketMessage)
        .where(
            TicketMessage.ticket_id == ticket_id,
            TicketMessage.is_admin.is_(True),
            TicketMessage.text == cleaned_text,
            TicketMessage.created_at >= dedupe_cutoff,
        )
        .order_by(TicketMessage.created_at.desc())
        .limit(1)
    )
    if duplicate_query.scalar_one_or_none():
        resp = HTMLResponse(_render_messages(ticket))
        _toast(resp, "Сообщение уже отправлено", "info")
        return resp

    msg = TicketMessage(
        ticket_id=ticket_id, sender_id=0, text=cleaned_text, is_admin=True
    )
    db.add(msg)
    if ticket.status == TicketStatus.CLOSED.value:
        ticket.status = TicketStatus.IN_PROGRESS.value
    await db.commit()
    if notify_user:
        await TelegramNotifyService().send_message(
            ticket.user_id,
            f"💬 <b>Ответ по тикету #{ticket.id}</b>\n\n{cleaned_text}",
        )
    resp = HTMLResponse(_render_messages(ticket))
    _toast(resp, "Ответ отправлен")
    return resp


@router.post("/{ticket_id}/close", response_class=HTMLResponse)
async def close_ticket(
    ticket_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "support.write")
    result = await db.execute(
        select(SupportTicket).where(SupportTicket.id == ticket_id)
    )
    ticket = result.scalar_one_or_none()
    if not ticket:
        resp = Response(status_code=404)
        _toast(resp, "Тикет не найден", "error")
        return resp
    ticket.status = TicketStatus.CLOSED.value
    await db.commit()
    resp = Response(status_code=200)
    _toast(resp, "Тикет закрыт")
    return resp


@router.patch("/{ticket_id}/status")
async def update_ticket_status(
    ticket_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "support.write")
    form = await request.form()
    new_status = str(form.get("status") or "").strip()
    result = await db.execute(
        select(SupportTicket).where(SupportTicket.id == ticket_id)
    )
    ticket = result.scalar_one_or_none()
    if not ticket:
        return JSONResponse(
            {"ok": False, "message": "Тикет не найден"}, status_code=404
        )
    allowed_statuses = {item.value for item in TicketStatus}
    if new_status in allowed_statuses:
        ticket.status = new_status
        await db.commit()
        return JSONResponse({"ok": True})
    return JSONResponse({"ok": False, "message": "Неверный статус"}, status_code=400)


@router.patch("/{ticket_id}/priority")
async def update_ticket_priority(
    ticket_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "support.write")
    form = await request.form()
    new_priority = str(form.get("priority") or "").strip()
    result = await db.execute(
        select(SupportTicket).where(SupportTicket.id == ticket_id)
    )
    ticket = result.scalar_one_or_none()
    if not ticket:
        return JSONResponse(
            {"ok": False, "message": "Тикет не найден"}, status_code=404
        )
    allowed_priorities = {item.value for item in TicketPriority}
    if new_priority in allowed_priorities:
        ticket.priority = new_priority
        await db.commit()
        return JSONResponse({"ok": True})
    return JSONResponse({"ok": False, "message": "Неверный приоритет"}, status_code=400)
