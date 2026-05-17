from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.broadcast import Broadcast, BroadcastStatus
from app.models.user import User
from app.models.vpn_key import VpnKey, VpnKeyStatus
from app.services.telegram_notify import TelegramNotifyService
from app.utils.log import log


class BroadcastService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_all(self, limit: int = 50, offset: int = 0) -> list[Broadcast]:
        result = await self.session.execute(
            select(Broadcast).order_by(Broadcast.created_at.desc()).limit(limit).offset(offset)
        )
        return list(result.scalars().all())

    async def get_by_id(self, broadcast_id: int) -> Broadcast | None:
        result = await self.session.execute(select(Broadcast).where(Broadcast.id == broadcast_id))
        return result.scalar_one_or_none()

    async def create(self, title: str, text: str, target: str = "all", parse_mode: str = "HTML") -> Broadcast:
        bc = Broadcast(title=title, text=text, target=target, parse_mode=parse_mode)
        self.session.add(bc)
        await self.session.flush()
        return bc

    async def send(self, broadcast_id: int) -> Broadcast | None:
        bc = await self.get_by_id(broadcast_id)
        if not bc or bc.status not in (BroadcastStatus.DRAFT.value, BroadcastStatus.FAILED.value):
            return None

        bc.status = BroadcastStatus.SENDING.value
        await self.session.flush()

        user_ids = await self._resolve_targets(bc.target)
        notify = TelegramNotifyService()
        sent, failed = await notify.broadcast(user_ids, bc.text, bc.parse_mode)

        bc.sent_count = sent
        bc.failed_count = failed
        if not user_ids:
            bc.status = BroadcastStatus.DONE.value
        elif failed == 0:
            bc.status = BroadcastStatus.DONE.value
        else:
            bc.status = BroadcastStatus.FAILED.value
        await self.session.flush()

        log.info(f"Broadcast {broadcast_id}: sent={sent} failed={failed}")
        return bc

    async def _resolve_targets(self, target: str) -> list[int]:
        if target == "all":
            result = await self.session.execute(
                select(User.id).where(User.is_banned.is_(False), User.is_active.is_(True))
            )
        elif target == "active":
            result = await self.session.execute(
                select(VpnKey.user_id)
                .where(VpnKey.status == VpnKeyStatus.ACTIVE.value)
                .distinct()
            )
        elif target == "expired":
            result = await self.session.execute(
                select(VpnKey.user_id)
                .where(VpnKey.status == VpnKeyStatus.EXPIRED.value)
                .distinct()
            )
        else:
            return []
        return [row[0] for row in result.all()]

    async def estimate_count(self, target: str) -> int:
        if target == "all":
            result = await self.session.execute(
                select(func.count(User.id)).where(User.is_banned.is_(False), User.is_active.is_(True))
            )
        elif target == "active":
            result = await self.session.execute(
                select(func.count(func.distinct(VpnKey.user_id)))
                .where(VpnKey.status == VpnKeyStatus.ACTIVE.value)
            )
        elif target == "expired":
            result = await self.session.execute(
                select(func.count(func.distinct(VpnKey.user_id)))
                .where(VpnKey.status == VpnKeyStatus.EXPIRED.value)
            )
        else:
            return 0
        return result.scalar() or 0
