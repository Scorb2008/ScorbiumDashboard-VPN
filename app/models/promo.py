import enum
from sqlalchemy import Boolean, Column, Integer, Numeric, String, ForeignKey
from sqlalchemy.orm import relationship

from app.models.base import Base


class PromoType(str, enum.Enum):
    DISCOUNT = "discount"
    BALANCE = "balance"
    DAYS = "days"


class PromoCode(Base):
    __tablename__ = "promo_codes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(64), nullable=False, unique=True, index=True)
    promo_type = Column(String(32), nullable=False)
    value = Column(Numeric(10, 2), nullable=False)
    plan_id = Column(
        Integer, ForeignKey("plans.id", ondelete="SET NULL"), nullable=True
    )
    max_uses = Column(Integer, default=0, nullable=False)
    current_uses = Column(Integer, default=0, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    plan = relationship("Plan", lazy="selectin")

    def __repr__(self) -> str:
        return f"<PromoCode code={self.code} type={self.promo_type} value={self.value}>"
