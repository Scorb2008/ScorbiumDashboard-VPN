from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.api.dependencies import get_db, get_current_admin
from app.models.payment import PaymentStatus
from app.schemas.payment import PaymentCreate, PaymentRead
from app.services.bot_settings import BotSettingsService
from app.services.payment import PaymentService
from app.services.payment_fulfillment import PaymentFulfillmentService, TopupFulfillmentResult
from app.services.plan import PlanService
from app.utils.log import log

router = APIRouter()


async def _get_yookassa_webhook_secret(db: AsyncSession) -> str:
    secret = await BotSettingsService(db).get("yookassa_secret_key_override") or ""
    if secret:
        return secret
    from app.core.config import config as _yk_cfg

    if _yk_cfg.yookassa and _yk_cfg.yookassa.yookassa_secret_key:
        return _yk_cfg.yookassa.yookassa_secret_key.get_secret_value()
    return ""


async def _notify_topup_success(payment_user_id: int, amount, balance) -> None:
    from app.services.telegram_notify import TelegramNotifyService

    text = (
        "✅ <b>Баланс пополнен!</b>\n\n"
        f"💰 Зачислено: <b>{amount} ₽</b>\n"
        f"👛 Текущий баланс: <b>{balance} ₽</b>"
    )
    try:
        await TelegramNotifyService().send_message(payment_user_id, text)
    except Exception as e:
        log.warning(f"Topup success notification failed for user {payment_user_id}: {e}")


async def _finalize_topup_payment(
    db: AsyncSession, payment_id: int, external_id: str
) -> TopupFulfillmentResult:
    result = await PaymentFulfillmentService(db).confirm_topup_and_credit_once(
        payment_id, external_id
    )
    await db.commit()
    if result.just_processed and result.payment and result.balance is not None:
        await _notify_topup_success(
            result.payment.user_id,
            result.payment.amount,
            result.balance,
        )
    return result


async def _finalize_subscription_payment(
    db: AsyncSession,
    payment_id: int,
    external_id: str,
    plan_id: int,
    extend_key_id: int | None = None,
):
    plan = await PlanService(db).get_by_id(plan_id)
    if not plan:
        return None, None, False, False

    confirmation = await PaymentService(db).confirm_once(payment_id, external_id)
    payment = confirmation.payment
    if not payment:
        return None, None, False, False

    fulfillment = PaymentFulfillmentService(db)
    if extend_key_id:
        delivery = await fulfillment.extend_subscription_once(
            payment_id, payment.user_id, int(extend_key_id), plan
        )
    else:
        delivery = await fulfillment.provision_subscription_once(
            payment_id, payment.user_id, plan
        )
    return payment, delivery.key, confirmation.just_confirmed, delivery.just_processed


