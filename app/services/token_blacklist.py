from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.token_blacklist import BlacklistedToken


class TokenBlacklistService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def blacklist_jti(self, jti: str, sub: str, expires_at: Optional[datetime] = None) -> None:
        """Blacklist a specific JWT by its jti."""
        entry = BlacklistedToken(
            jti=jti,
            sub=sub,
            blacklist_all=False,
            expires_at=expires_at,
        )
        self.session.add(entry)
        await self.session.flush()

    async def blacklist_all_for_user(self, sub: str, expires_at: Optional[datetime] = None) -> None:
        """Blacklist ALL tokens for a user (used on password change)."""
        if expires_at is None:
            expires_at = datetime.now(timezone.utc) + timedelta(days=7)
        entry = BlacklistedToken(
            jti=None,
            sub=sub,
            blacklist_all=True,
            expires_at=expires_at,
        )
        self.session.add(entry)
        await self.session.flush()

    async def is_blacklisted(self, jti: str, sub: str) -> bool:
        """Check if a token (identified by jti + sub) is blacklisted."""
        now = datetime.now(timezone.utc)
        result = await self.session.execute(
            select(BlacklistedToken).where(
                BlacklistedToken.sub == sub,
                (BlacklistedToken.expires_at > now) | (BlacklistedToken.expires_at.is_(None)),
            )
        )
        entries = result.scalars().all()
        for entry in entries:
            if entry.blacklist_all:
                return True
            if entry.jti == jti:
                return True
        return False

    async def cleanup_expired(self) -> int:
        """Remove expired blacklist entries. Returns count removed."""
        result = await self.session.execute(
            delete(BlacklistedToken).where(
                BlacklistedToken.expires_at < datetime.now(timezone.utc),
                BlacklistedToken.expires_at.isnot(None),
            )
        )
        return result.rowcount

    @staticmethod
    async def get_jti_from_token(token: str, secret: str, algorithm: str = "HS256") -> Optional[str]:
        """Extract jti from a JWT without validating expiry (for logout)."""
        from jose import JWTError, jwt
        try:
            payload = jwt.decode(token, secret, algorithms=[algorithm], options={"verify_exp": False})
            return payload.get("jti")
        except JWTError:
            return None
