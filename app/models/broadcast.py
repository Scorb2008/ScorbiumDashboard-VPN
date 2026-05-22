import enum
from sqlalchemy import Column, Integer, Text, String
from app.models.base import Base


class BroadcastStatus(str, enum.Enum):
    DRAFT = "draft"
    SENDING = "sending"
    DONE = "done"
    FAILED = "failed"


class Broadcast(Base):
    __tablename__ = "broadcasts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(256), nullable=False)
    text = Column(Text, nullable=False)
    parse_mode = Column(String(16), default="HTML", nullable=False)
    target = Column(String(32), default="all", nullable=False)  # all | active | expired
    status = Column(String(32), default=BroadcastStatus.DRAFT.value, nullable=False)
    sent_count = Column(Integer, default=0, nullable=False)
    failed_count = Column(Integer, default=0, nullable=False)

    def __repr__(self) -> str:
        return f"<Broadcast id={self.id} title={self.title} status={self.status}>"
