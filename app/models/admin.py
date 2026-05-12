import enum

from sqlalchemy import Boolean, Column, Integer, String, Text

from app.models.base import Base


class AdminRole(str, enum.Enum):
    SUPERADMIN = "superadmin"
    MANAGER = "manager"
    OPERATOR = "operator"


class Admin(Base):
    __tablename__ = "admins"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)
    role = Column(String(32), nullable=False, default=AdminRole.OPERATOR.value)
    is_active = Column(Boolean, default=True, nullable=False)
    totp_secret = Column(String(32), nullable=True)
    backup_codes = Column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<Admin id={self.id} username={self.username} role={self.role}>"
