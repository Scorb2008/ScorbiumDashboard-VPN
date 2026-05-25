from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.vpn_key import VpnKeyService


@pytest.mark.asyncio
async def test_extend_expired_key_uses_current_time_as_base(
    session, sample_vpn_key, monkeypatch
):
    sample_vpn_key.expires_at = datetime.now(timezone.utc) - timedelta(days=10)
    await session.commit()

    panel = SimpleNamespace(
        extend_user=AsyncMock(return_value={"username": sample_vpn_key.pasarguard_key_id})
    )
    monkeypatch.setattr(VpnKeyService, "_get_panel", lambda self: panel)

    before = datetime.now(timezone.utc)
    key = await VpnKeyService(session).extend(sample_vpn_key.id, 7)
    after = datetime.now(timezone.utc)

    assert key is not None
    expires_at = key.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    assert before + timedelta(days=7) <= expires_at <= after + timedelta(days=7)
    panel.extend_user.assert_awaited_once_with(sample_vpn_key.pasarguard_key_id, 7)
