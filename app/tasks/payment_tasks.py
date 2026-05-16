import asyncio
import json
from datetime import datetime, timezone, timedelta
from sqlalchemy import select

from app.core.database import AsyncSessionFactory
from app.models.payment import Payment, PaymentProvider, PaymentStatus
from app.services.payment import PaymentService
from app.services.plan import PlanService
from app.services.vpn_key import VpnKeyService
from app.services.bot_settings import BotSettingsService
from app.services.telegram_notify import TelegramNotifyService
from app.utils.log import log

CHECK_INTERVAL = 60
MAX_PENDING_AGE = timedelta(hours=24)
PAYMENT_EXPIRE_MINUTES = 15


async def _provision_with_retry(session, user_id: int, plan, max_retries: int = 3):
    """Retry VPN provisioning with backoff."""
    for attempt in range(max_retries):
        try:
            key = await VpnKeyService(session).provision(user_id=user_id, plan=plan)
            if key:
                return key
        except Exception as e:
            log.warning(f"[polling] VPN provision attempt {attempt+1}/{max_retries}: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
    return None


async def _yookassa_polling_is_configured() -> bool:
    async with AsyncSessionFactory() as session:
        settings = BotSettingsService(session)
        enabled = (await settings.get("ps_yookassa_enabled")) == "1"
        if not enabled:
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
            select(Payment).where(
                Payment.status == PaymentStatus.PENDING.value,
                Payment.provider == PaymentProvider.YOOKASSA.value,
                Payment.external_id.isnot(None),
                Payment.created_at >= cutoff,
            ).limit(100)
        )
        payments = list(result.scalars().all())

        payment_data = [
            {"id": p.id, "external_id": p.external_id, "user_id": p.user_id, "meta": p.meta}
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
                plan_id = None
                if pd["meta"]:
                    try:
                        meta = json.loads(pd["meta"])
                        plan_id = int(meta.get("plan_id", 0)) or None
                    except Exception:
                        pass

                if not plan_id:
                    continue

                key_data = None
                payment_amount = None
                payment_currency = None
                plan_days = None
                plan_name = None

                async with AsyncSessionFactory() as session:
                    plan = await PlanService(session).get_by_id(plan_id)
                    if not plan:
                        continue

                    payment = await PaymentService(session).get_by_id(pd["id"])
                    if not payment or payment.status == PaymentStatus.SUCCEEDED.value:
                        continue

                    payment_amount = str(payment.amount)
                    payment_currency = payment.currency
                    plan_days = plan.duration_days
                    plan_name = plan.name

                    payment.status = PaymentStatus.SUCCEEDED.value
                    await session.flush()

                    key = await _provision_with_retry(session, pd["user_id"], plan)
                    if key:
                        payment.vpn_key_id = key.id
                        key_data = {
                            "id": key.id,
                            "access_url": key.access_url,
                        }

                    await session.commit()

                try:
                    async with AsyncSessionFactory() as session:
                        settings = await BotSettingsService(session).get_all()
                except Exception as e:
                    log.warning(f"[polling] Failed to load settings: {e}")
                    settings = {}

                success_msg = settings.get("payment_success_message") or "✅ Оплата прошла успешно!"
                if key_data:
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
                            f"Платеж подтвержден, но ключ не создан. Проверьте Pasarguard."
                        )

                if key_data:
                    await TelegramNotifyService().send_message(pd["user_id"], text)

                if key_data:
                    try:
                        from app.services.notification import notification_manager
                        await notification_manager.broadcast({
                            "type": "new_payment",
                            "data": {
                                "payment_id": pd["id"],
                                "user_id": pd["user_id"],
                                "amount": payment_amount or "0",
                                "currency": payment_currency or "RUB",
                            },
                        })
                    except Exception as e:
                        log.warning(f"[polling] WebSocket broadcast failed: {e}")
                
                log.info(f"[polling] Payment {pd['id']} confirmed, key={key_data['id'] if key_data else 'FAILED'}")

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
                log.error(f"[payment_tasks] check_pending_yookassa_payments failed: {e}")
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
