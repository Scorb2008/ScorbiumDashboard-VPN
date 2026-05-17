"""Tests for PaymentService: idempotency, race conditions, edge cases."""
from decimal import Decimal

from app.models.payment import PaymentStatus, PaymentProvider, PaymentType, Payment
from app.services.payment import PaymentService


class TestPaymentService:
    async def test_confirm_double_spending_prevention(self, session, sample_payment):
        svc = PaymentService(session)
        payment_id = sample_payment.id

        first = await svc.confirm_once(payment_id, "ext_001")
        assert first.payment is not None
        assert first.just_confirmed is True
        assert first.payment.status == PaymentStatus.SUCCEEDED.value

        second = await svc.confirm_once(payment_id, "ext_002")
        assert second.payment is not None
        assert second.just_confirmed is False
        assert second.payment.status == PaymentStatus.SUCCEEDED.value
        assert second.payment.external_id == "ext_001"

    async def test_confirm_nonexistent_payment(self, session):
        svc = PaymentService(session)
        result = await svc.confirm(99999, "ext_001")
        assert result is None

    async def test_confirm_topup_idempotent(self, session, sample_user):
        payment = Payment(
            user_id=sample_user.id,
            provider=PaymentProvider.BALANCE.value,
            payment_type=PaymentType.TOPUP.value,
            amount=Decimal("50.00"),
            status=PaymentStatus.PENDING.value,
        )
        session.add(payment)
        await session.commit()
        payment_id = payment.id

        svc = PaymentService(session)
        first = await svc.confirm_topup_once(payment_id, "ext_topup_001")
        assert first.payment is not None
        assert first.just_confirmed is True
        assert first.payment.status == PaymentStatus.SUCCEEDED.value

        second = await svc.confirm_topup_once(payment_id, "ext_topup_002")
        assert second.payment is not None
        assert second.just_confirmed is False
        assert second.payment.external_id == "ext_topup_001"

    async def test_confirm_once_does_not_reopen_failed_payment(self, session, sample_payment):
        svc = PaymentService(session)
        await svc.fail(sample_payment.id)

        result = await svc.confirm_once(sample_payment.id, "ext_failed")

        assert result.payment is not None
        assert result.just_confirmed is False
        assert result.payment.status == PaymentStatus.FAILED.value

    async def test_is_already_processed(self, session, sample_payment):
        svc = PaymentService(session)
        assert not await svc.is_already_processed(sample_payment.id)

        await svc.confirm(sample_payment.id, "ext_001")
        assert await svc.is_already_processed(sample_payment.id)

    async def test_fail(self, session, sample_payment):
        svc = PaymentService(session)
        failed = await svc.fail(sample_payment.id)
        assert failed is not None
        assert failed.status == PaymentStatus.FAILED.value

    async def test_refund(self, session, sample_payment):
        svc = PaymentService(session)
        await svc.confirm(sample_payment.id, "ext_001")
        refunded = await svc.refund(sample_payment.id)
        assert refunded is not None
        assert refunded.status == PaymentStatus.REFUNDED.value

    async def test_get_by_external_id(self, session, sample_payment):
        svc = PaymentService(session)
        await svc.confirm(sample_payment.id, "ext_unique_123")
        found = await svc.get_by_external_id("ext_unique_123")
        assert found is not None
        assert found.id == sample_payment.id
