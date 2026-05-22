from app.models.bot_settings import BotSettings
from app.models.payment import Payment, PaymentProvider, PaymentStatus, PaymentType
from app.services.bot_settings import reset_bot_settings_cache
from app.tasks.payment_tasks import (
    _extract_payment_context,
    _yookassa_polling_is_configured,
    check_pending_yookassa_payments,
)
from app.models.user import User
from decimal import Decimal
from unittest.mock import AsyncMock


class _SessionFactory:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


async def test_yookassa_polling_disabled_without_bot_settings(session, monkeypatch):
    await reset_bot_settings_cache()
    monkeypatch.setattr(
        "app.tasks.payment_tasks.AsyncSessionFactory",
        lambda: _SessionFactory(session),
    )

    assert await _yookassa_polling_is_configured() is False


async def test_yookassa_polling_enabled_with_db_credentials(session, monkeypatch):
    await reset_bot_settings_cache()
    session.add_all(
        [
            BotSettings(key="ps_yookassa_enabled", value="1"),
            BotSettings(key="yookassa_shop_id_override", value="123456"),
            BotSettings(key="yookassa_secret_key_override", value="test_secret_12345"),
        ]
    )
    await session.commit()

    monkeypatch.setattr(
        "app.tasks.payment_tasks.AsyncSessionFactory",
        lambda: _SessionFactory(session),
    )

    assert await _yookassa_polling_is_configured() is True


def test_extract_payment_context_handles_invalid_meta():
    assert _extract_payment_context({"meta": None}) == (None, None)
    assert _extract_payment_context({"meta": "not-json"}) == (None, None)
    assert _extract_payment_context(
        {"meta": '{"plan_id":"5","extend_key_id":"9"}'}
    ) == (5, 9)


async def test_check_pending_yookassa_payments_confirms_topup_once(
    session, monkeypatch
):
    await reset_bot_settings_cache()
    session.add_all(
        [
            BotSettings(key="ps_yookassa_enabled", value="1"),
            BotSettings(key="yookassa_shop_id_override", value="123456"),
            BotSettings(key="yookassa_secret_key_override", value="test_secret_12345"),
        ]
    )
    user = User(
        id=777, username="topup-user", full_name="Topup User", balance=Decimal("15.00")
    )
    payment = Payment(
        user_id=user.id,
        provider=PaymentProvider.YOOKASSA.value,
        payment_type=PaymentType.TOPUP.value,
        amount=Decimal("50.00"),
        currency="RUB",
        status=PaymentStatus.PENDING.value,
        external_id="yk_invoice_1",
    )
    session.add_all([user, payment])
    await session.commit()

    class _FakeYookassa:
        async def get_payment(self, external_id: str):
            assert external_id == "yk_invoice_1"
            return type(
                "YKPayment", (), {"status": "succeeded", "id": "yk_invoice_1"}
            )()

    monkeypatch.setattr(
        "app.tasks.payment_tasks.AsyncSessionFactory",
        lambda: _SessionFactory(session),
    )
    monkeypatch.setattr(
        "app.services.yookassa.YookassaService.create",
        AsyncMock(return_value=_FakeYookassa()),
    )
    send_message = AsyncMock()
    monkeypatch.setattr(
        "app.tasks.payment_tasks.TelegramNotifyService.send_message",
        send_message,
    )

    await check_pending_yookassa_payments()
    await session.refresh(user)
    await session.refresh(payment)

    assert payment.status == PaymentStatus.SUCCEEDED.value
    assert user.balance == Decimal("65.00")
    send_message.assert_awaited_once()


async def test_check_pending_yookassa_payments_does_not_treat_subscription_without_meta_as_topup(
    session, monkeypatch
):
    await reset_bot_settings_cache()
    session.add_all(
        [
            BotSettings(key="ps_yookassa_enabled", value="1"),
            BotSettings(key="yookassa_shop_id_override", value="123456"),
            BotSettings(key="yookassa_secret_key_override", value="test_secret_12345"),
        ]
    )
    user = User(
        id=778,
        username="sub-user",
        full_name="Subscription User",
        balance=Decimal("15.00"),
    )
    payment = Payment(
        user_id=user.id,
        provider=PaymentProvider.YOOKASSA.value,
        payment_type=PaymentType.SUBSCRIPTION.value,
        amount=Decimal("50.00"),
        currency="RUB",
        status=PaymentStatus.PENDING.value,
        external_id="yk_invoice_sub_missing_meta",
        meta=None,
    )
    session.add_all([user, payment])
    await session.commit()

    class _FakeYookassa:
        async def get_payment(self, external_id: str):
            assert external_id == "yk_invoice_sub_missing_meta"
            return type(
                "YKPayment",
                (),
                {"status": "succeeded", "id": "yk_invoice_sub_missing_meta"},
            )()

    monkeypatch.setattr(
        "app.tasks.payment_tasks.AsyncSessionFactory",
        lambda: _SessionFactory(session),
    )
    monkeypatch.setattr(
        "app.services.yookassa.YookassaService.create",
        AsyncMock(return_value=_FakeYookassa()),
    )
    send_message = AsyncMock()
    monkeypatch.setattr(
        "app.tasks.payment_tasks.TelegramNotifyService.send_message",
        send_message,
    )

    await check_pending_yookassa_payments()
    await session.refresh(user)
    await session.refresh(payment)

    assert payment.status == PaymentStatus.PENDING.value
    assert user.balance == Decimal("15.00")
    send_message.assert_not_awaited()
