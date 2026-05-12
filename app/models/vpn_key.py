import enum
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import deferred, relationship

from app.models.base import Base


class VpnKeyStatus(str, enum.Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


class VpnKey(Base):
    __tablename__ = "vpn_keys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id = Column(
        Integer, ForeignKey("plans.id", ondelete="SET NULL"), nullable=True
    )
    pasarguard_key_id = Column(String(128), nullable=True, unique=True)
    access_url = Column(Text, nullable=False)
    name = Column(String(128), nullable=True)
    price = Column(Numeric(10, 2), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    status = Column(
        String(16),
        default=VpnKeyStatus.ACTIVE.value,
        nullable=False,
        index=True,
    )
    download = deferred(Column(BigInteger, default=0, nullable=False, server_default="0"))
    upload = deferred(Column(BigInteger, default=0, nullable=False, server_default="0"))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="vpn_keys")
    plan = relationship("Plan", lazy="selectin")

    def __repr__(self) -> str:
        return f"<VpnKey id={self.id} user_id={self.user_id} status={self.status}>"
