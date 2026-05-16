import base64

import pytest

from app.models.bot_settings import BotSettings
from app.services.branding_asset import BrandingAssetService, LOGO_ROUTE_PATH


@pytest.mark.asyncio
async def test_branding_asset_service_saves_and_clears_logo(session):
    service = BrandingAssetService(session)
    payload = b"\x89PNG\r\n\x1a\nfake"

    await service.save_logo(payload, "image/png")
    await session.commit()

    logo = await service.get_logo_payload()
    assert logo == ("image/png", payload)
    assert await service.get_logo_url() == LOGO_ROUTE_PATH

    await service.clear_logo()
    await session.commit()

    assert await service.get_logo_payload() is None
    assert await service.get_logo_url() == ""


@pytest.mark.asyncio
async def test_branding_asset_service_reads_legacy_data_uri_from_bot_settings(session):
    payload = b"legacy-logo"
    session.add(
        BotSettings(
            key="custom_logo",
            value=f"data:image/png;base64,{base64.b64encode(payload).decode()}",
        )
    )
    await session.commit()

    service = BrandingAssetService(session)

    assert await service.get_logo_payload() == ("image/png", payload)
    assert await service.get_logo_url() == LOGO_ROUTE_PATH
