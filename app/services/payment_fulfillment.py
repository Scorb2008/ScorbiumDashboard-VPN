from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payment import Payment, PaymentStatus, PaymentType
from app.models.plan import Plan
from app.models.vpn_key import VpnKey
from app.services.payment import PaymentService
from app.services.user import UserService
from app.services.vpn_key import VpnKeyService


@dataclass(frozen=True)
class TopupFulfillmentResult:
    payment: Optional[Payment]
    just_processed: bool
    balance: Optional[Decimal]


@dataclass(frozen=True)
class KeyFulfillmentResult:
    payment: Optional[Payment]
    key: Optional[VpnKey]
    just_processed: bool


class PaymentFulfillmentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._payment_svc = PaymentService(session)
        self._user_svc = UserService(session)
        self._key_svc = VpnKeyService(session)

    async def confirm_topup_and_credit_once(
        self, payment_id: int, external_id: str
    ) -> TopupFulfillmentResult:
        confirmation = await self._payment_svc.confirm_topup_once(
            payment_id, external_id
        )
        payment = confirmation.payment
        if not payment or payment.payment_type != PaymentType.TOPUP.value:
            return TopupFulfillmentResult(
                payment=payment, just_processed=False, balance=None
            )

        user = None
        if confirmation.just_confirmed:
            user = await self._user_svc.add_balance(payment.user_id, payment.amount)
            if not user:
                raise RuntimeError(f"Topup credit failed for payment {payment_id}")
        else:
            user = await self._user_svc.get_by_id(payment.user_id)

        balance = (
            Decimal(str(user.balance)) if user and user.balance is not None else None
        )
        return TopupFulfillmentResult(
            payment=payment,
            just_processed=confirmation.just_confirmed,
            balance=balance,
        )

    async def provision_subscription_once(
        self, payment_id: int, user_id: int, plan: Plan
    ) -> KeyFulfillmentResult:
        payment = await self._payment_svc.get_by_id_for_update(payment_id)
        if not payment or payment.user_id != user_id:
            return KeyFulfillmentResult(payment=payment, key=None, just_processed=False)
        if payment.payment_type != PaymentType.SUBSCRIPTION.value:
            return KeyFulfillmentResult(payment=payment, key=None, just_processed=False)
        if payment.status != PaymentStatus.SUCCEEDED.value:
            return KeyFulfillmentResult(payment=payment, key=None, just_processed=False)

        existing_key = await self._resolve_linked_key(payment)
        if existing_key:
            return KeyFulfillmentResult(
                payment=payment, key=existing_key, just_processed=False
            )

        key = await self._key_svc.provision(user_id=user_id, plan=plan)
        if key:
            payment.vpn_key_id = key.id
            await self.session.flush()
        return KeyFulfillmentResult(payment=payment, key=key, just_processed=bool(key))

    async def extend_subscription_once(
        self, payment_id: int, user_id: int, key_id: int, plan: Plan
    ) -> KeyFulfillmentResult:
        payment = await self._payment_svc.get_by_id_for_update(payment_id)
        if not payment or payment.user_id != user_id:
            return KeyFulfillmentResult(payment=payment, key=None, just_processed=False)
        if payment.payment_type != PaymentType.SUBSCRIPTION.value:
            return KeyFulfillmentResult(payment=payment, key=None, just_processed=False)
        if payment.status != PaymentStatus.SUCCEEDED.value:
            return KeyFulfillmentResult(payment=payment, key=None, just_processed=False)

        existing_key = await self._resolve_linked_key(payment)
        if existing_key:
            return KeyFulfillmentResult(
                payment=payment, key=existing_key, just_processed=False
            )

        target_key = await self._key_svc.get_by_id(key_id)
        if not target_key or target_key.user_id != user_id:
            return KeyFulfillmentResult(payment=payment, key=None, just_processed=False)

        key = await self._key_svc.extend(key_id, plan.duration_days)
        if key:
            payment.vpn_key_id = key.id
            await self.session.flush()
        return KeyFulfillmentResult(payment=payment, key=key, just_processed=bool(key))

    async def _resolve_linked_key(self, payment: Payment) -> Optional[VpnKey]:
        if not payment.vpn_key_id:
            return None
        key = await self._key_svc.get_by_id(payment.vpn_key_id)
        if key:
            return key
        payment.vpn_key_id = None
        await self.session.flush()
        return None
