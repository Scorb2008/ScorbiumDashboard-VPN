from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api.cabinet import auth as cabinet_auth
from app.api.cabinet import views as cabinet_views
from app.api.cabinet import get_cabinet_router
from app.api.dependencies import get_db
from app.models.payment import Payment, PaymentProvider, PaymentStatus
from app.models.vpn_key import VpnKey, VpnKeyStatus


@pytest.fixture
async def cabinet_e2e_client(session, monkeypatch):
    app = FastAPI()
    app.include_router(get_cabinet_router())

    async def override_get_db():
        yield session

    async def no_refresh(self, keys):
        return None

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(cabinet_views.VpnKeyService, "refresh_traffic_for_keys", no_refresh)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="https://testserver",
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_cabinet_widget_login_purchase_issue_and_view_keys_smoke(
    cabinet_e2e_client,
    session,
    sample_user,
    sample_plan,
    monkeypatch,
):
    def fake_verify_telegram_login(data, secret=None):
        return {
            "id": str(sample_user.id),
            "username": sample_user.username,
            "first_name": "Test",
            "last_name": "User",
            "auth_date": str(int(datetime.now(timezone.utc).timestamp())),
            "hash": "stub-hash",
        }

    async def fake_provision(self, user_id: int, plan):
        key = VpnKey(
            user_id=user_id,
            plan_id=plan.id,
            pasarguard_key_id=f"vpn_{user_id}_smoke",
            access_url="https://vpn.example/sub/smoke-e2e",
            name="Smoke E2E Key",
            price=plan.price,
            expires_at=datetime.now(timezone.utc) + timedelta(days=plan.duration_days),
            status=VpnKeyStatus.ACTIVE.value,
        )
        self.session.add(key)
        await self.session.flush()
        return key

    monkeypatch.setattr(cabinet_auth, "_verify_telegram_login", fake_verify_telegram_login)
    monkeypatch.setattr(cabinet_views.VpnKeyService, "provision", fake_provision)

    login_response = await cabinet_e2e_client.get("/cabinet/")

    assert login_response.status_code == 200
    assert "Вход в кабинет" in login_response.text
    assert "Войти через Telegram" in login_response.text
    assert "/cabinet/auth" in login_response.text

    auth_response = await cabinet_e2e_client.post(
        "/cabinet/auth",
        json={
            "id": str(sample_user.id),
            "username": sample_user.username,
            "first_name": "Test",
            "last_name": "User",
            "auth_date": str(int(datetime.now(timezone.utc).timestamp())),
            "hash": "widget-hash",
        },
    )

    assert auth_response.status_code == 200
    assert auth_response.json()["ok"] is True
    assert auth_response.json()["redirect"] == "/cabinet/"
    assert "cabinet_session=" in auth_response.headers["set-cookie"]

    plans_response = await cabinet_e2e_client.get("/cabinet/plans")

    assert plans_response.status_code == 200
    assert sample_plan.name in plans_response.text

    pay_response = await cabinet_e2e_client.post(
        "/cabinet/pay/balance",
        data={"plan_id": str(sample_plan.id)},
    )

    assert pay_response.status_code == 200
    pay_payload = pay_response.json()
    assert pay_payload["ok"] is True
    assert pay_payload["status"] == "succeeded"
    assert pay_payload["redirect"] == "/cabinet/keys"
    assert pay_payload["access_url"] == "https://vpn.example/sub/smoke-e2e"

    keys_response = await cabinet_e2e_client.get("/cabinet/keys")

    assert keys_response.status_code == 200
    assert "Smoke E2E Key" in keys_response.text
    assert "https://vpn.example/sub/smoke-e2e" in keys_response.text

    await session.refresh(sample_user)
    assert Decimal(str(sample_user.balance)) == Decimal("90.00")

    payment = (
        await session.execute(select(Payment).order_by(Payment.id.desc()))
    ).scalars().first()
    assert payment is not None
    assert payment.provider == PaymentProvider.BALANCE.value
    assert payment.status == PaymentStatus.SUCCEEDED.value
    assert payment.vpn_key_id is not None

    key = (await session.execute(select(VpnKey).order_by(VpnKey.id.desc()))).scalars().first()
    assert key is not None
    assert key.user_id == sample_user.id
    assert key.access_url == "https://vpn.example/sub/smoke-e2e"
