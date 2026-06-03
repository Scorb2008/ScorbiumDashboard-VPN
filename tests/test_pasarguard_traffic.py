from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.pasarguard.pasarguard import PasarguardService
from app.services.vpn_key import VpnKeyService
from app.models.vpn_key import VpnKeyStatus


def test_pasarguard_normalizes_used_traffic_into_download_upload():
    service = PasarguardService.__new__(PasarguardService)

    normalized = service._normalize_user_payload(
        {
            "username": "vpn_1_1",
            "used_traffic": 12345,
            "lifetime_used_traffic": 67890,
        }
    )

    assert normalized["download"] == 12345
    assert normalized["upload"] == 0
    assert normalized["used_traffic"] == 12345
    assert normalized["lifetime_used_traffic"] == 67890


@pytest.mark.asyncio
async def test_pasarguard_create_user_sends_unix_expire_timestamp():
    service = PasarguardService.__new__(PasarguardService)
    service._client = SimpleNamespace(
        post=AsyncMock(return_value={"username": "vpn_1_1"})
    )

    before = datetime.now(timezone.utc)
    await service.create_user("vpn_1_1", expire_days=30)
    after = datetime.now(timezone.utc)

    payload = service._client.post.await_args.args[1]
    assert isinstance(payload["expire"], int)
    assert int((before + timedelta(days=30)).timestamp()) <= payload["expire"]
    assert payload["expire"] <= int((after + timedelta(days=30)).timestamp())


@pytest.mark.asyncio
async def test_pasarguard_extend_user_preserves_future_expiry_with_unix_timestamp():
    service = PasarguardService.__new__(PasarguardService)
    now = datetime.now(timezone.utc)
    current_expire = int((now + timedelta(days=10)).timestamp())
    service.get_user = AsyncMock(return_value={"expire": current_expire})
    service.modify_user = AsyncMock(return_value={"username": "vpn_1_1"})

    await service.extend_user("vpn_1_1", 7)

    service.modify_user.assert_awaited_once_with(
        "vpn_1_1",
        expire=current_expire + 7 * 24 * 60 * 60,
    )


@pytest.mark.asyncio
async def test_refresh_traffic_for_keys_uses_pasarguard_payload(
    session, sample_vpn_key
):
    service = VpnKeyService(session)
    service._traffic_columns_supported = True
    panel = AsyncMock()
    panel.get_user.return_value = {
        "username": sample_vpn_key.pasarguard_key_id,
        "download": 222,
        "upload": 333,
    }
    service._marzban = panel

    await service.refresh_traffic_for_keys([sample_vpn_key])

    assert sample_vpn_key.download == 222
    assert sample_vpn_key.upload == 333
    panel.get_user.assert_awaited_once_with(sample_vpn_key.pasarguard_key_id)


@pytest.mark.asyncio
async def test_extend_keeps_local_expiry_unchanged_when_pasarguard_fails(
    session, sample_vpn_key
):
    service = VpnKeyService(session)
    panel = AsyncMock()
    panel.extend_user.side_effect = RuntimeError("panel unavailable")
    service._marzban = panel
    before = sample_vpn_key.expires_at

    result = await service.extend(sample_vpn_key.id, 7)

    assert result is None
    assert sample_vpn_key.expires_at == before


@pytest.mark.asyncio
async def test_refresh_traffic_for_keys_updates_stale_status_from_pasarguard(
    session, sample_vpn_key
):
    service = VpnKeyService(session)
    service._traffic_columns_supported = True
    sample_vpn_key.status = VpnKeyStatus.REVOKED.value

    panel = AsyncMock()
    panel.get_user.return_value = {
        "username": sample_vpn_key.pasarguard_key_id,
        "status": "active",
        "download": 1,
        "upload": 2,
    }
    service._marzban = panel

    await service.refresh_traffic_for_keys([sample_vpn_key])

    assert sample_vpn_key.status == VpnKeyStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_sync_from_marzban_maps_normalized_pasarguard_traffic(
    session, sample_vpn_key
):
    service = VpnKeyService(session)
    service._traffic_columns_supported = True
    panel = AsyncMock()
    panel.get_user.return_value = {
        "username": sample_vpn_key.pasarguard_key_id,
        "status": "active",
        "download": 4096,
        "upload": 0,
        "used_traffic": 4096,
    }
    service._marzban = panel

    result = await service.sync_from_marzban()

    assert result == {"synced": 1, "errors": 0, "fixed_expire": 0}
    assert sample_vpn_key.download == 4096
    assert sample_vpn_key.upload == 0


@pytest.mark.asyncio
async def test_sync_from_marzban_repairs_expire_without_panel_private_helpers(
    session, sample_vpn_key
):
    service = VpnKeyService(session)
    service._traffic_columns_supported = False
    original_expire = sample_vpn_key.expires_at
    panel_expire = sample_vpn_key.expires_at - timedelta(days=2)

    panel = AsyncMock()
    panel.get_user.return_value = {
        "username": sample_vpn_key.pasarguard_key_id,
        "status": "active",
        "expire": int(
            panel_expire.astimezone(timezone.utc).timestamp()
        ),
    }
    panel.modify_user = AsyncMock(return_value={"username": sample_vpn_key.pasarguard_key_id})
    service._marzban = panel

    result = await service.sync_from_marzban()

    assert result == {"synced": 1, "errors": 0, "fixed_expire": 1}
    assert service._expire_timestamp(sample_vpn_key.expires_at) == service._expire_timestamp(
        panel_expire
    )
    assert sample_vpn_key.expires_at != original_expire
    panel.modify_user.assert_not_awaited()
