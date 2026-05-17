from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import config
from app.models.admin import Admin, AdminRole
from app.services.admin import AdminService
from app.utils.security import hash_password


async def authenticate_admin_credentials(
    session: AsyncSession,
    username: str,
    password: str,
) -> Admin | None:
    """Authenticate admins against the DB, with env superadmin fallback."""
    service = AdminService(session)

    admin = await service.authenticate(username, password)
    if admin:
        return admin

    expected_username = config.web.web_superadmin_username
    expected_password = config.web.web_superadmin_password.get_secret_value()
    if username != expected_username or password != expected_password:
        return None

    admin = await service.get_by_username(username)
    if admin:
        if not admin.is_active:
            return None
        if admin.role != AdminRole.SUPERADMIN.value:
            admin.role = AdminRole.SUPERADMIN.value
            await session.commit()
            await session.refresh(admin)
        return admin

    admin = Admin(
        username=username,
        password_hash=hash_password(password),
        role=AdminRole.SUPERADMIN.value,
        is_active=True,
    )
    session.add(admin)
    await session.commit()
    await session.refresh(admin)
    return admin
