from sqlalchemy import Boolean, Column, Integer, Numeric, String, Text

from app.models.base import Base


class Plan(Base):
    """Тарифный план — управляется из панели, не из кода."""

    __tablename__ = "plans"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False, unique=True)
    slug = Column(String(64), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    duration_days = Column(Integer, nullable=False)
    price = Column(Numeric(10, 2), nullable=False)
    currency = Column(String(8), default="RUB", nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    sort_order = Column(Integer, default=0, nullable=False)

    def __repr__(self) -> str:
        return f"<Plan slug={self.slug} price={self.price} days={self.duration_days}>"
