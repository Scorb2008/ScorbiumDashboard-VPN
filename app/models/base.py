from sqlalchemy import Column, DateTime, func
from sqlalchemy.orm import DeclarativeBase
from typing import Any


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    __abstract__ = True

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=True
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=True,
    )

    def to_dict(self) -> dict[str, Any]:
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    def update_fields(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            if hasattr(self, key) and value is not None:
                setattr(self, key, value)
