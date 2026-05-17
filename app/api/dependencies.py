from typing import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.permissions import PERMISSIONS, has_permission
from app.utils.security import decode_access_token_full

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


async def get_db() -> AsyncSession:
    """FastAPI dependency — yields an async DB session."""
    async with get_session() as session:
        yield session


async def get_current_admin(token: str = Depends(oauth2_scheme)) -> dict:
    """Returns {"sub": str, "role": str} for the authenticated admin."""
    info = decode_access_token_full(token)
    if info is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    role = str(info.get("role", "")).strip().lower()
    if role not in PERMISSIONS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required",
        )
    jti = info.get("jti")
    sub = info.get("sub", "")
    if jti:
        from app.core.database import get_session
        from app.services.token_blacklist import TokenBlacklistService
        async with get_session() as session:
            blacklisted = await TokenBlacklistService(session).is_blacklisted(jti, sub)
            if blacklisted:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token has been revoked",
                    headers={"WWW-Authenticate": "Bearer"},
                )
    return info


async def get_current_admin_username(token: str = Depends(oauth2_scheme)) -> str:
    """Backward-compatible: returns just the username string."""
    info = decode_access_token_full(token)
    if info is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    role = str(info.get("role", "")).strip().lower()
    if role not in PERMISSIONS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required",
        )
    return str(info.get("sub"))


def require_role(*roles: str) -> Callable:
    """Dependency factory that checks the admin has one of the given roles."""

    async def _check(admin: dict = Depends(get_current_admin)) -> dict:
        if admin["role"] not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return admin

    return _check


def require_permission(permission: str) -> Callable:
    """Dependency factory that checks the admin has a specific permission."""

    async def _check(admin: dict = Depends(get_current_admin)) -> dict:
        if not has_permission(admin["role"], permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return admin

    return _check
