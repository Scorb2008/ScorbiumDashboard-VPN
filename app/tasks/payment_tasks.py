import asyncio
import json
from datetime import datetime, timezone, timedelta
from sqlalchemy import select

from app.core.database import AsyncSessionFactory
from app.models.payment import Payment, PaymentProvider, PaymentStatus, PaymentType
from app.services.payment_fulfillment import PaymentFulfillmentService
from app.services.payment import PaymentService
from app.services.plan import PlanService
from app.services.vpn_key import VpnKeyService
from app.services.bot_settings import BotSettingsService
from app.services.admin_events import (
    notify_admins_balance_topup,
    notify_admins_new_purchase,
)
from app.services.telegram_notify import TelegramNotifyService
from app.bot.utils.subscription_links import subscription_link_kb
from app.utils.log import log

CHECK_INTERVAL = 60
MAX_PENDING_AGE = timedelta(hours=24)
PAYMENT_EXPIRE_MINUTES = 15


def _extract_payment_context(payment_row: dict) -> tuple[int | None, int | None]:
    meta_raw = payment_row.get("meta")
    if not meta_raw:
        return None, None

    try:
        meta = json.loads(meta_raw)
    except (TypeError, ValueError):
        return None, None

    try:
        plan_id = int(meta.get("plan_id", 0)) or None
    except (TypeError, ValueError):
        plan_id = None

    try:
        extend_key_id = int(meta.get("extend_key_id", 0)) or None
    except (TypeError, ValueError):
        extend_key_id = None

    return plan_id, extend_key_id


