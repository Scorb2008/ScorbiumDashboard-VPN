import base64
import binascii
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bot_settings import BotSettings
from app.models.branding_asset import BrandingAsset


LOGO_ASSET_KEY = "custom_logo"
LOGO_ROUTE_PATH = "/panel/logo/current"


class BrandingAssetService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _parse_data_uri(value: str | None) -> tuple[str, bytes] | None:
        if not value or not isinstance(value, str) or not value.startswith("data:"):
            return None
        try:
            header, encoded = value.split(",", 1)
        except ValueError:
            return None
        if ";base64" not in header:
            return None
        mime_type = header[5:].split(";", 1)[0].strip() or "application/octet-stream"
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            return None
        return mime_type, data

    async def _get_asset(self, key: str) -> Optional[BrandingAsset]:
        result = await self.session.execute(
            select(BrandingAsset).where(BrandingAsset.key == key)
        )
        return result.scalar_one_or_none()

    async def _get_legacy_setting(self, key: str) -> str:
        result = await self.session.execute(
            select(BotSettings).where(BotSettings.key == key)
        )
        row = result.scalar_one_or_none()
        return row.value if row and row.value else ""

    async def get_logo_payload(self) -> tuple[str, bytes] | None:
        asset = await self._get_asset(LOGO_ASSET_KEY)
        if asset:
            return asset.mime_type, bytes(asset.data)

        legacy_logo = await self._get_legacy_setting(LOGO_ASSET_KEY)
        return self._parse_data_uri(legacy_logo)

    async def get_logo_url(self) -> str:
        payload = await self.get_logo_payload()
        return LOGO_ROUTE_PATH if payload else ""

    async def save_logo(self, raw: bytes, mime_type: str) -> None:
        asset = await self._get_asset(LOGO_ASSET_KEY)
        if asset:
            asset.mime_type = mime_type
            asset.data = raw
        else:
            self.session.add(
                BrandingAsset(key=LOGO_ASSET_KEY, mime_type=mime_type, data=raw)
            )

        result = await self.session.execute(
            select(BotSettings).where(BotSettings.key == LOGO_ASSET_KEY)
        )
        legacy_row = result.scalar_one_or_none()
        if legacy_row:
            legacy_row.value = ""

        await self.session.flush()

    async def clear_logo(self) -> None:
        asset = await self._get_asset(LOGO_ASSET_KEY)
        if asset:
            await self.session.delete(asset)

        result = await self.session.execute(
            select(BotSettings).where(BotSettings.key == LOGO_ASSET_KEY)
        )
        legacy_row = result.scalar_one_or_none()
        if legacy_row:
            legacy_row.value = ""

        await self.session.flush()
