from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.vpn_key import VpnKey
from app.models.plan import Plan
from app.services.vpn_key import VpnKeyService


class SubscriptionService:
    """Business logic for VPN subscriptions."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._key_svc = VpnKeyService(session)

    async def get_user_subscription(self, user_id: int) -> Optional[VpnKey]:
        """Get active subscription for user."""
        return await self._key_svc.get_active_for_user(user_id)

    async def extend_subscription(
        self, user_id: int, plan: Plan
    ) -> Optional[VpnKey]:
        """Extend user subscription by plan duration."""
        keys = await self._key_svc.get_active_for_user(user_id)
        if not keys:
            return None
        # Extend the first active key
        return await self._key_svc.extend(keys[0].id, plan.duration_days)

    async def cancel_subscription(self, user_id: int) -> int:
        """Cancel all active subscriptions for user."""
        return await self._key_svc.revoke_all_for_user(user_id)
