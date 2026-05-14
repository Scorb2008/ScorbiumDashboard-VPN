from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.promo import PromoCode, PromoType
from app.models.promo_usage import PromoUsage
from app.utils.log import log


@dataclass(frozen=True)
class PromoValidationResult:
    promo: Optional[PromoCode]
    message: Optional[str] = None


class PromoService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_all(self) -> list[PromoCode]:
        result = await self.session.execute(
            select(PromoCode).order_by(PromoCode.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_by_id(self, promo_id: int) -> Optional[PromoCode]:
        result = await self.session.execute(select(PromoCode).where(PromoCode.id == promo_id))
        return result.scalar_one_or_none()

    async def get_by_code(self, code: str) -> Optional[PromoCode]:
        result = await self.session.execute(
            select(PromoCode).where(PromoCode.code == code.upper().strip())
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        code: str,
        promo_type: str,
        value: Decimal,
        plan_id: Optional[int] = None,
        max_uses: int = 0,
        **_kwargs,
    ) -> PromoCode:
        promo = PromoCode(
            code=code.upper().strip(),
            promo_type=promo_type,
            value=value,
            plan_id=plan_id or None,
            max_uses=max_uses,
        )
        self.session.add(promo)
        await self.session.flush()
        return promo

    async def delete(self, promo_id: int) -> None:
        promo = await self.get_by_id(promo_id)
        if promo:
            await self.session.delete(promo)
            await self.session.flush()

    async def toggle_active(self, promo_id: int) -> Optional[PromoCode]:
        promo = await self.get_by_id(promo_id)
        if promo:
            promo.is_active = not promo.is_active
            await self.session.flush()
        return promo

    async def validate_for_user(
        self,
        code: str,
        user_id: Optional[int] = None,
        *,
        promo_type: Optional[str] = None,
        plan_id: Optional[int] = None,
    ) -> PromoValidationResult:
        promo = await self.get_by_code(code)
        if not promo or not promo.is_active:
            return PromoValidationResult(None, "Промокод не найден или отключён")

        if promo_type and str(promo.promo_type) != promo_type:
            return PromoValidationResult(None, "Этот промокод нельзя применить в данном сценарии")

        if promo.current_uses is None:
            promo.current_uses = 0
        if promo.max_uses > 0 and promo.current_uses >= promo.max_uses:
            return PromoValidationResult(None, "Лимит использований этого промокода исчерпан")

        if plan_id and promo.plan_id and promo.plan_id != plan_id:
            return PromoValidationResult(None, "Промокод действует только для другого тарифа")

        if user_id:
            result = await self.session.execute(
                select(PromoUsage).where(
                    PromoUsage.promo_id == promo.id,
                    PromoUsage.user_id == user_id,
                )
            )
            if result.scalar_one_or_none():
                log.warning(f"Promo {promo.code} already used by user {user_id}")
                return PromoValidationResult(None, "Вы уже использовали этот промокод")

        return PromoValidationResult(promo, None)

    async def consume(self, promo: PromoCode, user_id: Optional[int] = None) -> Optional[PromoCode]:
        validation = await self.validate_for_user(
            promo.code,
            user_id=user_id,
            promo_type=str(promo.promo_type),
        )
        if not validation.promo:
            return None

        stored_promo = validation.promo
        if stored_promo.current_uses is None:
            stored_promo.current_uses = 0
        stored_promo.current_uses += 1
        await self.session.flush()

        if user_id:
            self.session.add(PromoUsage(promo_id=stored_promo.id, user_id=user_id))
            await self.session.flush()
        return stored_promo

    async def apply(self, code: str, user_id: Optional[int] = None) -> Optional[PromoCode]:
        validation = await self.validate_for_user(code, user_id=user_id)
        if not validation.promo:
            return None
        return await self.consume(validation.promo, user_id=user_id)
