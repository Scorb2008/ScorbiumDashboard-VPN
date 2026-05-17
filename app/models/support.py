import enum
from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, Text, String, func
from sqlalchemy.orm import relationship

from app.models.base import Base



class TicketStatus(str, enum.Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    CLOSED = "closed"



class TicketPriority(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"



class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    subject = Column(String(256), nullable=False)
    status = Column(String(32), default=TicketStatus.OPEN.value, nullable=False)
    priority = Column(String(32), default=TicketPriority.MEDIUM.value, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="tickets")
    messages = relationship("TicketMessage", back_populates="ticket", lazy="selectin", order_by="TicketMessage.created_at")

    def __repr__(self) -> str:
        return f"<SupportTicket id={self.id} user_id={self.user_id} status={self.status}>"



class TicketMessage(Base):
    __tablename__ = "ticket_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticket_id = Column(Integer, ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False)
    sender_id = Column(BigInteger, nullable=False)
    is_admin = Column(Boolean, default=False, nullable=False)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    ticket = relationship("SupportTicket", back_populates="messages")
