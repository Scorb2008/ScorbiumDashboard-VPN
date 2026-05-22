from typing import Optional
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.support import (
    SupportTicket,
    TicketMessage,
    TicketStatus,
    TicketPriority,
)


class SupportService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_all(
        self,
        status: Optional[TicketStatus] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SupportTicket]:
        q = (
            select(SupportTicket)
            .options(selectinload(SupportTicket.messages))
            .order_by(SupportTicket.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if status:
            q = q.where(SupportTicket.status == status.value)
        result = await self.session.execute(q)
        return list(result.scalars().all())

    async def get_by_id(self, ticket_id: int) -> Optional[SupportTicket]:
        result = await self.session.execute(
            select(SupportTicket)
            .options(selectinload(SupportTicket.messages))
            .where(SupportTicket.id == ticket_id)
        )
        return result.scalar_one_or_none()

    async def get_for_user(self, user_id: int) -> list[SupportTicket]:
        result = await self.session.execute(
            select(SupportTicket)
            .options(selectinload(SupportTicket.messages))
            .where(SupportTicket.user_id == user_id)
            .order_by(SupportTicket.created_at.desc())
        )
        return list(result.scalars().all())

    async def create_ticket(
        self,
        user_id: int,
        subject: str,
        first_message: str,
        priority: TicketPriority = TicketPriority.MEDIUM,
    ) -> SupportTicket:
        ticket = SupportTicket(
            user_id=user_id, subject=subject, priority=priority.value
        )
        self.session.add(ticket)
        await self.session.flush()

        msg = TicketMessage(
            ticket_id=ticket.id, sender_id=user_id, is_admin=False, text=first_message
        )
        self.session.add(msg)
        await self.session.flush()
        return ticket

    async def add_message(
        self,
        ticket_id: int,
        sender_id: int,
        text: str,
        is_admin: bool = False,
    ) -> Optional[TicketMessage]:
        ticket = await self.get_by_id(ticket_id)
        if not ticket:
            return None
        msg = TicketMessage(
            ticket_id=ticket_id,
            sender_id=sender_id,
            is_admin=bool(is_admin),
            text=text,
        )
        self.session.add(msg)
        if ticket.status == TicketStatus.CLOSED.value:
            ticket.status = TicketStatus.IN_PROGRESS.value
        await self.session.flush()
        return msg

    async def set_status(
        self, ticket_id: int, status: TicketStatus
    ) -> Optional[SupportTicket]:
        ticket = await self.get_by_id(ticket_id)
        if ticket:
            ticket.status = status.value
            await self.session.flush()
        return ticket

    async def set_priority(
        self, ticket_id: int, priority: TicketPriority
    ) -> Optional[SupportTicket]:
        ticket = await self.get_by_id(ticket_id)
        if ticket:
            ticket.priority = priority.value
            await self.session.flush()
        return ticket

    async def count_open(self) -> int:
        result = await self.session.execute(
            select(func.count()).where(SupportTicket.status == TicketStatus.OPEN.value)
        )
        return result.scalar_one()
