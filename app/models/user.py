from sqlalchemy import BigInteger, Boolean, Column, DateTime, Numeric, String
from sqlalchemy.orm import relationship

from app.models.base import Base


class User(Base):
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True)
    username = Column(String(64), nullable=True)
    full_name = Column(String(256), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_banned = Column(Boolean, default=False, nullable=False)
    balance = Column(Numeric(10, 2), default=0, nullable=False)
    referral_code = Column(String(32), nullable=True, unique=True, index=True)
    language = Column(String(8), nullable=True, default=None)
    autorenew = Column(Boolean, default=False, nullable=False)
    last_seen = Column(DateTime(timezone=True), nullable=True)

    payments = relationship("Payment", back_populates="user", lazy="selectin")
    vpn_keys = relationship("VpnKey", back_populates="user", lazy="selectin")
    tickets = relationship("SupportTicket", back_populates="user", lazy="noload")

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username}>"
