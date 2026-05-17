import asyncio
import pytest
from decimal import Decimal
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.user import User
from app.models.payment import Payment, PaymentStatus, PaymentProvider, PaymentType
from app.models.plan import Plan
from app.models.vpn_key import VpnKey, VpnKeyStatus
from app.models.referral import Referral, ReferralBonusType


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def session(engine):
    async_session_factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    async with async_session_factory() as session:
        yield session


@pytest.fixture
async def sample_user(session):
    user = User(
        id=123456789,
        username="testuser",
        full_name="Test User",
        balance=Decimal("100.00"),
        referral_code="TEST123",
        language="ru",
    )
    session.add(user)
    await session.commit()
    return user


@pytest.fixture
async def sample_plan(session):
    plan = Plan(
        id=1,
        name="Test Plan 30d",
        slug="test_30d",
        description="Test plan",
        duration_days=30,
        price=Decimal("10.00"),
        is_active=True,
    )
    session.add(plan)
    await session.commit()
    return plan


@pytest.fixture
async def sample_payment(session, sample_user):
    payment = Payment(
        user_id=sample_user.id,
        provider=PaymentProvider.BALANCE.value,
        payment_type=PaymentType.SUBSCRIPTION.value,
        amount=Decimal("10.00"),
        currency="RUB",
        status=PaymentStatus.PENDING.value,
    )
    session.add(payment)
    await session.commit()
    return payment


@pytest.fixture
async def sample_vpn_key(session, sample_user, sample_plan):
    key = VpnKey(
        user_id=sample_user.id,
        plan_id=sample_plan.id,
        pasarguard_key_id="vpn_123456789_1",
        access_url="https://example.com/sub/test",
        name="Test Key",
        price=Decimal("10.00"),
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        status=VpnKeyStatus.ACTIVE.value,
    )
    session.add(key)
    await session.commit()
    return key


@pytest.fixture
async def sample_referral(session, sample_user):
    referred = User(
        id=987654321,
        username="referred_user",
        full_name="Referred User",
        balance=Decimal("0.00"),
    )
    session.add(referred)
    await session.commit()

    ref = Referral(
        referrer_id=sample_user.id,
        referred_id=referred.id,
        bonus_type=ReferralBonusType.BALANCE.value,
        bonus_value=Decimal("10.00"),
    )
    session.add(ref)
    await session.commit()
    return ref
