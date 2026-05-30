from datetime import datetime, timedelta, timezone
from typing import Optional
import asyncio

from sqlalchemy import func, inspect, select
from sqlalchemy.orm import undefer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import config
from app.core.database import AsyncSessionFactory
from app.models.plan import Plan
from app.models.vpn_key import VpnKey, VpnKeyStatus
from app.services.pasarguard.pasarguard import get_vpn_panel
from app.services.vpn_panel_interface import VpnPanelInterface
from app.utils.log import log


def _marzban_username(user_id: int, key_id: int) -> str:
    return f"vpn_{user_id}_{key_id}"


class VpnKeyService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._marzban: Optional[VpnPanelInterface] = None
        self._traffic_columns_supported: Optional[bool] = None

    def _get_panel(self) -> VpnPanelInterface:
        """Lazy init VPN panel — не падает при старте если панель не сконфигурирована."""
        if self._marzban is None:
            self._marzban = get_vpn_panel()
        return self._marzban

    async def _supports_traffic_columns(self) -> bool:
        if self._traffic_columns_supported is not None:
            return self._traffic_columns_supported

        conn = await self.session.connection()

        def _inspect_columns(sync_conn) -> bool:
            columns = {
                col["name"] for col in inspect(sync_conn).get_columns("vpn_keys")
            }
            return {"download", "upload"}.issubset(columns)

        self._traffic_columns_supported = await conn.run_sync(_inspect_columns)
        return self._traffic_columns_supported

    async def get_by_id(self, key_id: int) -> Optional[VpnKey]:
        result = await self.session.execute(select(VpnKey).where(VpnKey.id == key_id))
        return result.scalar_one_or_none()

    async def get_by_id_for_update(self, key_id: int) -> Optional[VpnKey]:
        result = await self.session.execute(
            select(VpnKey).where(VpnKey.id == key_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_active_for_user(self, user_id: int) -> list[VpnKey]:
        result = await self.session.execute(
            select(VpnKey)
            .options(
                undefer(VpnKey.download),
                undefer(VpnKey.upload),
            )
            .where(
                VpnKey.user_id == user_id,
                VpnKey.status == VpnKeyStatus.ACTIVE.value,
            )
            .order_by(VpnKey.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_user_keys(self, user_id: int) -> list[VpnKey]:
        return await self.get_active_for_user(user_id)

    async def get_all_for_user(self, user_id: int) -> list[VpnKey]:
        result = await self.session.execute(
            select(VpnKey)
            .options(
                undefer(VpnKey.download),
                undefer(VpnKey.upload),
            )
            .where(VpnKey.user_id == user_id)
            .order_by(VpnKey.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_all(self, limit: int = 200) -> list[VpnKey]:
        result = await self.session.execute(
            select(VpnKey).order_by(VpnKey.id.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def refresh_traffic_for_keys(self, keys: list[VpnKey]) -> None:
        if not keys:
            return
        traffic_columns_supported = await self._supports_traffic_columns()

        for key in keys:
            setattr(key, "panel_status_raw", None)
            if not key.pasarguard_key_id:
                continue
            try:
                marz_user = await self._get_panel().get_user(key.pasarguard_key_id)
                raw_status = (marz_user or {}).get("_normalized_status") or (
                    marz_user or {}
                ).get("status", "")
                setattr(
                    key,
                    "panel_status_raw",
                    str(raw_status).lower() if raw_status else None,
                )
                self._sync_key_status_from_panel(key, marz_user)
                if not marz_user or not traffic_columns_supported:
                    continue
                download = marz_user.get("download", 0) or 0
                upload = marz_user.get("upload", 0) or 0
                key.download = download if isinstance(download, int) else int(download)
                key.upload = upload if isinstance(upload, int) else int(upload)
            except Exception as e:
                log.warning(f"Traffic refresh error key {key.id}: {e}")

    def _sync_key_status_from_panel(self, key: VpnKey, marz_user: dict | None) -> None:
        if not marz_user:
            key.status = VpnKeyStatus.REVOKED.value
            return

        raw_status = (
            marz_user.get("_normalized_status") or marz_user.get("status", "")
        ).lower()

        if raw_status == "active":
            key.status = VpnKeyStatus.ACTIVE.value
            return

        if raw_status in ("expired", "limited", "disabled", "revoked"):
            key.status = VpnKeyStatus.EXPIRED.value

    @staticmethod
    def _normalize_expire_datetime(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _expire_timestamp(expire_at: datetime | None) -> int | None:
        expire_at = VpnKeyService._normalize_expire_datetime(expire_at)
        if expire_at is None:
            return None
        return int(expire_at.timestamp())

    @staticmethod
    def _parse_expire_datetime(raw_expire: object, now: datetime) -> datetime | None:
        if raw_expire is None:
            return None

        try:
            value = str(raw_expire).strip()
            if not value or value.lower() == "none":
                return None

            if value.isdigit():
                ts = int(value)
                return datetime.fromtimestamp(ts, tz=timezone.utc) if ts > 0 else now

            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                ts = float(value)
                parsed = datetime.fromtimestamp(ts, tz=timezone.utc) if ts > 0 else now

            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except Exception as e:
            log.warning(f"[vpn_sync] failed to parse expire {raw_expire!r}: {e}")
            return now

    async def _sync_panel_expire_from_db(
        self, key: VpnKey, marz_user: dict | None
    ) -> bool:
        if not key.pasarguard_key_id or key.expires_at is None or not marz_user:
            return False

        if "expire" not in marz_user:
            return False

        panel_expire = self._parse_expire_datetime(
            marz_user.get("expire"),
            datetime.now(timezone.utc),
        )
        db_expire = self._normalize_expire_datetime(key.expires_at)
        if db_expire is None:
            return False

        if panel_expire is None:
            return False

        delta = abs((panel_expire - db_expire).total_seconds())
        if delta <= 60:
            return False

        panel = self._get_panel()
        modify_user = getattr(panel, "modify_user", None)
        if not callable(modify_user):
            return False

        expire_ts = self._expire_timestamp(db_expire)
        if expire_ts is None:
            return False

        await modify_user(key.pasarguard_key_id, expire=expire_ts)
        log.info(
            f"[vpn_sync] fixed Pasarguard expire for key {key.id}: {db_expire.isoformat()}"
        )
        return True

    async def count_active(self) -> int:
        result = await self.session.execute(
            select(func.count()).where(VpnKey.status == VpnKeyStatus.ACTIVE.value)
        )
        return result.scalar_one()

    async def provision(self, user_id: int, plan: Plan) -> Optional[VpnKey]:
        """Provision VPN key with retry logic and rollback on failure."""
        from app.services.bot_settings import BotSettingsService

        async with AsyncSessionFactory() as check_session:
            if await BotSettingsService(check_session).is_maintenance_mode():
                log.info(f"Provision blocked: maintenance mode for user {user_id}")
                return None

        expires_at = datetime.now(timezone.utc) + timedelta(days=plan.duration_days)
        key = VpnKey(
            user_id=user_id,
            plan_id=plan.id,
            price=plan.price,
            expires_at=expires_at,
            name=f"{plan.name} — {plan.duration_days} дн.",
            status=VpnKeyStatus.ACTIVE.value,
            access_url="pending",
        )
        self.session.add(key)
        await self.session.flush()

        username = _marzban_username(user_id, key.id)

        marz_user = await self._create_in_marzban(
            username, expire_days=plan.duration_days
        )
        if marz_user is None:
            await self.session.delete(key)
            await self.session.flush()
            return None

        self._set_access_url(key, marz_user, username)
        await self.session.flush()
        log.info(f"VPN provisioned: user={user_id} key={key.id} marzban={username}")
        return key

    async def _create_in_marzban(
        self, username: str, expire_days: int, data_limit_gb: int = 0
    ) -> dict | None:
        """Create user in Marzban with retry logic and group_ids. Returns marz_user dict or None."""
        from app.services.bot_settings import BotSettingsService, parse_int_list_setting

        group_ids: list[int] = []
        try:
            raw_groups = await BotSettingsService(self.session).get("vpn_group_ids")
            if raw_groups:
                group_ids = parse_int_list_setting(raw_groups)
        except Exception as e:
            log.warning(f"Failed to load vpn_group_ids setting: {e}")

        last_error = None
        for attempt in range(3):
            try:
                marz_user = await self._get_panel().create_user(
                    username=username,
                    expire_days=expire_days,
                    data_limit_gb=data_limit_gb,
                    group_ids=group_ids or None,
                )
                log.info(f"Marzban provisioned {username} (attempt {attempt + 1})")
                return marz_user
            except Exception as e:
                last_error = e
                log.warning(
                    f"Marzban attempt {attempt + 1}/3 failed for {username}: {e}"
                )
                if attempt < 2:
                    await asyncio.sleep(0.5 * (attempt + 1))
        log.error(f"All 3 Marzban attempts failed for {username}: {last_error}")
        return None

    def _set_access_url(self, key: VpnKey, marz_user: dict, username: str) -> None:
        """Set access_url on a VpnKey from marzban response."""
        sub_token = marz_user.get("subscription_url", "")
        _pg = config.pasarguard
        panel_base = str(_pg.pasarguard_admin_panel).rstrip("/") if _pg else ""
        if sub_token:
            if sub_token.startswith("http"):
                access_url = sub_token.rstrip("/")
            else:
                access_url = f"{panel_base}{sub_token.rstrip('/')}"
        else:
            access_url = f"{panel_base}/sub/{username}"
        key.pasarguard_key_id = username
        key.access_url = access_url

    async def provision_days(
        self, user_id: int, days: int, name: str = None
    ) -> Optional[VpnKey]:
        """Create a VPN key with arbitrary days (no plan required)."""
        from app.services.bot_settings import BotSettingsService

        async with AsyncSessionFactory() as check_session:
            if await BotSettingsService(check_session).is_maintenance_mode():
                log.info(f"Provision days blocked: maintenance mode for user {user_id}")
                return None

        expires_at = datetime.now(timezone.utc) + timedelta(days=days)
        key_name = name or f"Подарок — {days} дн."
        key = VpnKey(
            user_id=user_id,
            plan_id=None,
            price=0,
            expires_at=expires_at,
            name=key_name,
            status=VpnKeyStatus.ACTIVE.value,
            access_url="pending",
        )
        self.session.add(key)
        await self.session.flush()

        username = _marzban_username(user_id, key.id)
        marz_user = await self._create_in_marzban(username, expire_days=days)
        if marz_user is None:
            await self.session.delete(key)
            await self.session.flush()
            return None

        self._set_access_url(key, marz_user, username)
        await self.session.flush()
        log.info(f"VPN provisioned (days): user={user_id} key={key.id} days={days}")
        return key

    async def provision_for_subscription(
        self, user_id: int, subscription_id: int, plan: Plan
    ) -> Optional[VpnKey]:
        return await self.provision(user_id, plan)

    # ── Management ───────────────────────────────────────────────────────────

    async def revoke(self, key_id: int) -> Optional[VpnKey]:
        key = await self.get_by_id_for_update(key_id)
        if not key:
            return None
        if key.pasarguard_key_id:
            try:
                await self._get_panel().disable_user(key.pasarguard_key_id)
            except Exception as e:
                log.warning(f"Marzban disable failed: {e}")
        key.status = VpnKeyStatus.REVOKED.value
        await self.session.flush()
        return key

    async def extend(self, key_id: int, days: int) -> Optional[VpnKey]:
        key = await self.get_by_id_for_update(key_id)
        if not key:
            return None
        if key.pasarguard_key_id:
            try:
                await self._get_panel().extend_user(key.pasarguard_key_id, days)
            except Exception as e:
                log.warning(f"Marzban extend failed: {e}")
                return None

        now = datetime.now(timezone.utc)
        base_expires_at = now
        if key.expires_at:
            expires_at = key.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at > now:
                base_expires_at = expires_at

        key.expires_at = base_expires_at + timedelta(days=days)
        key.status = VpnKeyStatus.ACTIVE.value
        await self.session.flush()
        return key

    async def delete_from_marzban(self, key_id: int) -> Optional[VpnKey]:
        key = await self.get_by_id_for_update(key_id)
        if not key:
            return None
        if key.pasarguard_key_id:
            try:
                await self._get_panel().delete_user(key.pasarguard_key_id)
            except Exception as e:
                log.warning(f"Marzban delete failed: {e}")
        key.status = VpnKeyStatus.REVOKED.value
        await self.session.flush()
        return key

    async def revoke_all_for_user(self, user_id: int) -> int:
        keys = await self.get_active_for_user(user_id)
        for key in keys:
            if key.pasarguard_key_id:
                try:
                    await self._get_panel().disable_user(key.pasarguard_key_id)
                except Exception:
                    pass
            key.status = VpnKeyStatus.REVOKED.value
        await self.session.flush()
        return len(keys)

    async def sync_from_marzban(self) -> dict:
        synced, errors, fixed_expire = 0, 0, 0
        traffic_columns_supported = await self._supports_traffic_columns()
        result = await self.session.execute(
            select(VpnKey).where(
                VpnKey.status == VpnKeyStatus.ACTIVE.value,
                VpnKey.pasarguard_key_id.isnot(None),
            )
        )
        for key in result.scalars().all():
            try:
                marz_user = await self._get_panel().get_user(key.pasarguard_key_id)
                if not marz_user:
                    key.status = VpnKeyStatus.REVOKED.value
                else:
                    if await self._sync_panel_expire_from_db(key, marz_user):
                        fixed_expire += 1
                    raw_status = (
                        marz_user.get("_normalized_status")
                        or marz_user.get("status", "")
                    ).lower()
                    if raw_status in ("expired", "limited", "disabled"):
                        key.status = VpnKeyStatus.EXPIRED.value
                    if traffic_columns_supported:
                        download = marz_user.get("download", 0) or 0
                        upload = marz_user.get("upload", 0) or 0
                        key.download = (
                            download if isinstance(download, int) else int(download)
                        )
                        key.upload = upload if isinstance(upload, int) else int(upload)
                synced += 1
            except Exception as e:
                log.warning(f"Sync error key {key.id}: {e}")
                errors += 1
        await self.session.flush()
        return {"synced": synced, "errors": errors, "fixed_expire": fixed_expire}

    async def expire_outdated(self) -> int:
        now = datetime.now(timezone.utc)
        result = await self.session.execute(
            select(VpnKey).where(
                VpnKey.status == VpnKeyStatus.ACTIVE.value,
                VpnKey.expires_at < now,
            )
        )
        keys = list(result.scalars().all())
        for key in keys:
            key.status = VpnKeyStatus.EXPIRED.value
            if key.pasarguard_key_id:
                try:
                    await self._get_panel().disable_user(key.pasarguard_key_id)
                except Exception:
                    pass
        await self.session.flush()
        return len(keys)
