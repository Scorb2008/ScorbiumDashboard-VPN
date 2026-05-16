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
async def test_refresh_traffic_for_keys_uses_pasarguard_payload(session, sample_vpn_key):
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
async def test_sync_from_marzban_maps_normalized_pasarguard_traffic(session, sample_vpn_key):
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

    assert result == {"synced": 1, "errors": 0}
    assert sample_vpn_key.download == 4096
    assert sample_vpn_key.upload == 0