async def _provision_with_retry(session, user_id: int, plan, max_retries: int = 3):
    """Retry VPN provisioning with backoff."""
    for attempt in range(max_retries):
        try:
            key = await VpnKeyService(session).provision(user_id=user_id, plan=plan)
            if key:
                return key
        except Exception as e:
            log.warning(
                f"[polling] VPN provision attempt {attempt + 1}/{max_retries}: {e}"
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(2**attempt)
    return None


async def _yookassa_polling_is_configured() -> bool:
    async with AsyncSessionFactory() as session:
        settings = BotSettingsService(session)
        yookassa_enabled = (await settings.get("ps_yookassa_enabled")) == "1"
        sbp_enabled = (await settings.get("ps_sbp_enabled")) == "1"
        if not (yookassa_enabled or sbp_enabled):
            return False

        shop_id = (await settings.get("yookassa_shop_id_override") or "").strip()
        secret_key = (await settings.get("yookassa_secret_key_override") or "").strip()
        return bool(shop_id and secret_key)


async def check_pending_yookassa_payments() -> None:
    if not await _yookassa_polling_is_configured():
        return

    try:
        from app.services.yookassa import YookassaService

        yk = await YookassaService.create()
    except Exception as e:
        log.warning(f"[payment_tasks] YookassaService init failed: {e}")
        return

    async with AsyncSessionFactory() as session:
        cutoff = datetime.now(timezone.utc) - MAX_PENDING_AGE
        result = await session.execute(
            select(Payment)
            .where(
                Payment.status == PaymentStatus.PENDING.value,
                Payment.provider.in_(
                    [
                        PaymentProvider.YOOKASSA.value,
                        PaymentProvider.YOOKASSA_SBP.value,
                    ]
                ),
                Payment.external_id.isnot(None),
                Payment.created_at >= cutoff,
            )
            .limit(100)
        )
        payments = list(result.scalars().all())

        payment_data = [
            {
                "id": p.id,
                "external_id": p.external_id,
                "user_id": p.user_id,
                "payment_type": p.payment_type,
                "meta": p.meta,
            }
            for p in payments
        ]

    for pd in payment_data:
        try:
            try:
                yk_payment = await asyncio.wait_for(
                    yk.get_payment(pd["external_id"]), timeout=30
                )
            except asyncio.TimeoutError:
                log.warning(f"[polling] Timeout checking payment {pd['id']}")
                continue
            if yk_payment.status == "succeeded":
                plan_id, extend_key_id = _extract_payment_context(pd)
                async with AsyncSessionFactory() as session:
                    payment = await PaymentService(session).get_by_id(pd["id"])
                    if not payment:
                        log.warning(f"[polling] Payment {pd['id']} not found")
                        continue

                    if payment.payment_type == PaymentType.TOPUP.value:
                        topup_result = await PaymentFulfillmentService(
                            session
                        ).confirm_topup_and_credit_once(pd["id"], str(yk_payment.id))
                        await session.commit()
                        if not topup_result.payment:
                            continue
                        if not topup_result.just_processed:
                            log.info(
                                f"[polling] Duplicate topup ignored for payment {pd['id']}"
                            )
                            continue
                        balance = topup_result.balance
                        text = (
                            "✅ <b>Баланс пополнен!</b>\n\n"
                            f"💰 Зачислено: <b>{topup_result.payment.amount} ₽</b>\n"
                            f"👛 Текущий баланс: <b>{balance} ₽</b>"
                        )
                        await TelegramNotifyService().send_message(pd["user_id"], text)
                        await notify_admins_balance_topup(
                            user_id=pd["user_id"],
                            payment_id=pd["id"],
                            amount=str(topup_result.payment.amount),
                            balance=str(balance),
                            provider=str(topup_result.payment.provider or "yookassa"),
                        )
                        log.info(
                            f"[polling] Topup {pd['id']} confirmed + balance credited"
                        )
                        continue

                    if not plan_id:
                        log.warning(
                            "[polling] Subscription payment %s has no plan_id in metadata",
                            pd["id"],
                        )
                        continue

                    plan = await PlanService(session).get_by_id(plan_id)
                    if not plan:
                        log.warning(
                            f"[polling] Plan {plan_id} not found for payment {pd['id']}"
                        )
                        continue

                    payment_amount = str(payment.amount)
                    payment_currency = payment.currency
                    plan_days = plan.duration_days
                    plan_name = plan.name

                    confirmation = await PaymentService(session).confirm_once(
                        pd["id"], str(yk_payment.id)
                    )
                    if not confirmation.payment:
                        continue

                    fulfillment = PaymentFulfillmentService(session)
                    if extend_key_id:
                        delivery = await fulfillment.extend_subscription_once(
                            pd["id"], pd["user_id"], extend_key_id, plan
                        )
                    else:
                        delivery = await fulfillment.provision_subscription_once(
                            pd["id"], pd["user_id"], plan
                        )

                    await session.commit()

                    if not confirmation.just_confirmed and not delivery.just_processed:
                        log.info(
                            f"[polling] Duplicate subscription webhook ignored for payment {pd['id']}"
                        )
                        continue

                    key_data = None
                    if delivery.key:
                        key_data = {
                            "id": delivery.key.id,
                            "access_url": delivery.key.access_url,
                        }

                try:
                    async with AsyncSessionFactory() as session:
                        settings = await BotSettingsService(session).get_all()
                except Exception as e:
                    log.warning(f"[polling] Failed to load settings: {e}")
                    settings = {}

                success_msg = (
                    settings.get("payment_success_message")
                    or "✅ Оплата прошла успешно!"
                )
                if extend_key_id and key_data and delivery.key:
                    exp = (
                        delivery.key.expires_at.strftime("%d.%m.%Y")
                        if delivery.key.expires_at
                        else "—"
                    )
                    text = (
                        f"{success_msg}\n\n"
                        f"🔄 <b>Подписка продлена!</b>\n"
                        f"📅 Новая дата: <b>{exp}</b>\n"
                        f"➕ +{plan_days} дней"
                    )
                elif key_data:
                    text = (
                        f"{success_msg}\n\n"
                        f"🔑 <b>Ссылка подписки:</b>\n<code>{key_data['access_url']}</code>\n\n"
                        f"📅 Действует <b>{plan_days} дней</b>"
                    )
                else:
                    text = f"{success_msg}\n\n⚠️ Не удалось создать ключ. Обратитесь в поддержку."

                    from app.core.config import config

                    for admin_id in config.telegram.telegram_admin_ids[:3]:
                        await TelegramNotifyService().send_message(
                            admin_id,
                            f"🚨 <b>Ошибка выдачи ключа!</b>\n\n"
                            f"Пользователь: {pd['user_id']}\n"
                            f"Платеж: #{pd['id']}\n"
                            f"План: {plan_name}\n\n"
                            f"Платеж подтвержден, но ключ не создан. Проверьте Pasarguard.",
                        )

                reply_markup = None
                if key_data and key_data["access_url"]:
                    reply_markup = subscription_link_kb(
                        key_data["access_url"], lang="ru"
                    ).model_dump(exclude_none=True)
                await TelegramNotifyService().send_message(
                    pd["user_id"],
                    text,
                    reply_markup=reply_markup,
                )

                await notify_admins_new_purchase(
                    user_id=pd["user_id"],
                    payment_id=pd["id"],
                    plan_name=plan_name,
                    amount=payment_amount or plan_price,
                    currency=payment_currency or "RUB",
                    provider=str(payment_provider),
                    plan_days=plan_days,
                    key_issued=bool(key_data),
                )

                if key_data:
                    try:
                        from app.services.notification import notification_manager

                        await notification_manager.broadcast(
                            {
                                "type": "new_payment",
                                "data": {
                                    "payment_id": pd["id"],
                                    "user_id": pd["user_id"],
                                    "amount": payment_amount or "0",
                                    "currency": payment_currency or "RUB",
                                },
                            }
                        )
                    except Exception as e:
                        log.warning(f"[polling] WebSocket broadcast failed: {e}")

                log.info(
                    f"[polling] Payment {pd['id']} confirmed, key={key_data['id'] if key_data else 'FAILED'}"
                )

            elif yk_payment.status in ("canceled", "expired"):
                async with AsyncSessionFactory() as session:
                    await PaymentService(session).fail(pd["id"])
                    await session.commit()

        except Exception as e:
            log.warning(f"[polling] Error checking payment {pd['id']}: {e}")


async def payment_polling_loop() -> None:
    """Main payment polling loop with error isolation."""
    log.info("💳 Payment polling task started")
    while True:
        try:
            await asyncio.sleep(CHECK_INTERVAL)
            try:
                await check_pending_yookassa_payments()
            except Exception as e:
                log.error(
                    f"[payment_tasks] check_pending_yookassa_payments failed: {e}"
                )
            try:
                await expire_old_pending_payments()
            except Exception as e:
                log.error(f"[payment_tasks] expire_old_pending_payments failed: {e}")
        except Exception as e:
            log.error(f"[payment_tasks] polling loop fatal error: {e}")
            await asyncio.sleep(CHECK_INTERVAL * 2)


async def expire_old_pending_payments() -> None:
    try:
        async with AsyncSessionFactory() as session:
            from app.services.payment import PaymentService

            svc = PaymentService(session)
            count = await svc.expire_old_pending(max_age_minutes=PAYMENT_EXPIRE_MINUTES)
            if count:
                await session.commit()
                log.info(f"[payment_tasks] Expired {count} old pending payments")
    except Exception as e:
        log.error(f"[payment_tasks] expire_old_pending error: {e}")