@router.get("/", response_model=list[PaymentRead], summary="List payments")
async def list_payments(
    limit: int = 100,
    offset: int = 0,
    status: Optional[PaymentStatus] = None,
    user_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> list[PaymentRead]:
    return await PaymentService(db).get_all(limit=limit, offset=offset, status=status, user_id=user_id)


@router.get("/{payment_id}", response_model=PaymentRead, summary="Get payment")
async def get_payment(
    payment_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> PaymentRead:
    payment = await PaymentService(db).get_by_id(payment_id)
    if not payment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
    return payment


@router.post("/", response_model=PaymentRead, status_code=status.HTTP_201_CREATED, summary="Create pending payment")
async def create_payment(
    data: PaymentCreate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> PaymentRead:
    plan = await PlanService(db).get_by_id(data.plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    return await PaymentService(db).create_pending(data.user_id, plan, data.provider)


@router.post("/{payment_id}/refund", response_model=PaymentRead, summary="Refund payment")
async def refund_payment(
    payment_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> PaymentRead:
    payment = await PaymentService(db).refund(payment_id)
    if not payment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
    return payment


@router.post("/webhook/freekassa", summary="FreeKassa webhook", include_in_schema=False)
async def freekassa_webhook(request: Request, db: AsyncSession = Depends(get_db)) -> str:
    """
    URL оповещения для FreeKassa.
    Укажи в личном кабинете: https://project.cfd/api/v1/payments/webhook/freekassa
    """
    import ipaddress
    from app.services.freekassa import FreeKassaService
    client_ip = request.headers.get("X-Real-IP") or request.client.host
    try:
        if ipaddress.ip_address(client_ip) not in {ipaddress.ip_address(ip) for ip in FreeKassaService.ALLOWED_IPS}:
            log.warning(f"FreeKassa webhook: blocked IP {client_ip}")
            return "FORBIDDEN"
    except Exception:
        log.warning(f"FreeKassa webhook: invalid IP {client_ip}")
        return "FORBIDDEN"

    form = await request.form()
    merchant_id = str(form.get("MERCHANT_ID", ""))
    amount = str(form.get("AMOUNT", ""))
    order_id = str(form.get("MERCHANT_ORDER_ID", ""))
    sign = str(form.get("SIGN", ""))

    # Получаем секретное слово 2 из БД
    settings = await BotSettingsService(db).get_all()
    fk = FreeKassaService.from_settings(settings)
    if not fk:
        log.error("FreeKassa webhook: service not configured")
        return "ERROR"

    if not fk.verify_notification(merchant_id, amount, order_id, sign):
        log.warning(f"FreeKassa webhook: invalid sign for order {order_id}")
        return "WRONG SIGN"

    # order_id формат:
    # "fk_{payment_id}_{plan_id}" — подписка
    # "fk_topup_{payment_id}" — пополнение баланса
    # "fk_ext_{payment_id}_{plan_id}_{key_id}" — продление
    try:
        parts = order_id.split("_")
        if parts[0] == "fk" and len(parts) >= 3:
            if parts[1] == "topup":
                payment_id = int(parts[2])
                result = await _finalize_topup_payment(
                    db,
                    payment_id,
                    str(form.get("intid", "")),
                )
                payment = result.payment
                if not payment:
                    log.error(f"FreeKassa: topup payment {payment_id} not found")
                    return "YES"
                if not result.just_processed:
                    log.info(f"FreeKassa: duplicate topup webhook ignored for payment {payment_id}")
                    return "YES"
                log.info(f"FreeKassa: topup {payment_id} confirmed + balance credited")
                return "YES"
            elif parts[1] == "ext" and len(parts) >= 5:
                payment_id = int(parts[2])
                plan_id = int(parts[3])
                key_id = int(parts[4])
            else:
                payment_id = int(parts[1])
                plan_id = int(parts[2])
        else:
            log.error(f"FreeKassa webhook: unknown order_id format: {order_id}")
            return "YES"
    except (ValueError, IndexError):
        log.error(f"FreeKassa webhook: cannot parse order_id: {order_id}")
        return "YES"

    from app.services.plan import PlanService
    from app.services.vpn_key import VpnKeyService
    from app.services.user import UserService
    from app.services.telegram_notify import TelegramNotifyService

    payment, key, just_confirmed, just_processed = await _finalize_subscription_payment(
        db,
        payment_id,
        str(form.get("intid", "")),
        plan_id,
        key_id if 'key_id' in locals() else None,
    )
    if not payment:
        log.error(f"FreeKassa webhook: payment {payment_id} not found")
        return "YES"
    if not just_confirmed and not just_processed:
        log.info(f"FreeKassa webhook: duplicate or stale payment ignored: {payment_id}")
        return "YES"

    if 'key_id' in locals():
        await db.commit()
        if key:
            exp = key.expires_at.strftime("%d.%m.%Y") if key.expires_at else "—"
            plan = await PlanService(db).get_by_id(plan_id)
            days = plan.duration_days if plan else 0
            text = f"✅ Оплата прошла успешно!\n\n🔄 <b>Подписка продлена!</b>\n📅 Новая дата: <b>{exp}</b>\n➕ +{days} дней"
        else:
            text = "✅ Оплата прошла, но возникла ошибка продления. Обратитесь в поддержку."
    else:
        await db.commit()

        success_msg = settings.get("payment_success_message", "✅ Оплата прошла успешно!")
        plan = await PlanService(db).get_by_id(plan_id)
        days = plan.duration_days if plan else "—"
        if key:
            text = f"{success_msg}\n\n🔑 <b>Ваш ключ:</b>\n<code>{key.access_url}</code>\n\n📅 Действует <b>{days} дней</b>"
        else:
            text = f"{success_msg}\n\nНажмите «Мои ключи» для получения ключа."

    await TelegramNotifyService().send_message(payment.user_id, text)
    log.info(f"FreeKassa: payment {payment_id} confirmed via webhook")
    return "YES"


@router.post("/webhook/yookassa", summary="Yookassa webhook", include_in_schema=False)
async def yookassa_webhook(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """Atomic Yookassa webhook: verify signature, confirm payment + provision."""
    import asyncio
    import json

    try:
        raw_body = await request.body()
        data = json.loads(raw_body)
    except Exception as e:
        log.error(f"Yookassa webhook: invalid JSON body: {e}")
        return {"status": "error", "message": "invalid body"}

    # Signature verification — fetch secret from DB first, then .env fallback
    from app.services.webhook_security import verify_yookassa_signature

    sig_header = request.headers.get("Authorization", "").strip()
    secret = await _get_yookassa_webhook_secret(db)
    if not secret:
        log.error("Yookassa webhook: secret is not configured")
        return {"status": "error", "message": "webhook not configured"}
    if not sig_header:
        log.warning("Yookassa webhook: missing Authorization header")
        return {"status": "error", "message": "missing signature"}
    if not await verify_yookassa_signature(raw_body, sig_header, secret):
        log.warning("Yookassa webhook: invalid signature")
        return {"status": "error", "message": "invalid signature"}

    event = data.get("event")
    obj = data.get("object", {})
    external_id = obj.get("id")
    metadata = obj.get("metadata", {})
    payment_id = metadata.get("payment_id")
    plan_id = metadata.get("plan_id")
    extend_key_id = metadata.get("extend_key_id")

    log.info(f"Yookassa webhook: event={event} payment_id={payment_id} plan_id={plan_id} extend_key_id={extend_key_id}")

    if event == "payment.canceled" and payment_id:
        try:
            await PaymentService(db).fail(int(payment_id))
            await db.commit()
            log.info(f"Yookassa: payment {payment_id} marked as failed")
        except Exception as e:
            log.error(f"Yookassa cancel error: {e}")
        return {"status": "ok"}

    if event != "payment.succeeded" or not payment_id:
        return {"status": "ok"}

    try:
        if not plan_id:
            result = await _finalize_topup_payment(
                db, int(payment_id), str(external_id)
            )
            if not result.payment:
                log.warning(f"Yookassa topup: payment {payment_id} not found")
            elif not result.just_processed:
                log.info(f"Yookassa topup: duplicate or stale webhook ignored for payment {payment_id}")
            else:
                log.info(f"Yookassa topup: payment {payment_id} confirmed + balance credited")
            return {"status": "ok"}

        payment, key, just_confirmed, just_processed = await _finalize_subscription_payment(
            db,
            int(payment_id),
            str(external_id),
            int(plan_id),
            int(extend_key_id) if extend_key_id else None,
        )

        if not payment:
            log.warning(f"Yookassa: payment {payment_id} not found for confirmation")
            return {"status": "ok"}
        if not just_confirmed and not just_processed:
            log.info(f"Yookassa: duplicate or stale webhook ignored for payment {payment_id}")
            return {"status": "ok"}

        await db.commit()

        try:
            from app.services.telegram_notify import TelegramNotifyService
            from app.services.bot_settings import BotSettingsService
            settings = await BotSettingsService(db).get_all()
            success_msg = settings.get("payment_success_message", "Оплата прошла успешно!")
            plan = await PlanService(db).get_by_id(int(plan_id))
            days = plan.duration_days if plan else "—"

            if extend_key_id and key:
                exp = key.expires_at.strftime("%d.%m.%Y") if key.expires_at else "—"
                text = (
                    f"✅ {success_msg}\n\n"
                    f"🔄 <b>Подписка продлена!</b>\n"
                    f"📅 Новая дата: <b>{exp}</b>\n"
                    f"➕ +{days} дней"
                )
            elif key:
                text = (
                    f"✅ {success_msg}\n\n"
                    f"🔑 <b>Ваш VPN ключ:</b>\n<code>{key.access_url}</code>\n\n"
                    f"📅 Действует <b>{days} дней</b>"
                )
            else:
                text = (
                    f"✅ {success_msg}\n\n"
                    f"🔐 Ключ готовится (1-2 минуты). "
                    f"Нажмите «Мои ключи» или обратитесь в поддержку, если ключ не появился."
                )

            await TelegramNotifyService().send_message(payment.user_id, text)
        except Exception as e:
            log.warning(f"Yookassa notification error: {e}")

        return {"status": "ok"}

    except Exception as e:
        log.error(f"Yookassa webhook error: {e}")
        return {"status": "ok"}


@router.post("/webhook/cryptobot", summary="CryptoBot webhook", include_in_schema=False)
async def cryptobot_webhook(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """Handle CryptoBot invoice paid webhook with signature verification."""
    try:
        data = await request.json()
    except Exception as e:
        log.error(f"CryptoBot webhook: invalid JSON: {e}")
        return {"ok": False}

    sig_header = request.headers.get("X-Crypto-Pay-API-Signature", "").strip()
    from app.services.webhook_security import verify_cryptobot_signature
    settings = await BotSettingsService(db).get_all()
    cb_token = settings.get("cryptobot_token", "").strip()
    if not cb_token:
        log.error("CryptoBot webhook: token is not configured")
        return {"ok": False}
    if not sig_header:
        log.warning("CryptoBot webhook: missing signature header")
        return {"ok": False}
    if not verify_cryptobot_signature(data, sig_header, cb_token):
        log.warning("CryptoBot webhook: invalid signature")
        return {"ok": False}

    invoice = data.get("payload", {})
    invoice_id = invoice.get("invoice_id")
    status = invoice.get("status", "")

    if status != "paid":
        return {"ok": True}

    payload_raw = invoice.get("payload", "")
    try:
        from app.services.vpn_key import VpnKeyService
        from app.services.telegram_notify import TelegramNotifyService

        if payload_raw.startswith("topup_crypto:"):
            topup_parts = payload_raw.split(":")
            if len(topup_parts) < 2:
                log.error(f"CryptoBot webhook: invalid topup payload: {payload_raw}")
                return {"ok": True}
            payment_id = int(topup_parts[1])
            result = await _finalize_topup_payment(db, payment_id, str(invoice_id))
            if not result.payment:
                log.error(f"CryptoBot topup: payment {payment_id} not found")
            elif not result.just_processed:
                log.info(f"CryptoBot topup: duplicate or stale payment ignored: {payment_id}")
            else:
                log.info(f"CryptoBot topup: payment {payment_id} confirmed + balance credited")
            return {"ok": True}

        parts = payload_raw.split("_")
        if len(parts) >= 3 and parts[0] == "cb":
            payment_id = int(parts[1])
            plan_id = int(parts[2])
            extend_key_id = int(parts[3]) if len(parts) > 3 else None
        else:
            log.error(f"CryptoBot webhook: unknown payload format: {payload_raw}")
            return {"ok": True}

        payment, key, just_confirmed, just_processed = await _finalize_subscription_payment(
            db,
            int(payment_id),
            str(invoice_id),
            plan_id,
            extend_key_id,
        )
        if not payment:
            log.error(f"CryptoBot webhook: payment {payment_id} not found")
            return {"ok": True}
        if not just_confirmed and not just_processed:
            log.info(f"CryptoBot webhook: duplicate or stale payment ignored: {payment_id}")
            return {"ok": True}

        await db.commit()

        settings = await BotSettingsService(db).get_all()
        success_msg = settings.get("payment_success_message", "✅ Оплата прошла успешно!")

        if key:
            if extend_key_id:
                exp = key.expires_at.strftime("%d.%m.%Y") if key.expires_at else "—"
                text = f"{success_msg}\n\n🔄 <b>Подписка продлена!</b>\n📅 Новая дата: <b>{exp}</b>"
            else:
                text = f"{success_msg}\n\n🔑 <b>Ваш VPN ключ:</b>\n<code>{key.access_url}</code>"
        else:
            text = f"{success_msg}\n\n⚠️ Ключ не создан, обратитесь в поддержку."

        await TelegramNotifyService().send_message(payment.user_id, text)
        log.info(f"CryptoBot: payment {payment_id} confirmed via webhook")

    except Exception as e:
        log.error(f"CryptoBot webhook error: {e}")

    return {"ok": True}


@router.post("/webhook/platega", summary="Platega webhook", include_in_schema=False)
async def platega_webhook(request: Request, db: AsyncSession = Depends(get_db)) -> str:
    """Handle Platega transaction webhook."""
    try:
        data = await request.json()
    except Exception as e:
        log.error(f"Platega webhook: invalid JSON: {e}")
        return "ERROR"

    transaction_id = data.get("transactionId", "")
    status = data.get("status", "")

    if status not in ("SUCCESS", "PAID", "COMPLETED"):
        return "OK"

    payload_raw = data.get("payload", "")
    try:
        from app.services.telegram_notify import TelegramNotifyService

        parts = payload_raw.split("_")
        if parts[0] == "pl" and len(parts) >= 3:
            payment_id = int(parts[1])
            plan_id = int(parts[2])
            extend_key_id = int(parts[3]) if len(parts) > 3 else None
        else:
            log.error(f"Platega webhook: unknown payload format: {payload_raw}")
            return "OK"

        payment, key, just_confirmed, just_processed = await _finalize_subscription_payment(
            db,
            int(payment_id),
            transaction_id,
            plan_id,
            extend_key_id,
        )
        if not payment:
            log.error(f"Platega webhook: payment {payment_id} not found")
            return "OK"
        if not just_confirmed and not just_processed:
            log.info(f"Platega webhook: duplicate or stale payment ignored: {payment_id}")
            return "OK"

        await db.commit()

        settings = await BotSettingsService(db).get_all()
        success_msg = settings.get("payment_success_message", "✅ Оплата прошла успешно!")

        if key:
            if extend_key_id:
                exp = key.expires_at.strftime("%d.%m.%Y") if key.expires_at else "—"
                text = f"{success_msg}\n\n🔄 <b>Подписка продлена!</b>\n📅 Новая дата: <b>{exp}</b>"
            else:
                text = f"{success_msg}\n\n🔑 <b>Ваш VPN ключ:</b>\n<code>{key.access_url}</code>"
        else:
            text = f"{success_msg}\n\n⚠️ Ключ не создан, обратитесь в поддержку."

        await TelegramNotifyService().send_message(payment.user_id, text)
        log.info(f"Platega: payment {payment_id} confirmed via webhook")

    except Exception as e:
        log.error(f"Platega webhook error: {e}")

    return "OK"


@router.post("/webhook/paypalych", summary="PayPalych webhook", include_in_schema=False)
async def paypalych_webhook(request: Request, db: AsyncSession = Depends(get_db)) -> str:
    """Handle PayPalych bill webhook."""
    try:
        data = await request.json()
    except Exception as e:
        log.error(f"PayPalych webhook: invalid JSON: {e}")
        return "ERROR"

    bill_id = data.get("bill_id", "")
    status = data.get("status", "")

    if status not in ("PAID", "SUCCESS", "COMPLETED"):
        return "OK"

    custom_raw = data.get("custom", "")
    try:
        from app.services.telegram_notify import TelegramNotifyService

        parts = custom_raw.split("_")
        if parts[0] == "pp" and len(parts) >= 3:
            payment_id = int(parts[1])
            plan_id = int(parts[2])
            extend_key_id = int(parts[3]) if len(parts) > 3 else None
        else:
            log.error(f"PayPalych webhook: unknown custom format: {custom_raw}")
            return "OK"

        payment, key, just_confirmed, just_processed = await _finalize_subscription_payment(
            db,
            int(payment_id),
            bill_id,
            plan_id,
            extend_key_id,
        )
        if not payment:
            log.error(f"PayPalych webhook: payment {payment_id} not found")
            return "OK"
        if not just_confirmed and not just_processed:
            log.info(f"PayPalych webhook: duplicate or stale payment ignored: {payment_id}")
            return "OK"

        await db.commit()

        settings = await BotSettingsService(db).get_all()
        success_msg = settings.get("payment_success_message", "✅ Оплата прошла успешно!")

        if key:
            if extend_key_id:
                exp = key.expires_at.strftime("%d.%m.%Y") if key.expires_at else "—"
                text = f"{success_msg}\n\n🔄 <b>Подписка продлена!</b>\n📅 Новая дата: <b>{exp}</b>"
            else:
                text = f"{success_msg}\n\n🔑 <b>Ваш VPN ключ:</b>\n<code>{key.access_url}</code>"
        else:
            text = f"{success_msg}\n\n⚠️ Ключ не создан, обратитесь в поддержку."

        await TelegramNotifyService().send_message(payment.user_id, text)
        log.info(f"PayPalych: payment {payment_id} confirmed via webhook")

    except Exception as e:
        log.error(f"PayPalych webhook error: {e}")

    return "OK"


@router.post("/webhook/aikassa", summary="AiKassa webhook", include_in_schema=False)
async def aikassa_webhook(request: Request, db: AsyncSession = Depends(get_db)) -> str:
    """Handle AiKassa webhook."""
    form = await request.form()
    invoice_id = str(form.get("invoice_id", ""))
    status = str(form.get("status", ""))

    if status not in ("paid", "success", "completed"):
        return "OK"

    payload_raw = form.get("orderId", "")
    try:
        from app.services.telegram_notify import TelegramNotifyService

        parts = payload_raw.split("_")
        if parts[0] == "ak" and len(parts) >= 3:
            payment_id = int(parts[1])
            plan_id = int(parts[2])
            extend_key_id = int(parts[3]) if len(parts) > 3 else None
        else:
            log.error(f"AiKassa webhook: unknown orderId format: {payload_raw}")
            return "OK"

        payment, key, just_confirmed, just_processed = await _finalize_subscription_payment(
            db,
            int(payment_id),
            invoice_id,
            plan_id,
            extend_key_id,
        )
        if not payment:
            log.error(f"AiKassa webhook: payment {payment_id} not found")
            return "OK"
        if not just_confirmed and not just_processed:
            log.info(f"AiKassa webhook: duplicate or stale payment ignored: {payment_id}")
            return "OK"

        await db.commit()

        settings = await BotSettingsService(db).get_all()
        success_msg = settings.get("payment_success_message", "✅ Оплата прошла успешно!")

        if key:
            if extend_key_id:
                exp = key.expires_at.strftime("%d.%m.%Y") if key.expires_at else "—"
                text = f"{success_msg}\n\n🔄 <b>Подписка продлена!</b>\n📅 Новая дата: <b>{exp}</b>"
            else:
                text = f"{success_msg}\n\n🔑 <b>Ваш VPN ключ:</b>\n<code>{key.access_url}</code>"
        else:
            text = f"{success_msg}\n\n⚠️ Ключ не создан, обратитесь в поддержку."

        await TelegramNotifyService().send_message(payment.user_id, text)
        log.info(f"AiKassa: payment {payment_id} confirmed via webhook")

    except Exception as e:
        log.error(f"AiKassa webhook error: {e}")

    return "OK"
