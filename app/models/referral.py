import enum
from decimal import Decimal
from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import relationship

from app.models.base import Base


class ReferralBonusType(str, enum.Enum):
    DAYS = "days"
    BALANCE = "balance"
    PERCENT = "percent"


class Referral(Base):
    __tablename__ = "referrals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    referrer_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    referred_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    bonus_type = Column(String(32), nullable=True)
    bonus_value = Column(Numeric(10, 2), nullable=True)
    is_paid = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    referrer = relationship("User", foreign_keys=[referrer_id], lazy="selectin")
    referred = relationship("User", foreign_keys=[referred_id], lazy="selectin")

    def __repr__(self) -> str:
        return f"<Referral referrer={self.referrer_id} referred={self.referred_id}>"
