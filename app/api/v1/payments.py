from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.api.dependencies import get_db, get_current_admin
from app.models.payment import PaymentProvider, PaymentStatus
from app.schemas.payment import PaymentCreate, PaymentRead
from app.services.payment import PaymentService
from app.services.plan import PlanService
from app.utils.log import log

router = APIRouter()


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
    from app.services.bot_settings import BotSettingsService

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
                payment = await PaymentService(db).confirm_topup(payment_id, str(form.get("intid", "")))
                await db.flush()
                if payment and payment.status == PaymentStatus.SUCCEEDED.value:
                    from app.services.user import UserService
                    result = await UserService(db).add_balance(payment.user_id, payment.amount)
                    await db.commit()
                    if result:
                        log.info(f"FreeKassa: topup {payment_id} confirmed + balance credited")
                    else:
                        log.error(f"FreeKassa: topup {payment_id} confirmed but add_balance failed")
                else:
                    await db.commit()
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

    plan = await PlanService(db).get_by_id(plan_id)
    payment = await PaymentService(db).get_by_id(payment_id)

    if not payment:
        log.error(f"FreeKassa webhook: payment {payment_id} not found")
        return "YES"

    if payment.status == PaymentStatus.SUCCEEDED.value:
        return "YES"  # уже обработан

    payment.status = PaymentStatus.SUCCEEDED.value
    payment.external_id = str(form.get("intid", ""))
    await db.flush()

    if 'key_id' in locals():
        key = await VpnKeyService(db).extend(key_id, plan.duration_days)
        await db.commit()
        if key:
            exp = key.expires_at.strftime("%d.%m.%Y") if key.expires_at else "—"
            text = f"✅ Оплата прошла успешно!\n\n🔄 <b>Подписка продлена!</b>\n📅 Новая дата: <b>{exp}</b>\n➕ +{plan.duration_days} дней"
        else:
            text = "✅ Оплата прошла, но возникла ошибка продления. Обратитесь в поддержку."
    else:
        if not plan:
            log.error(f"FreeKassa webhook: plan {plan_id} not found")
            return "YES"
        key = await VpnKeyService(db).provision(user_id=payment.user_id, plan=plan)
        if key:
            payment.vpn_key_id = key.id
        await db.commit()

        success_msg = settings.get("payment_success_message", "✅ Оплата прошла успешно!")
        if key:
            text = f"{success_msg}\n\n🔑 <b>Ваш ключ:</b>\n<code>{key.access_url}</code>\n\n📅 Действует <b>{plan.duration_days} дней</b>"
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
    sig_header = request.headers.get("Authorization", "")
    if sig_header:
        from app.services.webhook_security import verify_yookassa_signature
        from app.services.bot_settings import BotSettingsService
        secret = ""
        try:
            secret = await BotSettingsService(db).get("yookassa_secret_key_override") or ""
        except Exception:
            pass
        if not secret:
            from app.core.config import config as _yk_cfg
            secret = _yk_cfg.yookassa.yookassa_secret_key.get_secret_value() if _yk_cfg.yookassa else ""
        if secret and not await verify_yookassa_signature(raw_body, sig_header, secret):
            log.warning(f"Yookassa webhook: invalid signature")
            return {"status": "error", "message": "invalid signature"}
    else:
        log.warning("Yookassa webhook: missing Authorization header")

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

    if event != "payment.succeeded" or not payment_id or not plan_id:
        return {"status": "ok"}

    try:
        plan = await PlanService(db).get_by_id(int(plan_id))
        if not plan:
            log.warning(f"Yookassa: plan {plan_id} not found")
            return {"status": "ok"}

        # Atomic confirm with FOR UPDATE — prevents double-spending
        payment = await PaymentService(db).confirm(int(payment_id), external_id)
        await db.commit()

        if not payment:
            log.warning(f"Yookassa: payment {payment_id} not found for confirmation")
            return {"status": "ok"}

        key = None
        last_error = None

        if extend_key_id:
            from app.services.vpn_key import VpnKeyService
            try:
                key = await VpnKeyService(db).extend(int(extend_key_id), plan.duration_days)
                await db.commit()
                log.info(f"Yookassa: key {extend_key_id} extended by {plan.duration_days} days")
            except Exception as e:
                last_error = e
                log.error(f"Yookassa: extend key {extend_key_id} failed: {e}")
        else:
            for attempt in range(3):
                try:
                    from app.services.vpn_key import VpnKeyService
                    key = await VpnKeyService(db).provision(user_id=payment.user_id, plan=plan)
                    if key:
                        break
                except Exception as e:
                    last_error = e
                    log.warning(f"Yookassa provision attempt {attempt + 1}/3 failed: {e}")
                    if attempt < 2:
                        await asyncio.sleep(1.5 * (attempt + 1))

            if key:
                payment.vpn_key_id = key.id
                await db.commit()
                log.info(f"Yookassa: payment={payment_id} key={key.id} provisioned")
            else:
                log.error(f"Yookassa: payment={payment_id} provisioning failed after 3 retries: {last_error}")

        try:
            from app.services.telegram_notify import TelegramNotifyService
            from app.services.bot_settings import BotSettingsService
            settings = await BotSettingsService(db).get_all()
            success_msg = settings.get("payment_success_message", "Оплата прошла успешно!")

            if extend_key_id and key:
                exp = key.expires_at.strftime("%d.%m.%Y") if key.expires_at else "—"
                text = (
                    f"✅ {success_msg}\n\n"
                    f"🔄 <b>Подписка продлена!</b>\n"
                    f"📅 Новая дата: <b>{exp}</b>\n"
                    f"➕ +{plan.duration_days} дней"
                )
            elif key:
                text = (
                    f"✅ {success_msg}\n\n"
                    f"🔑 <b>Ваш VPN ключ:</b>\n<code>{key.access_url}</code>\n\n"
                    f"📅 Действует <b>{plan.duration_days} дней</b>"
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
    import json
    try:
        data = await request.json()
    except Exception as e:
        log.error(f"CryptoBot webhook: invalid JSON: {e}")
        return {"ok": False}

    sig_header = request.headers.get("X-Crypto-Pay-API-Signature", "")
    if sig_header:
        from app.services.webhook_security import verify_cryptobot_signature
        from app.services.bot_settings import BotSettingsService
        settings = await BotSettingsService(db).get_all()
        cb_token = settings.get("cryptobot_token", "")
        if cb_token and not verify_cryptobot_signature(data, sig_header, cb_token):
            log.warning("CryptoBot webhook: invalid signature")
            return {"ok": False}
    else:
        log.warning("CryptoBot webhook: missing signature header")

    invoice = data.get("payload", {})
    invoice_id = invoice.get("invoice_id")
    status = invoice.get("status", "")

    if status != "paid":
        return {"ok": True}

    payload_raw = invoice.get("payload", "")
    try:
        from app.services.plan import PlanService
        from app.services.vpn_key import VpnKeyService
        from app.services.telegram_notify import TelegramNotifyService

        parts = payload_raw.split("_")
        if len(parts) >= 3 and parts[0] == "cb":
            payment_id = int(parts[1])
            plan_id = int(parts[2])
            extend_key_id = int(parts[3]) if len(parts) > 3 else None
        else:
            log.error(f"CryptoBot webhook: unknown payload format: {payload_raw}")
            return {"ok": True}

        payment = await PaymentService(db).confirm(int(payment_id), str(invoice_id))
        if not payment:
            log.error(f"CryptoBot webhook: payment {payment_id} not found")
            return {"ok": True}

        plan = await PlanService(db).get_by_id(plan_id)
        if not plan:
            log.error(f"CryptoBot webhook: plan {plan_id} not found")
            return {"ok": True}

        key = None
        if extend_key_id:
            key = await VpnKeyService(db).extend(extend_key_id, plan.duration_days)
        else:
            key = await VpnKeyService(db).provision(user_id=payment.user_id, plan=plan)

        if key:
            payment.vpn_key_id = key.id

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
    import json
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
        from app.services.plan import PlanService
        from app.services.vpn_key import VpnKeyService
        from app.services.telegram_notify import TelegramNotifyService

        parts = payload_raw.split("_")
        if parts[0] == "pl" and len(parts) >= 3:
            payment_id = int(parts[1])
            plan_id = int(parts[2])
            extend_key_id = int(parts[3]) if len(parts) > 3 else None
        else:
            log.error(f"Platega webhook: unknown payload format: {payload_raw}")
            return "OK"

        payment = await PaymentService(db).confirm(int(payment_id), transaction_id)
        if not payment:
            log.error(f"Platega webhook: payment {payment_id} not found")
            return "OK"

        plan = await PlanService(db).get_by_id(plan_id)
        if not plan:
            log.error(f"Platega webhook: plan {plan_id} not found")
            return "OK"

        key = None
        if extend_key_id:
            key = await VpnKeyService(db).extend(extend_key_id, plan.duration_days)
        else:
            key = await VpnKeyService(db).provision(user_id=payment.user_id, plan=plan)

        if key:
            payment.vpn_key_id = key.id

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
    import json
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
        from app.services.plan import PlanService
        from app.services.vpn_key import VpnKeyService
        from app.services.telegram_notify import TelegramNotifyService

        parts = custom_raw.split("_")
        if parts[0] == "pp" and len(parts) >= 3:
            payment_id = int(parts[1])
            plan_id = int(parts[2])
            extend_key_id = int(parts[3]) if len(parts) > 3 else None
        else:
            log.error(f"PayPalych webhook: unknown custom format: {custom_raw}")
            return "OK"

        payment = await PaymentService(db).confirm(int(payment_id), bill_id)
        if not payment:
            log.error(f"PayPalych webhook: payment {payment_id} not found")
            return "OK"

        plan = await PlanService(db).get_by_id(plan_id)
        if not plan:
            log.error(f"PayPalych webhook: plan {plan_id} not found")
            return "OK"

        key = None
        if extend_key_id:
            key = await VpnKeyService(db).extend(extend_key_id, plan.duration_days)
        else:
            key = await VpnKeyService(db).provision(user_id=payment.user_id, plan=plan)

        if key:
            payment.vpn_key_id = key.id

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
        from app.services.plan import PlanService
        from app.services.vpn_key import VpnKeyService
        from app.services.telegram_notify import TelegramNotifyService

        parts = payload_raw.split("_")
        if parts[0] == "ak" and len(parts) >= 3:
            payment_id = int(parts[1])
            plan_id = int(parts[2])
            extend_key_id = int(parts[3]) if len(parts) > 3 else None
        else:
            log.error(f"AiKassa webhook: unknown orderId format: {payload_raw}")
            return "OK"

        payment = await PaymentService(db).confirm(int(payment_id), invoice_id)
        if not payment:
            log.error(f"AiKassa webhook: payment {payment_id} not found")
            return "OK"

        plan = await PlanService(db).get_by_id(plan_id)
        if not plan:
            log.error(f"AiKassa webhook: plan {plan_id} not found")
            return "OK"

        key = None
        if extend_key_id:
            key = await VpnKeyService(db).extend(extend_key_id, plan.duration_days)
        else:
            key = await VpnKeyService(db).provision(user_id=payment.user_id, plan=plan)

        if key:
            payment.vpn_key_id = key.id

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
