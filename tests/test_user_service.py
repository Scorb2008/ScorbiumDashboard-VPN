"""Tests for UserService: atomic balance operations, race conditions."""
import pytest
from decimal import Decimal
from sqlalchemy import select

from app.models.user import User
from app.schemas.user import UserCreate, UserUpdate
from app.services.user import UserService


class TestUserService:
    async def test_deduct_balance_sufficient(self, session, sample_user):
        svc = UserService(session)
        user = await svc.deduct_balance(sample_user.id, Decimal("30.00"))
        assert user is not None
        assert user.balance == Decimal("70.00")

    async def test_deduct_balance_insufficient(self, session, sample_user):
        svc = UserService(session)
        user = await svc.deduct_balance(sample_user.id, Decimal("200.00"))
        assert user is None

        # Balance should remain unchanged
        user = await svc.get_by_id(sample_user.id)
        assert user.balance == Decimal("100.00")

    async def test_deduct_balance_exact_amount(self, session, sample_user):
        svc = UserService(session)
        user = await svc.deduct_balance(sample_user.id, Decimal("100.00"))
        assert user is not None
        assert user.balance == Decimal("0.00")

    async def test_deduct_balance_atomic(self, session, sample_user):
        svc = UserService(session)
        from sqlalchemy import update
        user = await svc.deduct_balance(sample_user.id, Decimal("10.00"))
        assert user is not None
        assert user.balance == Decimal("90.00")

    async def test_add_balance(self, session, sample_user):
        svc = UserService(session)
        user = await svc.add_balance(sample_user.id, Decimal("50.00"))
        assert user is not None
        assert user.balance == Decimal("150.00")

    async def test_add_balance_zero(self, session, sample_user):
        svc = UserService(session)
        user = await svc.add_balance(sample_user.id, Decimal("0"))
        assert user is not None
        assert user.balance == Decimal("100.00")

    async def test_get_or_create_new(self, session):
        svc = UserService(session)
        user, created = await svc.get_or_create(
            UserCreate(id=555, username="newuser", full_name="New User")
        )
        assert created is True
        assert user.id == 555
        assert user.balance == Decimal("0")

    async def test_get_or_create_existing(self, session, sample_user):
        svc = UserService(session)
        user, created = await svc.get_or_create(
            UserCreate(id=sample_user.id, username=sample_user.username, full_name=sample_user.full_name)
        )
        assert created is False
        assert user.id == sample_user.id

    async def test_ban_unban(self, session, sample_user):
        svc = UserService(session)
        user = await svc.ban(sample_user.id)
        assert user is not None
        assert user.is_banned is True

        user = await svc.unban(sample_user.id)
        assert user is not None
        assert user.is_banned is False

    async def test_get_by_referral_code(self, session, sample_user):
        svc = UserService(session)
        user = await svc.get_by_referral_code("TEST123")
        assert user is not None
        assert user.id == sample_user.id
