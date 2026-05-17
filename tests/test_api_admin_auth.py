import pytest
from fastapi import HTTPException
from fastapi.security import OAuth2PasswordRequestForm

from app.api.dependencies import get_current_admin
from app.api.v1.auth import login
from app.models.admin import Admin, AdminRole
from app.utils.security import create_access_token, hash_password


@pytest.mark.asyncio
async def test_api_login_accepts_database_admin(session):
    admin = Admin(
        username="db-admin",
        password_hash=hash_password("StrongPass123"),
        role=AdminRole.MANAGER.value,
        is_active=True,
    )
    session.add(admin)
    await session.commit()

    form = OAuth2PasswordRequestForm(
        username="db-admin",
        password="StrongPass123",
        scope="",
    )
    result = await login(form=form, db=session)

    assert result.token_type == "bearer"
    assert result.access_token


@pytest.mark.asyncio
async def test_get_current_admin_rejects_non_admin_role():
    token = create_access_token(subject="123", role="user")

    with pytest.raises(HTTPException) as exc_info:
        await get_current_admin(token=token)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Admin role required"
