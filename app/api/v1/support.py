from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.api.dependencies import get_db, get_current_admin
from app.models.support import TicketStatus
from app.schemas.support import (
    TicketCreate,
    TicketRead,
    TicketReply,
    TicketStatusUpdate,
    TicketPriorityUpdate,
)
from app.services.support import SupportService
from app.services.telegram_notify import TelegramNotifyService

router = APIRouter()


@router.get("/", response_model=list[TicketRead], summary="List support tickets")
async def list_tickets(
    status: Optional[TicketStatus] = None,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> list[TicketRead]:
    return await SupportService(db).get_all(status=status, limit=limit, offset=offset)


@router.get("/{ticket_id}", response_model=TicketRead, summary="Get ticket")
async def get_ticket(
    ticket_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> TicketRead:
    ticket = await SupportService(db).get_by_id(ticket_id)
    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found"
        )
    return ticket


@router.post(
    "/",
    response_model=TicketRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create ticket",
)
async def create_ticket(
    data: TicketCreate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> TicketRead:
    svc = SupportService(db)
    ticket = await svc.create_ticket(
        user_id=data.user_id,
        subject=data.subject,
        first_message=data.message,
        priority=data.priority,
    )
    return await svc.get_by_id(ticket.id)


@router.post(
    "/{ticket_id}/reply", response_model=TicketRead, summary="Reply to ticket (admin)"
)
async def reply_ticket(
    ticket_id: int,
    body: TicketReply,
    db: AsyncSession = Depends(get_db),
    admin: str = Depends(get_current_admin),
) -> TicketRead:
    svc = SupportService(db)
    ticket = await svc.get_by_id(ticket_id)
    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found"
        )

    await svc.add_message(
        ticket_id=ticket_id, sender_id=0, text=body.text, is_admin=True
    )

    if body.notify_user:
        notify = TelegramNotifyService()
        await notify.send_message(
            chat_id=ticket.user_id,
            text=f"💬 <b>Ответ по тикету #{ticket_id}</b>\n\n{body.text}",
        )

    return await svc.get_by_id(ticket_id)


@router.patch(
    "/{ticket_id}/status", response_model=TicketRead, summary="Update ticket status"
)
async def update_ticket_status(
    ticket_id: int,
    body: TicketStatusUpdate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> TicketRead:
    svc = SupportService(db)
    ticket = await svc.set_status(ticket_id, body.status)
    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found"
        )
    return await svc.get_by_id(ticket_id)


@router.patch(
    "/{ticket_id}/priority", response_model=TicketRead, summary="Update ticket priority"
)
async def update_ticket_priority(
    ticket_id: int,
    body: TicketPriorityUpdate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> TicketRead:
    svc = SupportService(db)
    ticket = await svc.set_priority(ticket_id, body.priority)
    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found"
        )
    return await svc.get_by_id(ticket_id)
