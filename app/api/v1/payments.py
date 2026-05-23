from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from decimal import Decimal, InvalidOperation
from typing import Optional
import hashlib

from app.api.dependencies import get_db, get_current_admin
from app.core.config import config
from app.models.payment import PaymentStatus, PaymentType
from app.schemas.payment import PaymentCreate, PaymentRead
from app.services.bot_settings import BotSettingsService
from app.services.payment import PaymentService
from app.services.payment_fulfillment import (
    PaymentFulfillmentService,
    TopupFulfillmentResult,
)
from app.services.plan import PlanService
from app.utils.log import log

router = APIRouter()
YOOKASSA_ALLOWED_IPS = {
    "185.71.76.0/27",
    "185.71.77.0/27",
    "77.75.153.0/25",
    "77.75.156.11",
    "77.75.156.35",
    "77.75.154.128/25",
    "2a02:5180::/32",
}


async def _get_yookassa_webhook_secret(db: AsyncSession) -> str:
    override = (
        await BotSettingsService(db).get("yookassa_secret_key_override") or ""
    ).strip()
    if override:
        return override
    fallback = config.yookassa.yookassa_secret_key
    return fallback.get_secret_value().strip() if fallback else ""


def _money_equal(left, right) -> bool:
    try:
        return Decimal(str(left)).quantize(Decimal("0.01")) == Decimal(
            str(right)
        ).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return False


async def _verify_remote_provider_payment(
    db: AsyncSession,
    *,
    provider: str,
    external_id: str,
    payment_id: int,
) -> bool:
    """Verify unsigned provider callbacks against the provider API.

    Some aggregators do not send a webhook HMAC in this integration. Before
    provisioning access we ask the provider API for the authoritative status,
    and compare it with our pending payment amount.
    """
    payment = await PaymentService(db).get_by_id(payment_id)
    if not payment:
        log.warning(
            f"{provider} webhook: payment {payment_id} not found before remote verification"
        )
        return False

    settings = await BotSettingsService(db).get_all()

    try:
        if provider == "platega":
            from app.services.platega import PlategaService

            svc = PlategaService.from_settings(settings)
            if not svc:
                log.error("Platega webhook: service not configured")
                return False
            remote = await svc.get_transaction_status(external_id)
            status_raw = PlategaService.normalize_status(remote.get("status", ""))
            amount_raw = (remote.get("payment_details") or {}).get("amount")
            if not remote.get("ok") or not PlategaService.is_success_status(status_raw):
                log.warning(
                    f"Platega webhook: remote status is not confirmed for {external_id}: {status_raw}"
                )
                return False
            if amount_raw is not None and not _money_equal(amount_raw, payment.amount):
                log.warning(
                    f"Platega webhook: amount mismatch for payment {payment_id}"
                )
                return False
            return True

        if provider == "paypalych":
            from app.services.paypalych import PayPalychService

            svc = PayPalychService.from_settings(settings)
            if not svc:
                log.error("PayPalych webhook: service not configured")
                return False
            remote = await svc.get_payment_status(external_id)
            status_raw = str(remote.get("status", "")).upper()
            if not remote.get("ok") or status_raw not in {"SUCCESS", "OVERPAID"}:
                log.warning(
                    f"PayPalych webhook: remote status is not paid for {external_id}: {status_raw}"
                )
                return False
            amount_raw = remote.get("account_amount")
            if amount_raw is None:
                amount_raw = remote.get("amount")
            if amount_raw is not None and not _money_equal(amount_raw, payment.amount):
                log.warning(
                    f"PayPalych webhook: amount mismatch for payment {payment_id}"
                )
                return False
            return True

        if provider == "aikassa":
            from app.services.aikassa import AiKassaService

            svc = AiKassaService.from_settings(settings)
            if not svc:
                log.error("AiKassa webhook: service not configured")
                return False
            remote = await svc.get_invoice(external_id)
            status_raw = str((remote or {}).get("status", "")).lower()
            if not remote or status_raw not in {"paid", "success", "completed"}:
                log.warning(
                    f"AiKassa webhook: remote status is not paid for {external_id}: {status_raw}"
                )
                return False
            amount_raw = remote.get("amount")
            if amount_raw is not None and not _money_equal(amount_raw, payment.amount):
                log.warning(
                    f"AiKassa webhook: amount mismatch for payment {payment_id}"
                )
                return False
            return True
    except Exception as exc:
        log.error(f"{provider} webhook: remote verification failed: {exc}")
        return False

    return False


