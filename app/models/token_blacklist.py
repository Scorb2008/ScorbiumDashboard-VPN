from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Integer, String, func

from app.models.base import Base


class BlacklistedToken(Base):
    __tablename__ = "blacklisted_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    jti = Column(String(128), nullable=True, index=True)
    sub = Column(String(64), nullable=False, index=True)
    blacklist_all = Column(Boolean, default=False, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<BlacklistedToken id={self.id} sub={self.sub} jti={self.jti}>"

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return self.expires_at < datetime.now(timezone.utc)
