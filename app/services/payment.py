from decimal import Decimal
from typing import Optional
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payment import Payment, PaymentProvider, PaymentStatus, PaymentType
from app.models.plan import Plan


class PaymentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, payment_id: int) -> Optional[Payment]:
        result = await self.session.execute(select(Payment).where(Payment.id == payment_id))
        return result.scalar_one_or_none()

    async def get_by_external_id(self, external_id: str) -> Optional[Payment]:
        result = await self.session.execute(
            select(Payment).where(Payment.external_id == external_id)
        )
        return result.scalar_one_or_none()

    async def get_all(
        self,
        limit: int = 100,
        offset: int = 0,
        status: Optional[PaymentStatus] = None,
        user_id: Optional[int] = None,
        payment_type: Optional[PaymentType] = None,
    ) -> list[Payment]:
        q = select(Payment).order_by(Payment.created_at.desc()).limit(limit).offset(offset)
        if status:
            q = q.where(Payment.status == status.value)
        if user_id:
            q = q.where(Payment.user_id == user_id)
        if payment_type:
            q = q.where(Payment.payment_type == payment_type.value)
        result = await self.session.execute(q)
        return list(result.scalars().all())

    async def total_revenue(self) -> Decimal:
        """Выручка только от подписок (не считаем пополнения баланса)."""
        result = await self.session.execute(
            select(func.sum(Payment.amount)).where(
                Payment.status == PaymentStatus.SUCCEEDED.value,
                Payment.payment_type == PaymentType.SUBSCRIPTION.value,
            )
        )
        val = result.scalar()
        return val if val is not None else Decimal("0")

    async def total_topups(self) -> Decimal:
        """Сумма всех пополнений баланса."""
        result = await self.session.execute(
            select(func.sum(Payment.amount)).where(
                Payment.status == PaymentStatus.SUCCEEDED.value,
                Payment.payment_type == PaymentType.TOPUP.value,
            )
        )
        val = result.scalar()
        return val if val is not None else Decimal("0")

    async def count_by_status(self, status: PaymentStatus) -> int:
        result = await self.session.execute(
            select(func.count()).where(Payment.status == status.value)
        )
        return result.scalar_one()

    async def create_pending(
        self,
        user_id: int,
        plan: Plan,
        provider: PaymentProvider,
        currency: str = "RUB",
    ) -> Payment:
        """Создать pending платёж за подписку."""
        from datetime import datetime, timezone, timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
        old_result = await self.session.execute(
            select(Payment).where(
                Payment.user_id == user_id,
                Payment.status == PaymentStatus.PENDING.value,
                Payment.provider == provider.value,
                Payment.payment_type == PaymentType.SUBSCRIPTION.value,
                Payment.created_at <= cutoff,
            )
        )
        for old in old_result.scalars().all():
            old.status = PaymentStatus.FAILED.value

        payment = Payment(
            user_id=user_id,
            provider=provider.value,
            payment_type=PaymentType.SUBSCRIPTION.value,
            amount=plan.price,
            currency=currency,
            status=PaymentStatus.PENDING.value,
        )
        self.session.add(payment)
        await self.session.flush()
        return payment

    async def create_topup_pending(
        self,
        user_id: int,
        amount: Decimal,
        provider: PaymentProvider,
        external_id: Optional[str] = None,
        currency: str = "RUB",
    ) -> Payment:
        """Создать pending платёж пополнения баланса."""
        from datetime import datetime, timezone, timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
        # Отменяем старые pending topup от того же провайдера
        old_result = await self.session.execute(
            select(Payment).where(
                Payment.user_id == user_id,
                Payment.status == PaymentStatus.PENDING.value,
                Payment.provider == provider.value,
                Payment.payment_type == PaymentType.TOPUP.value,
                Payment.created_at <= cutoff,
            )
        )
        for old in old_result.scalars().all():
            old.status = PaymentStatus.FAILED.value

        payment = Payment(
            user_id=user_id,
            provider=provider.value,
            payment_type=PaymentType.TOPUP.value,
            amount=amount,
            currency=currency,
            status=PaymentStatus.PENDING.value,
            external_id=external_id,
        )
        self.session.add(payment)
        await self.session.flush()
        return payment

    async def expire_old_pending(self, max_age_minutes: int = 15) -> int:
        from datetime import datetime, timezone, timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)
        result = await self.session.execute(
            select(Payment).where(
                Payment.status == PaymentStatus.PENDING.value,
                Payment.created_at <= cutoff,
            )
        )
        payments = result.scalars().all()
        count = 0
        for p in payments:
            p.status = PaymentStatus.FAILED.value
            count += 1
        if count:
            await self.session.flush()
        return count

    async def confirm(self, payment_id: int, external_id: str) -> Optional[Payment]:
        """Atomic confirm with double-spending protection.

        Uses SELECT ... FOR UPDATE to prevent race conditions where
        two webhooks could both confirm the same payment.
        """
        from sqlalchemy import select as _select
        result = await self.session.execute(
            _select(Payment)
            .where(Payment.id == payment_id)
            .with_for_update()
        )
        payment = result.scalar_one_or_none()
        if not payment:
            return None
        if payment.status == PaymentStatus.SUCCEEDED.value:
            return payment
        payment.status = PaymentStatus.SUCCEEDED.value
        payment.external_id = external_id
        await self.session.flush()
        return payment

    async def confirm_topup(self, payment_id: int, external_id: str) -> Optional[Payment]:
        """Atomic topup confirmation with FOR UPDATE lock."""
        from sqlalchemy import select as _select
        result = await self.session.execute(
            _select(Payment)
            .where(Payment.id == payment_id)
            .with_for_update()
        )
        payment = result.scalar_one_or_none()
        if not payment or payment.status == PaymentStatus.SUCCEEDED.value:
            return payment
        payment.status = PaymentStatus.SUCCEEDED.value
        payment.external_id = external_id
        await self.session.flush()
        return payment

    async def fail(self, payment_id: int) -> Optional[Payment]:
        payment = await self.get_by_id(payment_id)
        if payment:
            payment.status = PaymentStatus.FAILED.value
            await self.session.flush()
        return payment

    async def refund(self, payment_id: int) -> Optional[Payment]:
        payment = await self.get_by_id(payment_id)
        if payment:
            payment.status = PaymentStatus.REFUNDED.value
            await self.session.flush()
        return payment

    async def is_already_processed(self, payment_id: int) -> bool:
        payment = await self.get_by_id(payment_id)
        return payment is not None and payment.status == PaymentStatus.SUCCEEDED.value
