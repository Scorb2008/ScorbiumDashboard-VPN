from decimal import Decimal
from typing import Optional
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.referral import Referral, ReferralBonusType
from app.models.user import User
from app.utils.log import log


class ReferralService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_stats(self) -> dict:
        total = await self.session.execute(select(func.count()).select_from(Referral))
        paid = await self.session.execute(
            select(func.count()).select_from(Referral).where(Referral.is_paid.is_(True))
        )
        bonus_sum = await self.session.execute(
            select(func.sum(Referral.bonus_value)).where(
                Referral.bonus_type == ReferralBonusType.DAYS.value
            )
        )
        return {
            "total_referrals": total.scalar_one(),
            "paid_referrals": paid.scalar_one(),
            "total_bonus_days": int(bonus_sum.scalar_one() or 0),
        }

    async def get_top_referrers(self, limit: int = 50) -> list[dict]:
        """Alias for get_top with default limit of 50."""
        return await self.get_top(limit=limit)

    async def get_top(self, limit: int = 20) -> list[dict]:
        result = await self.session.execute(
            select(
                Referral.referrer_id,
                User.username,
                User.full_name,
                func.count(Referral.id).label("count"),
            )
            .join(User, User.id == Referral.referrer_id)
            .group_by(Referral.referrer_id, User.username, User.full_name)
            .order_by(func.count(Referral.id).desc())
            .limit(limit)
        )
        return [
            {
                "user_id": row.referrer_id,
                "username": row.username,
                "full_name": row.full_name,
                "referral_count": row.count,
            }
            for row in result.all()
        ]

    async def get_for_user(self, referrer_id: int) -> list[Referral]:
        result = await self.session.execute(
            select(Referral)
            .where(Referral.referrer_id == referrer_id)
            .order_by(Referral.created_at.desc())
        )
        return list(result.scalars().all())

    MAX_REFERRALS_PER_USER = 500
    MAX_BONUS_VALUE = Decimal("999999")

    @staticmethod
    def normalize_bonus_type(bonus_type: str | None) -> str:
        allowed = {
            ReferralBonusType.DAYS.value,
            ReferralBonusType.BALANCE.value,
            ReferralBonusType.PERCENT.value,
        }
        normalized = (bonus_type or "").strip().lower()
        return normalized if normalized in allowed else ReferralBonusType.DAYS.value

    @classmethod
    def format_bonus_label(
        cls,
        bonus_type: str | None,
        bonus_value: Decimal | int | str | None,
        *,
        lang: str = "ru",
    ) -> str:
        normalized_type = cls.normalize_bonus_type(bonus_type)
        value = Decimal(str(bonus_value or 0))

        if normalized_type == ReferralBonusType.BALANCE.value:
            return (
                f"{value.normalize()} ₽"
                if value == value.to_integral()
                else f"{value} ₽"
            )
        if normalized_type == ReferralBonusType.PERCENT.value:
            return (
                f"{value.normalize()}%" if value == value.to_integral() else f"{value}%"
            )

        days_word = {
            "ru": "дн.",
            "en": "days",
            "fa": "روز",
        }.get(lang, "days")
        days_value = int(value)
        return f"{days_value} {days_word}"

    async def create(
        self,
        referrer_id: int,
        referred_id: int,
        bonus_type: str = "days",
        bonus_value: Decimal = Decimal("3"),
        bonus_days: int = 0,
    ) -> Optional[Referral]:
        if referrer_id == referred_id:
            log.warning(f"Referral fraud: user {referrer_id} referred themselves")
            return None

        bonus_type = self.normalize_bonus_type(bonus_type)

        # Validate bonus
        if bonus_value <= 0:
            log.warning(
                f"Referral: non-positive bonus {bonus_value} for referrer {referrer_id}"
            )
            return None
        if bonus_value > self.MAX_BONUS_VALUE:
            bonus_value = self.MAX_BONUS_VALUE

        result = await self.session.execute(
            select(Referral).where(Referral.referred_id == referred_id).limit(1)
        )
        existing = result.scalar_one_or_none()
        if existing:
            return None

        count = await self.count_referrals(referrer_id)
        if count >= self.MAX_REFERRALS_PER_USER:
            log.warning(f"Referral limit reached for user {referrer_id}: {count}")
            return None

        ref = Referral(
            referrer_id=referrer_id,
            referred_id=referred_id,
            bonus_type=bonus_type,
            bonus_value=bonus_value,
        )
        self.session.add(ref)
        await self.session.flush()
        return ref

    async def pay_bonus(self, referral_id: int) -> Optional[Referral]:
        result = await self.session.execute(
            select(Referral).where(Referral.id == referral_id)
        )
        ref = result.scalar_one_or_none()
        if not ref or ref.is_paid:
            return ref

        from app.services.user import UserService

        user_svc = UserService(self.session)

        bonus_type = ref.bonus_type or "days"
        bonus_type = self.normalize_bonus_type(bonus_type)
        bonus_value = ref.bonus_value or Decimal("0")

        if not bonus_value or bonus_value <= 0:
            ref.is_paid = True
            await self.session.flush()
            return ref

        if bonus_type == ReferralBonusType.BALANCE.value:
            await user_svc.add_balance(ref.referrer_id, bonus_value)
        elif bonus_type == ReferralBonusType.DAYS.value:
            from app.models.vpn_key import VpnKey, VpnKeyStatus
            from app.services.pasarguard.pasarguard import get_vpn_panel

            key_result = await self.session.execute(
                select(VpnKey).where(
                    VpnKey.user_id == ref.referrer_id,
                    VpnKey.status == VpnKeyStatus.ACTIVE.value,
                )
            )
            key = key_result.scalar_one_or_none()
            if key and key.expires_at:
                from datetime import timedelta

                key.expires_at = key.expires_at + timedelta(days=int(bonus_value))
                if key.pasarguard_key_id:
                    try:
                        await get_vpn_panel().extend_user(
                            key.pasarguard_key_id, int(bonus_value)
                        )
                    except Exception as e:
                        log.warning(f"Failed to extend VPN for referral bonus: {e}")
        elif bonus_type == ReferralBonusType.PERCENT.value:
            await user_svc.add_balance(ref.referrer_id, bonus_value)

        ref.is_paid = True
        await self.session.flush()
        return ref

    async def count_referrals(self, referrer_id: int) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(Referral)
            .where(Referral.referrer_id == referrer_id)
        )
        return result.scalar_one()
