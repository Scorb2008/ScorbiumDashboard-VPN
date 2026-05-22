from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.schemas.user import UserCreate, UserUpdate


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, user_id: int) -> Optional[User]:
        result = await self.session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def get_all(self, limit: int = 100, offset: int = 0) -> list[User]:
        result = await self.session.execute(select(User).limit(limit).offset(offset))
        return list(result.scalars().all())

    async def count_all(self) -> int:
        result = await self.session.execute(select(func.count()).select_from(User))
        return result.scalar_one()

    async def create(self, data: UserCreate) -> User:
        user = User(**data.model_dump())
        self.session.add(user)
        await self.session.flush()
        return user

    async def get_or_create(self, data: UserCreate) -> tuple[User, bool]:
        user = await self.get_by_id(data.id)
        if user:
            return user, False
        user = await self.create(data)
        return user, True

    async def sync_telegram_profile(self, data: UserCreate) -> tuple[User, bool]:
        user = await self.get_by_id(data.id)
        if not user:
            user = await self.create(data)
            return user, True

        username = (data.username or "").strip() or user.username
        full_name = data.full_name.strip() or user.full_name

        if user.username != username:
            user.username = username
        if user.full_name != full_name:
            user.full_name = full_name

        await self.session.flush()
        return user, False

    async def update(self, user_id: int, data: UserUpdate) -> Optional[User]:
        user = await self.get_by_id(user_id)
        if not user:
            return None
        user.update_fields(**data.model_dump(exclude_none=True))
        await self.session.flush()
        return user

    async def ban(self, user_id: int) -> Optional[User]:
        return await self.update(user_id, UserUpdate(is_banned=True))

    async def unban(self, user_id: int) -> Optional[User]:
        return await self.update(user_id, UserUpdate(is_banned=False))

    async def add_balance(self, user_id: int, amount) -> Optional[User]:
        """Atomic balance addition — race-condition safe."""
        amount_dec = Decimal(str(amount))
        result = await self.session.execute(
            update(User)
            .where(User.id == user_id)
            .values(balance=User.balance + amount_dec)
            .returning(User)
        )
        return result.scalar_one_or_none()

    async def deduct_balance(self, user_id: int, amount) -> Optional[User]:
        """Atomic balance deduction with built-in check — race-condition safe."""
        amount_dec = Decimal(str(amount))
        result = await self.session.execute(
            update(User)
            .where(
                User.id == user_id,
                User.balance >= amount_dec,
            )
            .values(balance=User.balance - amount_dec)
            .returning(User)
        )
        return result.scalar_one_or_none()

    async def get_by_referral_code(self, code: str) -> Optional[User]:
        result = await self.session.execute(
            select(User).where(User.referral_code == code)
        )
        return result.scalar_one_or_none()

    async def set_autorenew(self, user_id: int, enabled: bool) -> Optional[User]:
        user = await self.get_by_id(user_id)
        if not user:
            return None
        user.autorenew = enabled
        await self.session.flush()
        return user
