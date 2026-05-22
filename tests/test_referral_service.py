"""Tests for ReferralService: creation, limits, fraud prevention."""

import pytest
from decimal import Decimal
from types import SimpleNamespace

from app.bot.handlers import start as start_handler
from app.models.bot_settings import BotSettings
from app.models.referral import ReferralBonusType
from app.models.user import User
from app.services.bot_settings import reset_bot_settings_cache
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

    async def test_referral_bonus_type_is_normalized(self, session, sample_user):
        referred = User(id=555, username="referred5", full_name="Referred Five")
        session.add(referred)
        await session.commit()

        svc = ReferralService(session)
        ref = await svc.create(
            referrer_id=sample_user.id,
            referred_id=referred.id,
            bonus_type="unexpected",
            bonus_value=Decimal("7"),
        )
        assert ref is not None
        assert ref.bonus_type == ReferralBonusType.DAYS.value


@pytest.mark.asyncio
async def test_start_referral_immediately_pays_bonus(session, sample_user, monkeypatch):
    await reset_bot_settings_cache()
    session.add_all(
        [
            BotSettings(key="referral_bonus_type", value="balance"),
            BotSettings(key="referral_bonus_value", value="15"),
        ]
    )
    await session.commit()
    await reset_bot_settings_cache()

    class _SessionContext:
        async def __aenter__(self):
            return session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def fake_menu(*args, **kwargs):
        return None

    async def fake_answer_with_photo(*args, **kwargs):
        return None

    class _Broadcast:
        async def broadcast(self, payload):
            return None

    monkeypatch.setattr(start_handler, "AsyncSessionFactory", lambda: _SessionContext())
    monkeypatch.setattr(start_handler, "_get_menu_kb", fake_menu)
    monkeypatch.setattr(start_handler, "_is_admin", lambda _user_id: False)
    monkeypatch.setattr("app.bot.utils.media.answer_with_photo", fake_answer_with_photo)
    monkeypatch.setattr("app.services.notification.notification_manager", _Broadcast())

    message = SimpleNamespace(
        text=f"/start {sample_user.referral_code}",
        from_user=SimpleNamespace(
            id=987654321,
            username="new_ref_user",
            full_name="New Referral User",
            first_name="New",
        ),
    )

    await start_handler.cmd_start(message)

    svc = ReferralService(session)
    refs = await svc.get_for_user(sample_user.id)
    assert len(refs) == 1
    assert refs[0].is_paid is True

    from app.services.user import UserService

    await session.refresh(sample_user)
    referrer = await UserService(session).get_by_id(sample_user.id)
    assert referrer.balance == Decimal("115.00")
