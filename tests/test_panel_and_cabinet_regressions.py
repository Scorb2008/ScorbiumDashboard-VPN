import json
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.api.cabinet import views as cabinet_views
from app.api.dependencies import get_db
from app.api.panel.routes import payments as payments_routes
from app.api.panel.routes import subscriptions as subscriptions_routes
from app.api.panel.routes import support as support_routes
from app.api.panel.routes import users as users_routes
from app.bot.handlers import payments as bot_payments
from app.bot.middlewares import user_notify as user_notify_middleware
from app.models.bot_settings import BotSettings
from app.models.payment import Payment, PaymentProvider, PaymentStatus, PaymentType
from app.models.referral import Referral
from app.models.promo import PromoCode, PromoType
from app.models.promo_usage import PromoUsage
from app.models.support import (
    SupportTicket,
    TicketMessage,
    TicketPriority,
    TicketStatus,
)
from app.models.vpn_key import VpnKey, VpnKeyStatus
from app.services import encryption as encryption_service
from app.services.bot_settings import BotSettingsService
from app.services.plan import PlanService
from app.services.platega import PlategaService


def _make_request(
    path: str, *, headers: list[tuple[bytes, bytes]] | None = None
) -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": headers or [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 443),
    }
    return Request(scope)


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
    async with AsyncClient(
        transport=transport, base_url="https://testserver"
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_cabinet_promo_days_creates_subscription_without_existing_key(
    cabinet_client, session, sample_user, monkeypatch
):
    promo = PromoCode(
        code="DAYSWELCOME",
        promo_type=PromoType.DAYS.value,
        value=Decimal("5.00"),
        max_uses=1,
        current_uses=0,
        is_active=True,
    )
    session.add(promo)
    await session.commit()

    expires_at = datetime.now(timezone.utc) + timedelta(days=5)

    async def fake_provision_days(
        self, user_id: int, days: int, name: str | None = None
    ):
        assert user_id == sample_user.id
        assert days == 5
        assert name == "Промокод — DAYSWELCOME"
        return SimpleNamespace(
            access_url="https://vpn.example/new-key", expires_at=expires_at
        )

    monkeypatch.setattr(
        cabinet_views.VpnKeyService, "provision_days", fake_provision_days
    )

    response = await cabinet_client.post(
        "/cabinet/promo/apply", data={"code": "DAYSWELCOME"}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["access_url"] == "https://vpn.example/new-key"
    assert "создана" in payload["message"]

    usage = await session.execute(
        select(PromoUsage).where(
            PromoUsage.promo_id == promo.id,
            PromoUsage.user_id == sample_user.id,
        )
    )
    assert usage.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_payments_page_htmx_returns_rows_and_ignores_invalid_status(
    session, sample_payment, monkeypatch
):
    monkeypatch.setattr(
        payments_routes,
        "_require_permission",
        lambda request, permission: {"sub": "admin", "role": "superadmin"},
    )

    async def fake_base_ctx(request, db, active):
        return {"request": request, "active": active}

    monkeypatch.setattr(payments_routes, "_base_ctx", fake_base_ctx)

    request = _make_request(
        "/panel/payments",
        headers=[(b"hx-request", b"true")],
    )

    response = await payments_routes.payments_page(
        request=request,
        status="definitely-invalid",
        payment_type="nope",
        db=session,
    )

    body = response.body.decode("utf-8")
    assert response.status_code == 200
    assert "<tr>" in body
    assert "История платежей" not in body
    assert f"#{sample_payment.id}" in body


@pytest.mark.asyncio
async def test_payments_stats_json_returns_daily_buckets_without_grouping_errors(
    session, sample_payment, monkeypatch
):
    sample_payment.status = PaymentStatus.SUCCEEDED.value
    sample_payment.amount = Decimal("199.99")
    await session.commit()

    monkeypatch.setattr(
        payments_routes,
        "_require_permission",
        lambda request, permission: {"sub": "admin", "role": "superadmin"},
    )

    response = await payments_routes.payments_stats_json(
        request=_make_request("/panel/payments/stats/json"),
        days=30,
        db=session,
    )

    payload = json.loads(response.body)
    assert response.status_code == 200
    assert payload["total_payments"] == 1
    assert payload["total_revenue"] == "199.99"
    assert payload["daily"]
    assert payload["daily"][0]["amount"] == 199.99


@pytest.mark.asyncio
async def test_payments_stats_json_returns_zero_filled_daily_series_without_sales(
    session, monkeypatch
):
    monkeypatch.setattr(
        payments_routes,
        "_require_permission",
        lambda request, permission: {"sub": "admin", "role": "superadmin"},
    )

    response = await payments_routes.payments_stats_json(
        request=_make_request("/panel/payments/stats/json"),
        days=30,
        db=session,
    )

    payload = json.loads(response.body)
    assert response.status_code == 200
    assert payload["total_payments"] == 0
    assert len(payload["daily"]) == 30
    assert all(point["amount"] == 0.0 for point in payload["daily"])


@pytest.mark.asyncio
async def test_support_reply_deduplicates_double_submit(
    session, sample_user, monkeypatch
):
    ticket = SupportTicket(
        user_id=sample_user.id,
        subject="Duplicate check",
        status=TicketStatus.OPEN.value,
        priority=TicketPriority.MEDIUM.value,
    )
    session.add(ticket)
    await session.commit()

    sent: list[tuple[int, str]] = []

    class FakeNotify:
        async def send_message(self, chat_id, text):
            sent.append((chat_id, text))

    monkeypatch.setattr(
        support_routes,
        "_require_permission",
        lambda request, permission: {"sub": "admin", "role": "superadmin"},
    )
    monkeypatch.setattr(support_routes, "TelegramNotifyService", lambda: FakeNotify())

    request = _make_request(f"/panel/support/{ticket.id}/reply")

    first = await support_routes.reply_ticket(
        ticket_id=ticket.id,
        request=request,
        text="One message only",
        notify_user="on",
        db=session,
    )
    second = await support_routes.reply_ticket(
        ticket_id=ticket.id,
        request=request,
        text="One message only",
        notify_user="on",
        db=session,
    )

    count_result = await session.execute(
        select(func.count(TicketMessage.id)).where(TicketMessage.ticket_id == ticket.id)
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert count_result.scalar_one() == 1
    assert len(sent) == 1
    toast_payload = json.loads(second.headers["HX-Trigger"])
    assert toast_payload["showToast"]["type"] == "info"


def test_platega_status_helpers_cover_documented_and_common_variants():
    assert PlategaService.is_success_status("CONFIRMED") is True
    assert PlategaService.is_success_status("confirmed") is True
    assert PlategaService.is_success_status("paid") is True
    assert PlategaService.is_failure_status("CANCELED") is True
    assert PlategaService.is_failure_status("cancelled") is True
    assert PlategaService.is_failure_status("CHARGEBACKED") is True


def test_encryption_accepts_valid_fernet_key(monkeypatch):
    key = encryption_service.generate_key()
    monkeypatch.setenv("ENCRYPTION_KEY", key)
    monkeypatch.setattr(encryption_service, "_FERNET", None)
    monkeypatch.setattr(encryption_service, "_MASTER_KEY", None)

    encrypted = encryption_service.encrypt_value("secret-value")

    assert encryption_service.decrypt_value(encrypted) == "secret-value"
    assert encryption_service.get_encryption_key_info().startswith("Configured")


def test_encryption_falls_back_without_crashing_on_invalid_key(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", "!" * 44)
    monkeypatch.setattr(encryption_service, "_FERNET", None)
    monkeypatch.setattr(encryption_service, "_MASTER_KEY", None)

    encrypted = encryption_service.encrypt_value("safe-fallback")

    assert encryption_service.decrypt_value(encrypted) == "safe-fallback"
    assert encryption_service.get_encryption_key_info().startswith("Auto-generated")


@pytest.mark.asyncio
async def test_bot_settings_encrypts_yookassa_secret_override(session):
    service = BotSettingsService(session)

    await service.set("yookassa_secret_key_override", "yk-secret-value")
    await session.commit()

    row = (
        await session.execute(
            select(BotSettings).where(BotSettings.key == "yookassa_secret_key_override")
        )
    ).scalar_one()

    assert row.value != "yk-secret-value"
    assert await service.get("yookassa_secret_key_override") == "yk-secret-value"


def test_user_notify_prunes_stale_cache_entries():
    now = time.time()
    cache = {
        user_id: now - (user_notify_middleware._EXPIRED_COOLDOWN * 10)
        for user_id in range(user_notify_middleware._NOTIFY_CACHE_PRUNE_THRESHOLD)
    }
    cache[user_notify_middleware._NOTIFY_CACHE_PRUNE_THRESHOLD + 1] = now - 60

    user_notify_middleware._prune_notification_cache(
        cache,
        now,
        user_notify_middleware._EXPIRED_COOLDOWN,
    )

    assert 1 not in cache
    assert user_notify_middleware._NOTIFY_CACHE_PRUNE_THRESHOLD + 1 in cache


@pytest.mark.asyncio
async def test_subscriptions_page_includes_expired_and_revoked_keys(
    session, sample_user, sample_plan, sample_vpn_key, monkeypatch
):
    expired_key = VpnKey(
        user_id=sample_user.id,
        plan_id=sample_plan.id,
        pasarguard_key_id="vpn_123456789_2",
        access_url="https://example.com/sub/expired",
        name="Expired Key",
        price=Decimal("10.00"),
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        status=VpnKeyStatus.EXPIRED.value,
    )
    revoked_key = VpnKey(
        user_id=sample_user.id,
        plan_id=sample_plan.id,
        pasarguard_key_id="vpn_123456789_3",
        access_url="https://example.com/sub/revoked",
        name="Revoked Key",
        price=Decimal("10.00"),
        expires_at=datetime.now(timezone.utc) - timedelta(days=2),
        status=VpnKeyStatus.REVOKED.value,
    )
    session.add_all([expired_key, revoked_key])
    await session.commit()

    monkeypatch.setattr(
        subscriptions_routes,
        "_require_permission",
        lambda request, permission: {"sub": "admin", "role": "superadmin"},
    )

    async def fake_base_ctx(request, db, active, admin_info=None):
        return {"request": request, "active": active, "admin_role": "superadmin"}

    async def fake_refresh(self, keys):
        for key in keys:
            setattr(key, "panel_status_raw", key.status)

    monkeypatch.setattr(subscriptions_routes, "_base_ctx", fake_base_ctx)
    monkeypatch.setattr(
        subscriptions_routes.VpnKeyService, "refresh_traffic_for_keys", fake_refresh
    )
    monkeypatch.setitem(
        subscriptions_routes.templates.env.globals, "has_perm", lambda role, perm: True
    )

    response = await subscriptions_routes.subscriptions_page(
        request=_make_request("/panel/subscriptions"),
        db=session,
    )

    body = response.body.decode("utf-8")
    assert response.status_code == 200
    assert f"#{sample_vpn_key.id}" in body
    assert f"#{expired_key.id}" in body
    assert f"#{revoked_key.id}" in body
    assert "Истекла" in body
    assert "Отозвана" in body


@pytest.mark.asyncio
async def test_user_detail_page_shows_language_and_registration_date(
    session, sample_user, sample_plan, sample_vpn_key, sample_referral, monkeypatch
):
    sample_user.autorenew = True
    sample_user.created_at = datetime(2026, 5, 1, 12, 30, tzinfo=timezone.utc)
    sample_user.last_seen = datetime(2026, 5, 2, 8, 45, tzinfo=timezone.utc)
    assert sample_vpn_key.status == VpnKeyStatus.ACTIVE.value
    assert isinstance(sample_referral, Referral)
    payment = Payment(
        user_id=sample_user.id,
        provider=PaymentProvider.YOOKASSA.value,
        payment_type=PaymentType.SUBSCRIPTION.value,
        amount=Decimal("199.00"),
        currency="RUB",
        status=PaymentStatus.SUCCEEDED.value,
        created_at=datetime(2026, 5, 3, 18, 20, tzinfo=timezone.utc),
    )
    session.add(payment)
    await session.commit()

    monkeypatch.setattr(
        users_routes,
        "_require_permission",
        lambda request, permission: {"sub": "admin", "role": "superadmin"},
    )

    async def fake_base_ctx(request, db, active, admin_info=None):
        return {"request": request, "active": active, "admin_role": "superadmin"}

    monkeypatch.setattr(users_routes, "_base_ctx", fake_base_ctx)
    monkeypatch.setitem(
        users_routes.templates.env.globals, "has_perm", lambda role, perm: True
    )

    response = await users_routes.user_detail_page(
        user_id=sample_user.id,
        request=_make_request(f"/panel/users/{sample_user.id}"),
        db=session,
    )

    body = response.body.decode("utf-8")
    user_stats = response.context["user_stats"]
    assert response.status_code == 200
    assert user_stats["active_subscriptions_count"] == 1
    assert user_stats["successful_payments_count"] == 1
    assert user_stats["referrals_count"] == 1
    assert user_stats["total_spent"] == Decimal("199.00")
    assert "Язык:" in body
    assert ">ru<" in body
    assert "Регистрация:" in body
    assert "01.05.2026 12:30" in body
    assert "Последняя активность:" in body
    assert "02.05.2026 08:45" in body
    assert "Автопродление:" in body
    assert "Вкл" in body
    assert "Успешные платежи" in body
    assert "Активные подписки" in body
    assert "Рефералы" in body
    assert "Потрачено" in body
    assert "199.00 ₽" in body
    assert "Последний успешный платёж" in body
    assert "03.05.2026 18:20" in body


@pytest.mark.asyncio
async def test_bot_provision_and_notify_sends_fallback_message_when_key_not_ready(
    session, sample_user, sample_plan, monkeypatch
):
    payment = Payment(
        user_id=sample_user.id,
        provider=PaymentProvider.YOOKASSA.value,
        payment_type=PaymentType.SUBSCRIPTION.value,
        amount=sample_plan.price,
        currency="RUB",
        status=PaymentStatus.SUCCEEDED.value,
        external_id="yk_ready_later",
    )
    session.add(payment)
    await session.commit()

    class _SessionCtx:
        def __init__(self, db_session):
            self._db_session = db_session

        async def __aenter__(self):
            return self._db_session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _Bot:
        def __init__(self):
            self.messages: list[tuple[int, str]] = []

        async def send_message(self, chat_id, text, parse_mode="HTML"):
            self.messages.append((chat_id, text))

    async def fake_get_all(self):
        return {}

    async def fake_provision(self, user_id: int, plan):
        return None

    monkeypatch.setattr(
        bot_payments, "AsyncSessionFactory", lambda: _SessionCtx(session)
    )
    monkeypatch.setattr(bot_payments.BotSettingsService, "get_all", fake_get_all)
    monkeypatch.setattr(bot_payments.VpnKeyService, "provision", fake_provision)

    bot = _Bot()
    ok = await bot_payments._provision_and_notify(
        sample_user.id,
        payment.id,
        sample_plan.id,
        bot,
        force_notify=True,
    )

    assert ok is True
    assert bot.messages
    assert "готовится" in bot.messages[0][1]


@pytest.mark.asyncio
async def test_plan_service_update_allows_clearing_description(session, sample_plan):
    updated = await PlanService(session).update(
        sample_plan.id,
        name="Updated Plan",
        description=None,
    )
    await session.commit()

    assert updated is not None
    assert updated.name == "Updated Plan"
    assert updated.description is None


@pytest.mark.asyncio
async def test_cancel_subscription_notifies_user(session, sample_vpn_key, monkeypatch):
    sent: list[tuple[int, str]] = []

    class _Notify:
        async def send_message(self, chat_id, text):
            sent.append((chat_id, text))
            return True

    monkeypatch.setattr(
        subscriptions_routes,
        "_require_permission",
        lambda request, permission: {"sub": "admin", "role": "superadmin"},
    )
    monkeypatch.setattr(
        subscriptions_routes, "TelegramNotifyService", lambda: _Notify()
    )

    response = await subscriptions_routes.cancel_subscription(
        key_id=sample_vpn_key.id,
        request=_make_request(f"/panel/subscriptions/{sample_vpn_key.id}/cancel"),
        db=session,
    )

    await session.refresh(sample_vpn_key)

    assert response.status_code == 200
    assert sample_vpn_key.status == VpnKeyStatus.EXPIRED.value
    assert sent == [
        (
            sample_vpn_key.user_id,
            "⚠️ <b>Подписка остановлена администратором.</b>\n\nЕсли это произошло по ошибке, напишите в поддержку.",
        )
    ]
