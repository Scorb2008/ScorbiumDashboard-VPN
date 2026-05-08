"""Tests for PaymentService: idempotency, race conditions, edge cases."""
import pytest
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from sqlalchemy import select

from app.models.payment import PaymentStatus, PaymentProvider, PaymentType, Payment
from app.services.payment import PaymentService


class TestPaymentService:
    async def test_confirm_double_spending_prevention(self, session, sample_payment):
        svc = PaymentService(session)
        payment_id = sample_payment.id

        confirmed = await svc.confirm(payment_id, "ext_001")
        assert confirmed is not None
        assert confirmed.status == PaymentStatus.SUCCEEDED.value

        confirmed_again = await svc.confirm(payment_id, "ext_002")
        assert confirmed_again is not None
        assert confirmed_again.status == PaymentStatus.SUCCEEDED.value
        assert confirmed_again.external_id == "ext_001"

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
        confirmed = await svc.confirm_topup(payment_id, "ext_topup_001")
        assert confirmed is not None
        assert confirmed.status == PaymentStatus.SUCCEEDED.value

        confirmed_again = await svc.confirm_topup(payment_id, "ext_topup_002")
        assert confirmed_again is not None
        assert confirmed_again.external_id == "ext_topup_001"

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
