from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

from app.models.plan import Plan
from app.models.user import User
from app.models.vpn_key import VpnKey, VpnKeyStatus
from app.tasks import vpn_tasks


class _SessionFactory:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


async def test_auto_renew_keys_skips_revoked_keys(session, monkeypatch):
    user = User(
        id=424242,
        username="revoked-user",
        full_name="Revoked User",
        balance=Decimal("150.00"),
        autorenew=True,
    )
    plan = Plan(
        id=42,
        name="Auto Renew Plan",
        slug="auto-renew-plan",
        duration_days=30,
        price=Decimal("99.00"),
        is_active=True,
    )
    key = VpnKey(
        user_id=user.id,
        plan_id=plan.id,
        pasarguard_key_id="vpn_424242_1",
        access_url="https://example.com/sub/revoked",
        name="Revoked Key",
        price=plan.price,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        status=VpnKeyStatus.REVOKED.value,
    )
    session.add_all([user, plan, key])
    await session.commit()

    monkeypatch.setattr(
        vpn_tasks, "AsyncSessionFactory", lambda: _SessionFactory(session)
    )
    monkeypatch.setattr(
        "app.services.bot_settings.BotSettingsService.get",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.telegram_notify.TelegramNotifyService.send_message",
        AsyncMock(),
    )
    extend_mock = AsyncMock()
    monkeypatch.setattr("app.services.vpn_key.VpnKeyService.extend", extend_mock)

    await vpn_tasks.auto_renew_keys()
    await session.refresh(user)
    await session.refresh(key)

    assert user.balance == Decimal("150.00")
    assert key.status == VpnKeyStatus.REVOKED.value
    extend_mock.assert_not_awaited()