def _platega_headers_match(request: Request, settings: dict) -> bool:
    from app.services.platega import PlategaService

    svc = PlategaService.from_settings(settings)
    if not svc:
        log.error("Platega webhook: provider is not configured")
        return False

    merchant_expected = svc.merchant_id.strip()
    secret_expected = svc.api_secret.strip()

    merchant_actual = (request.headers.get("X-MerchantId") or "").strip()
    secret_actual = (request.headers.get("X-Secret") or "").strip()

    if merchant_actual != merchant_expected or secret_actual != secret_expected:
        log.warning("Platega webhook: invalid auth headers")
        return False

    return True


def _compute_paypalych_signature(out_sum: str, inv_id: str, api_token: str) -> str:
    payload = f"{out_sum}:{inv_id}:{api_token}"
    return (
        hashlib.md5(payload.encode("utf-8"), usedforsecurity=False).hexdigest().upper()
    )


def _verify_paypalych_signature(
    out_sum: str,
    inv_id: str,
    signature: str,
    api_token: str,
) -> bool:
    expected = _compute_paypalych_signature(out_sum, inv_id, api_token)
    return expected == (signature or "").strip().upper()


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
        log.warning(
            f"Topup success notification failed for user {payment_user_id}: {e}"
        )


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
    return await PaymentService(db).get_all(
        limit=limit, offset=offset, status=status, user_id=user_id
    )


@router.get("/{payment_id}", response_model=PaymentRead, summary="Get payment")
async def get_payment(
    payment_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> PaymentRead:
    payment = await PaymentService(db).get_by_id(payment_id)
    if not payment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found"
        )
    return payment


@router.post(
    "/",
    response_model=PaymentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create pending payment",
)
async def create_payment(
    data: PaymentCreate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> PaymentRead:
    plan = await PlanService(db).get_by_id(data.plan_id)
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found"
        )
    return await PaymentService(db).create_pending(data.user_id, plan, data.provider)


@router.post(
    "/{payment_id}/refund", response_model=PaymentRead, summary="Refund payment"
)
async def refund_payment(
    payment_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> PaymentRead:
    payment = await PaymentService(db).refund(payment_id)
    if not payment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found"
        )
    return payment


