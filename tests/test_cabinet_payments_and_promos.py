import json
from datetime import timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api.cabinet import views as cabinet_views
from app.api.dependencies import get_db
from app.models.payment import Payment, PaymentProvider, PaymentStatus, PaymentType
from app.models.promo import PromoCode, PromoType
from app.models.promo_usage import PromoUsage


@pytest.fixture
async def cabinet_client(session, sample_user, monkeypatch):
    app = FastAPI()
    app.include_router(cabinet_views.router)

    async def override_get_db():
        yield session

    async def fake_require_active_user(request, db):
        return sample_user

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(cabinet_views, "_require_active_user", fake_require_active_user)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://testserver") as client:
        yield client


@pytest.mark.asyncio
async def test_cabinet_promo_balance_credits_user(cabinet_client, session, sample_user):
    promo = PromoCode(
        code="GIFT100",
        promo_type=PromoType.BALANCE.value,
        value=Decimal("100.00"),
        max_uses=1,
        current_uses=0,
        is_active=True,
    )
    session.add(promo)
    await session.commit()

    response = await cabinet_client.post("/cabinet/promo/apply", data={"code": "GIFT100"})

    assert response.status_code == 200
    assert response.json()["ok"] is True

    await session.refresh(sample_user)
    assert Decimal(str(sample_user.balance)) == Decimal("200.00")

    usage = await session.execute(
        select(PromoUsage).where(
            PromoUsage.promo_id == promo.id,
            PromoUsage.user_id == sample_user.id,
        )
    )
    assert usage.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_cabinet_promo_days_extends_subscription(
    cabinet_client, session, sample_user, sample_vpn_key
):
    before = sample_vpn_key.expires_at
    promo = PromoCode(
        code="WEEK7",
        promo_type=PromoType.DAYS.value,
        value=Decimal("7.00"),
        max_uses=1,
        current_uses=0,
        is_active=True,
    )
    session.add(promo)
    await session.commit()

    response = await cabinet_client.post("/cabinet/promo/apply", data={"code": "WEEK7"})

    assert response.status_code == 200
    assert response.json()["ok"] is True

    await session.refresh(sample_vpn_key)
    after = sample_vpn_key.expires_at
    if after.tzinfo is None:
        after = after.replace(tzinfo=timezone.utc)
    assert after == before + timedelta(days=7)


@pytest.mark.asyncio
async def test_cabinet_pay_yookassa_creates_discounted_payment(
    cabinet_client, session, sample_user, sample_plan, monkeypatch
):
    promo = PromoCode(
        code="SALE20",
        promo_type=PromoType.DISCOUNT.value,
        value=Decimal("20.00"),
        max_uses=1,
        current_uses=0,
        is_active=True,
    )
    session.add(promo)
    await session.commit()

    calls = {}

    class FakeYookassa:
        async def create_payment(self, amount, description, return_url, currency="RUB", metadata=None, payment_method=None):
            calls["amount"] = amount
            calls["description"] = description
            calls["return_url"] = return_url
            calls["metadata"] = metadata
            return SimpleNamespace(
                id="yk_test_123",
                confirmation=SimpleNamespace(confirmation_url="https://pay.test/yk_test_123"),
            )

    async def fake_create():
        return FakeYookassa()

    monkeypatch.setattr(cabinet_views.YookassaService, "create", fake_create)

    response = await cabinet_client.post(
        "/cabinet/pay/yookassa",
        data={"plan_id": sample_plan.id, "promo_code": "SALE20"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["status"] == "pending"
    assert data["payment_url"] == "https://pay.test/yk_test_123"
    assert calls["amount"] == Decimal("8.00")

    payment_result = await session.execute(
        select(Payment).order_by(Payment.id.desc())
    )
    payment = payment_result.scalars().first()
    assert payment is not None
    assert payment.provider == PaymentProvider.YOOKASSA.value
    assert payment.status == PaymentStatus.PENDING.value
    assert Decimal(str(payment.amount)) == Decimal("8.00")
    assert payment.external_id == "yk_test_123"

    meta = json.loads(payment.meta)
    assert meta["plan_id"] == sample_plan.id
    assert meta["promo_code"] == "SALE20"
    assert meta["discount_amount"] == "2.00"

    usage = await session.execute(
        select(PromoUsage).where(
            PromoUsage.promo_id == promo.id,
            PromoUsage.user_id == sample_user.id,
        )
    )
    assert usage.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_cabinet_pay_status_finalizes_topup_once(
    cabinet_client, session, sample_user, monkeypatch
):
    payment = Payment(
        user_id=sample_user.id,
        provider=PaymentProvider.YOOKASSA.value,
        payment_type=PaymentType.TOPUP.value,
        amount=Decimal("50.00"),
        currency="RUB",
        status=PaymentStatus.PENDING.value,
        external_id="yk_topup_1",
    )
    session.add(payment)
    await session.commit()

    class FakeYookassa:
        async def get_payment(self, external_id):
            assert external_id == "yk_topup_1"
            return SimpleNamespace(status="succeeded", id="yk_topup_1")

    async def fake_create():
        return FakeYookassa()

    monkeypatch.setattr(cabinet_views.YookassaService, "create", fake_create)

    first = await cabinet_client.get(f"/cabinet/pay/status/{payment.id}")
    assert first.status_code == 200
    assert first.json()["status"] == "succeeded"

    await session.refresh(sample_user)
    await session.refresh(payment)
    assert Decimal(str(sample_user.balance)) == Decimal("150.00")
    assert payment.status == PaymentStatus.SUCCEEDED.value

    second = await cabinet_client.get(f"/cabinet/pay/status/{payment.id}")
    assert second.status_code == 200
    assert second.json()["status"] == "succeeded"

    await session.refresh(sample_user)
    assert Decimal(str(sample_user.balance)) == Decimal("150.00")
