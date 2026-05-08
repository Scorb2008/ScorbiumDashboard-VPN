"""Tests for ReferralService: creation, limits, fraud prevention."""
import pytest
from decimal import Decimal

from app.models.referral import Referral, ReferralBonusType
from app.models.user import User
from app.services.referral import ReferralService


class TestReferralService:
    async def test_create_referral(self, session, sample_user):
        referred = User(id=111, username="referred1", full_name="Referred One")
        session.add(referred)
        await session.commit()

        svc = ReferralService(session)
        ref = await svc.create(
            referrer_id=sample_user.id,
            referred_id=referred.id,
            bonus_type=ReferralBonusType.BALANCE.value,
            bonus_value=Decimal("10.00"),
        )
        assert ref is not None
        assert ref.referrer_id == sample_user.id
        assert ref.referred_id == referred.id
        assert ref.is_paid is False

    async def test_prevent_self_referral(self, session, sample_user):
        svc = ReferralService(session)
        ref = await svc.create(
            referrer_id=sample_user.id,
            referred_id=sample_user.id,
            bonus_type=ReferralBonusType.DAYS.value,
            bonus_value=Decimal("3"),
        )
        assert ref is None

    async def test_prevent_duplicate_referral(self, session, sample_user):
        referred = User(id=222, username="referred2", full_name="Referred Two")
        session.add(referred)
        await session.commit()

        svc = ReferralService(session)
        ref1 = await svc.create(
            referrer_id=sample_user.id,
            referred_id=referred.id,
            bonus_type=ReferralBonusType.DAYS.value,
            bonus_value=Decimal("3"),
        )
        assert ref1 is not None

        ref2 = await svc.create(
            referrer_id=sample_user.id,
            referred_id=referred.id,
            bonus_type=ReferralBonusType.DAYS.value,
            bonus_value=Decimal("3"),
        )
        assert ref2 is None

    async def test_prevent_negative_bonus(self, session, sample_user):
        referred = User(id=333, username="referred3", full_name="Referred Three")
        session.add(referred)
        await session.commit()

        svc = ReferralService(session)
        ref = await svc.create(
            referrer_id=sample_user.id,
            referred_id=referred.id,
            bonus_type=ReferralBonusType.BALANCE.value,
            bonus_value=Decimal("-10.00"),
        )
        assert ref is None

    async def test_count_referrals(self, session, sample_user):
        svc = ReferralService(session)
        count = await svc.count_referrals(sample_user.id)
        assert count == 0

        for i in range(3):
            referred = User(id=1000 + i, username=f"ref{i}", full_name=f"Ref {i}")
            session.add(referred)
            await session.commit()
            await svc.create(
                referrer_id=sample_user.id,
                referred_id=referred.id,
                bonus_type=ReferralBonusType.DAYS.value,
                bonus_value=Decimal("3"),
            )

        count = await svc.count_referrals(sample_user.id)
        assert count == 3

    async def test_pay_bonus_balance(self, session, sample_user):
        svc = ReferralService(session)
        referred = User(id=444, username="referred4", full_name="Referred Four")
        session.add(referred)
        await session.commit()

        ref = await svc.create(
            referrer_id=sample_user.id,
            referred_id=referred.id,
            bonus_type=ReferralBonusType.BALANCE.value,
            bonus_value=Decimal("25.00"),
        )
        assert ref is not None

        paid = await svc.pay_bonus(ref.id)
        assert paid is not None
        assert paid.is_paid is True

        # Check balance increased
        from app.services.user import UserService
        user = await UserService(session).get_by_id(sample_user.id)
        assert user.balance == Decimal("125.00")