@router.post("/webhook/freekassa", summary="FreeKassa webhook", include_in_schema=False)
async def freekassa_webhook(
    request: Request, db: AsyncSession = Depends(get_db)
) -> str:
    """
    URL оповещения для FreeKassa.
    Укажи в личном кабинете: https://project.cfd/api/v1/payments/webhook/freekassa
    """
    import ipaddress
    from app.services.freekassa import FreeKassaService

    client_ip = request.headers.get("X-Real-IP") or request.client.host
    try:
        if ipaddress.ip_address(client_ip) not in {
            ipaddress.ip_address(ip) for ip in FreeKassaService.ALLOWED_IPS
        }:
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
                    log.info(
                        f"FreeKassa: duplicate topup webhook ignored for payment {payment_id}"
                    )
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
    from app.services.telegram_notify import TelegramNotifyService

    payment, key, just_confirmed, just_processed = await _finalize_subscription_payment(
        db,
        payment_id,
        str(form.get("intid", "")),
        plan_id,
        key_id if "key_id" in locals() else None,
    )
    if not payment:
        log.error(f"FreeKassa webhook: payment {payment_id} not found")
        return "YES"
    if not just_confirmed and not just_processed:
        log.info(f"FreeKassa webhook: duplicate or stale payment ignored: {payment_id}")
        return "YES"

    if "key_id" in locals():
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

        success_msg = settings.get(
            "payment_success_message", "✅ Оплата прошла успешно!"
        )
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
async def yookassa_webhook(
    request: Request, db: AsyncSession = Depends(get_db)
) -> dict:
    """Atomic YooKassa webhook: verify source IP, confirm payment + provision."""
    import json
    import ipaddress

    try:
        raw_body = await request.body()
        data = json.loads(raw_body)
    except Exception as e:
        log.error(f"Yookassa webhook: invalid JSON body: {e}")
        return {"status": "error", "message": "invalid body"}

    client_ip = (
        request.headers.get("X-Real-IP")
        or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or (request.client.host if request.client else "")
    )
    try:
        addr = ipaddress.ip_address(client_ip)
        allowed = {
            ipaddress.ip_network(value, strict=False)
            if "/" in value
            else ipaddress.ip_network(f"{value}/32", strict=False)
            for value in YOOKASSA_ALLOWED_IPS
        }
        if not any(addr in network for network in allowed):
            log.warning("Yookassa webhook: blocked IP %s", client_ip)
            return {"status": "error", "message": "forbidden"}
    except ValueError:
        log.warning("Yookassa webhook: invalid IP %s", client_ip)
        return {"status": "error", "message": "forbidden"}

    event = data.get("event")
    obj = data.get("object", {})
    external_id = obj.get("id")
    metadata = obj.get("metadata", {})
    payment_id = metadata.get("payment_id")
    plan_id = metadata.get("plan_id")
    extend_key_id = metadata.get("extend_key_id")

    log.info(
        f"Yookassa webhook: event={event} payment_id={payment_id} plan_id={plan_id} extend_key_id={extend_key_id}"
    )

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
        payment = await PaymentService(db).get_by_id(int(payment_id))
        if not payment:
            log.warning(
                "Yookassa webhook: payment %s not found before processing", payment_id
            )
            return {"status": "ok"}

        status_value = str(obj.get("status", "")).lower().strip()
        if status_value and status_value != "succeeded":
            log.warning(
                "Yookassa webhook: unexpected object status %s for payment %s",
                status_value,
                payment_id,
            )
            return {"status": "ok"}

        if payment.payment_type == PaymentType.TOPUP.value:
            result = await _finalize_topup_payment(
                db, int(payment_id), str(external_id)
            )
            if not result.payment:
                log.warning(f"Yookassa topup: payment {payment_id} not found")
            elif not result.just_processed:
                log.info(
                    f"Yookassa topup: duplicate or stale webhook ignored for payment {payment_id}"
                )
            else:
                log.info(
                    f"Yookassa topup: payment {payment_id} confirmed + balance credited"
                )
            return {"status": "ok"}

        if not plan_id:
            log.warning(
                "Yookassa webhook: subscription payment %s missing plan_id metadata",
                payment_id,
            )
            return {"status": "ok"}

        (
            payment,
            key,
            just_confirmed,
            just_processed,
        ) = await _finalize_subscription_payment(
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
            log.info(
                f"Yookassa: duplicate or stale webhook ignored for payment {payment_id}"
            )
            return {"status": "ok"}

        await db.commit()

        try:
            from app.services.telegram_notify import TelegramNotifyService
            from app.services.bot_settings import BotSettingsService

            settings = await BotSettingsService(db).get_all()
            success_msg = settings.get(
                "payment_success_message", "Оплата прошла успешно!"
            )
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
async def cryptobot_webhook(
    request: Request, db: AsyncSession = Depends(get_db)
) -> dict:
    """Handle CryptoBot invoice paid webhook with signature verification."""
    try:
        raw_body = await request.body()
        import json

        data = json.loads(raw_body)
    except Exception as e:
        log.error(f"CryptoBot webhook: invalid JSON: {e}")
        return {"ok": False}

    sig_header = request.headers.get("X-Crypto-Pay-API-Signature", "").strip()
    from app.services.webhook_security import verify_cryptobot_signature

    cb_token = (await BotSettingsService(db).get("cryptobot_token") or "").strip()
    if not cb_token:
        log.error("CryptoBot webhook: token is not configured")
        return {"ok": False}
    if not sig_header:
        log.warning("CryptoBot webhook: missing signature header")
        return {"ok": False}
    if not verify_cryptobot_signature(raw_body, sig_header, cb_token):
        log.warning("CryptoBot webhook: invalid signature")
        return {"ok": False}

    invoice = data.get("payload", {})
    invoice_id = invoice.get("invoice_id")
    status = invoice.get("status", "")

    if status != "paid":
        return {"ok": True}

    payload_raw = invoice.get("payload", "")
    try:
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
                log.info(
                    f"CryptoBot topup: duplicate or stale payment ignored: {payment_id}"
                )
            else:
                log.info(
                    f"CryptoBot topup: payment {payment_id} confirmed + balance credited"
                )
            return {"ok": True}

        parts = payload_raw.split("_")
        if len(parts) >= 3 and parts[0] == "cb":
            payment_id = int(parts[1])
            plan_id = int(parts[2])
            extend_key_id = int(parts[3]) if len(parts) > 3 else None
        else:
            log.error(f"CryptoBot webhook: unknown payload format: {payload_raw}")
            return {"ok": True}

        (
            payment,
            key,
            just_confirmed,
            just_processed,
        ) = await _finalize_subscription_payment(
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
            log.info(
                f"CryptoBot webhook: duplicate or stale payment ignored: {payment_id}"
            )
            return {"ok": True}

        await db.commit()

        settings = await BotSettingsService(db).get_all()
        success_msg = settings.get(
            "payment_success_message", "✅ Оплата прошла успешно!"
        )

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

    settings = await BotSettingsService(db).get_all()
    if not _platega_headers_match(request, settings):
        return "OK"

    from app.services.platega import PlategaService

    transaction_id = str(data.get("id", "")).strip()
    status = PlategaService.normalize_status(data.get("status", ""))

    if not PlategaService.is_success_status(status):
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

        payment = await PaymentService(db).get_by_id(payment_id)
        if not payment:
            log.error(f"Platega webhook: payment {payment_id} not found")
            return "OK"

        callback_amount = data.get("amount")
        if callback_amount is not None and not _money_equal(
            callback_amount, payment.amount
        ):
            log.warning(
                f"Platega webhook: callback amount mismatch for payment {payment_id}"
            )
            return "OK"

        if not await _verify_remote_provider_payment(
            db,
            provider="platega",
            external_id=str(transaction_id),
            payment_id=payment_id,
        ):
            return "OK"

        (
            payment,
            key,
            just_confirmed,
            just_processed,
        ) = await _finalize_subscription_payment(
            db,
            int(payment_id),
            transaction_id,
            plan_id,
            extend_key_id,
        )
        if not payment:
            log.info(
                f"Platega webhook: duplicate or stale payment ignored: {payment_id}"
            )
            return "OK"
        if not just_confirmed and not just_processed:
            log.info(
                f"Platega webhook: duplicate or stale payment ignored: {payment_id}"
            )
            return "OK"

        await db.commit()

        settings = await BotSettingsService(db).get_all()
        success_msg = settings.get(
            "payment_success_message", "✅ Оплата прошла успешно!"
        )

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
async def paypalych_webhook(
    request: Request, db: AsyncSession = Depends(get_db)
) -> str:
    """Handle PayPalych bill webhook."""
    try:
        data = await request.form()
    except Exception as e:
        log.error(f"PayPalych webhook: invalid form payload: {e}")
        return "ERROR"

    status = str(data.get("Status", "")).upper().strip()
    order_id = str(data.get("InvId", "")).strip()
    transaction_id = str(data.get("TrsId", "")).strip()
    custom_raw = str(data.get("custom", "")).strip()
    out_sum = str(data.get("OutSum", "")).strip()
    signature = str(data.get("SignatureValue", "")).strip()

    if status not in {"SUCCESS", "OVERPAID"}:
        return "OK"

    try:
        from app.services.telegram_notify import TelegramNotifyService
        from app.services.paypalych import PayPalychService

        settings = await BotSettingsService(db).get_all()
        svc = PayPalychService.from_settings(settings)
        if not svc:
            log.error("PayPalych webhook: service not configured")
            return "OK"

        if not _verify_paypalych_signature(out_sum, order_id, signature, svc.api_token):
            log.warning("PayPalych webhook: invalid signature")
            return "OK"

        parts = custom_raw.split("_")
        if parts[0] == "pp" and len(parts) >= 3:
            payment_id = int(parts[1])
            plan_id = int(parts[2])
            extend_key_id = int(parts[3]) if len(parts) > 3 else None
        else:
            log.error(f"PayPalych webhook: unknown custom format: {custom_raw}")
            return "OK"

        payment = await PaymentService(db).get_by_id(payment_id)
        if not payment:
            log.error(f"PayPalych webhook: payment {payment_id} not found")
            return "OK"

        if out_sum and not _money_equal(out_sum, payment.amount):
            log.warning(
                f"PayPalych webhook: callback amount mismatch for payment {payment_id}"
            )
            return "OK"

        if not await _verify_remote_provider_payment(
            db,
            provider="paypalych",
            external_id=str(transaction_id),
            payment_id=payment_id,
        ):
            return "OK"

        (
            payment,
            key,
            just_confirmed,
            just_processed,
        ) = await _finalize_subscription_payment(
            db,
            int(payment_id),
            transaction_id,
            plan_id,
            extend_key_id,
        )
        if not payment:
            log.info(
                f"PayPalych webhook: duplicate or stale payment ignored: {payment_id}"
            )
            return "OK"
        if not just_confirmed and not just_processed:
            log.info(
                f"PayPalych webhook: duplicate or stale payment ignored: {payment_id}"
            )
            return "OK"

        await db.commit()

        settings = await BotSettingsService(db).get_all()
        success_msg = settings.get(
            "payment_success_message", "✅ Оплата прошла успешно!"
        )

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

        if not await _verify_remote_provider_payment(
            db,
            provider="aikassa",
            external_id=str(invoice_id),
            payment_id=payment_id,
        ):
            return "OK"

        (
            payment,
            key,
            just_confirmed,
            just_processed,
        ) = await _finalize_subscription_payment(
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
            log.info(
                f"AiKassa webhook: duplicate or stale payment ignored: {payment_id}"
            )
            return "OK"

        await db.commit()

        settings = await BotSettingsService(db).get_all()
        success_msg = settings.get(
            "payment_success_message", "✅ Оплата прошла успешно!"
        )

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
