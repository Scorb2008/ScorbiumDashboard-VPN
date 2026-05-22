from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin import Admin, AdminRole
from app.utils.security import hash_password, verify_password


class AdminService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, admin_id: int) -> Optional[Admin]:
        result = await self.session.execute(select(Admin).where(Admin.id == admin_id))
        return result.scalar_one_or_none()

    async def get_by_username(self, username: str) -> Optional[Admin]:
        result = await self.session.execute(
            select(Admin).where(Admin.username == username)
        )
        return result.scalar_one_or_none()

    async def get_all(self) -> list[Admin]:
        result = await self.session.execute(select(Admin).order_by(Admin.id))
        return list(result.scalars().all())

    async def create(
        self,
        username: str,
        password: str,
        role: str = AdminRole.OPERATOR.value,
    ) -> Admin:
        admin = Admin(
            username=username,
            password_hash=hash_password(password),
            role=role,
            is_active=True,
        )
        self.session.add(admin)
        await self.session.flush()
        return admin

    async def update(
        self,
        admin_id: int,
        *,
        username: Optional[str] = None,
        password: Optional[str] = None,
        role: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> Optional[Admin]:
        admin = await self.get_by_id(admin_id)
        if not admin:
            return None
        if username is not None:
            admin.username = username
        if password is not None:
            admin.password_hash = hash_password(password)
        if role is not None:
            admin.role = role
        if is_active is not None:
            admin.is_active = is_active
        await self.session.flush()
        return admin

    async def delete(self, admin_id: int) -> bool:
        admin = await self.get_by_id(admin_id)
        if not admin:
            return False
        await self.session.delete(admin)
        await self.session.flush()
        return True

    async def authenticate(self, username: str, password: str) -> Optional[Admin]:
        admin = await self.get_by_username(username)
        if not admin or not admin.is_active:
            return None
        if not verify_password(password, admin.password_hash):
            return None
        return admin
