import enum
from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import relationship

from app.models.base import Base


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class PaymentProvider(str, enum.Enum):
    YOOKASSA = "yookassa"
    YOOKASSA_SBP = "yookassa_sbp"
    CRYPTOBOT = "cryptobot"
    TELEGRAM_STARS = "telegram_stars"
    FREEKASSA = "freekassa"
    AIKASSA = "aikassa"
    PLATEGA = "platega"
    PAYPALYCH = "paypalych"
    BALANCE = "balance"
    TOPUP = "topup"


class PaymentType(str, enum.Enum):
    SUBSCRIPTION = "subscription"  # оплата подписки
    TOPUP = "topup"  # пополнение баланса


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vpn_key_id = Column(
        Integer, ForeignKey("vpn_keys.id", ondelete="SET NULL"), nullable=True
    )
    provider = Column(String(32), nullable=False)
    payment_type = Column(
        String(32), default=PaymentType.SUBSCRIPTION.value, nullable=False
    )
    external_id = Column(String(256), nullable=True, unique=True)
    amount = Column(Numeric(10, 2), nullable=False)
    currency = Column(String(8), default="RUB", nullable=False)
    status = Column(String(16), default=PaymentStatus.PENDING.value, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    meta = Column(Text, nullable=True)

    user = relationship("User", back_populates="payments")

    def __repr__(self) -> str:
        return f"<Payment id={self.id} provider={self.provider} status={self.status} amount={self.amount}>"
