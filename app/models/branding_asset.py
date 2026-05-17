from sqlalchemy import Column, DateTime, Integer, LargeBinary, String, func

from app.models.base import Base


class BrandingAsset(Base):
    __tablename__ = "branding_assets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(64), nullable=False, unique=True, index=True)
    mime_type = Column(String(128), nullable=False)
    data = Column(LargeBinary, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=True,
    )

    def __repr__(self) -> str:
        return f"<BrandingAsset key={self.key}>"
