from pydantic import BaseModel, ConfigDict, Field
from app.models.support import TicketStatus, TicketPriority


class TicketMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sender_id: int
    is_admin: int
    text: str


class TicketCreate(BaseModel):
    user_id: int
    subject: str = Field(..., min_length=1, max_length=256)
    message: str = Field(..., min_length=1)
    priority: TicketPriority = TicketPriority.MEDIUM


class TicketReply(BaseModel):
    text: str = Field(..., min_length=1)
    notify_user: bool = True  # отправить сообщение юзеру в Telegram


class TicketStatusUpdate(BaseModel):
    status: TicketStatus


class TicketPriorityUpdate(BaseModel):
    priority: TicketPriority


class TicketRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    subject: str
    status: TicketStatus
    priority: TicketPriority
    messages: list[TicketMessageRead] = []
