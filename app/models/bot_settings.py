from sqlalchemy import Column, Integer, String, Text

from app.models.base import Base


class BotSettings(Base):
    """Key-value store for bot customization settings."""

    __tablename__ = "bot_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(64), nullable=False, unique=True, index=True)
    value = Column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<BotSettings key={self.key}>"
