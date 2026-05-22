from decimal import Decimal
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.plan import Plan


class PlanService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_all(self, only_active: bool = False) -> list[Plan]:
        q = select(Plan).order_by(Plan.sort_order, Plan.price)
        if only_active:
            q = q.where(Plan.is_active.is_(True))
        result = await self.session.execute(q)
        return list(result.scalars().all())

    async def get_by_id(self, plan_id: int) -> Optional[Plan]:
        result = await self.session.execute(select(Plan).where(Plan.id == plan_id))
        return result.scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> Optional[Plan]:
        result = await self.session.execute(select(Plan).where(Plan.slug == slug))
        return result.scalar_one_or_none()

    async def create(
        self,
        name: str,
        slug: str,
        duration_days: int,
        price: Decimal,
        description: str | None = None,
        currency: str = "RUB",
        sort_order: int = 0,
    ) -> Plan:
        plan = Plan(
            name=name,
            slug=slug,
            duration_days=duration_days,
            price=price,
            description=description,
            currency=currency,
            sort_order=sort_order,
        )
        self.session.add(plan)
        await self.session.flush()
        return plan

    async def update(self, plan_id: int, **kwargs) -> Optional[Plan]:
        plan = await self.get_by_id(plan_id)
        if not plan:
            return None
        for key, value in kwargs.items():
            if hasattr(plan, key):
                setattr(plan, key, value)
        await self.session.flush()
        return plan

    async def toggle_active(self, plan_id: int) -> Optional[Plan]:
        plan = await self.get_by_id(plan_id)
        if not plan:
            return None
        plan.is_active = not plan.is_active
        await self.session.flush()
        return plan

    async def delete(self, plan_id: int) -> bool:
        plan = await self.get_by_id(plan_id)
        if not plan:
            return False
        await self.session.delete(plan)
        await self.session.flush()
        return True
