import asyncio
from aiogram import Router, F, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message, PreCheckoutQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.keyboards.main import back_kb
from app.bot.utils.menu import get_main_menu_kb as _get_menu_kb
from app.bot.handlers.admin import _is_admin
from app.core.database import AsyncSessionFactory
from app.models.payment import PaymentProvider, PaymentStatus
from app.services.payment import PaymentService
from app.services.payment_fulfillment import PaymentFulfillmentService
from app.services.plan import PlanService
from app.services.vpn_key import VpnKeyService
from app.services.bot_settings import BotSettingsService
from app.services.user import UserService
from app.services.telegram_stars import TelegramStarsService
from app.services.cryptobot import CryptoBotService
from app.services.freekassa import FreeKassaService
from app.services.platega import PlategaService
from app.services.telegram_notify import TelegramNotifyService
from app.services.admin_events import (
    notify_admins_balance_topup,
    notify_admins_new_purchase,
)
from app.services.i18n import t, get_lang
from app.bot.utils.subscription_links import subscription_link_kb
from app.bot.utils.media import resolve_photo_input
from app.utils.html_utils import escape_html, html_code
from app.utils.log import log

router = Router()


async def _safe_callback_answer(
    callback: CallbackQuery, text: str | None = None, *, show_alert: bool = False
) -> bool:
    """Answer callback queries without crashing on expired Telegram query ids."""
    try:
        if text is None:
            await callback.answer()
        else:
            await callback.answer(text, show_alert=show_alert)
        return True
    except TelegramBadRequest as exc:
        error_text = str(exc).lower()
        if (
            "query is too old" in error_text
            or "query id is invalid" in error_text
            or "response timeout expired" in error_text
        ):
            log.warning(
                "Skipped stale callback answer for user %s: %s",
                callback.from_user.id if callback.from_user else "unknown",
                exc,
            )
            return False
        raise


async def _get_user_lang(user_id: int, session) -> str:
    user = await UserService(session).get_by_id(user_id)
    settings = await BotSettingsService(session).get_all()
    user_lang = user.language if user and user.language else None
    return get_lang(settings, user_lang)


def _payment_already_confirmed_text(lang: str) -> str:
    return {
        "ru": "Оплата уже подтверждена",
        "en": "Payment already confirmed",
        "fa": "پرداخت قبلا تایید شده است",
    }.get(lang, "Payment already confirmed")


async def _provision_with_retry(session, user_id: int, plan, max_retries: int = 3):
    """Retry VPN provisioning with backoff."""
    for attempt in range(max_retries):
        try:
            key = await VpnKeyService(session).provision(user_id=user_id, plan=plan)
            if key:
                return key
        except Exception as e:
            log.warning(f"[bot provision] attempt {attempt + 1}/{max_retries}: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2**attempt)
    return None


async def _provision_and_notify(
    user_id: int,
    payment_id: int,
    plan_id: int,
    bot: Bot,
    force_notify: bool = False,
    force_admin_notify: bool = False,
) -> bool:
    """Создаём VPN-подписку и уведомляем пользователя.

    CRITICAL: Extract ALL ORM scalars before closing session to avoid DetachedInstanceError.
    """
    key_data = None
    plan_data = None
    payment_amount = None
    payment_currency = "RUB"
    payment_provider = "—"
    should_notify_user = force_notify
    should_notify_admins = force_admin_notify
    settings = {}

    async with AsyncSessionFactory() as session:
        plan = await PlanService(session).get_by_id(plan_id)
        if not plan:
            log.error(f"[provision] Plan {plan_id} not found for payment {payment_id}")
            return False

        payment = await PaymentService(session).get_by_id(payment_id)
        if not payment or payment.user_id != user_id:
            log.error(f"[provision] Payment {payment_id} not found for user {user_id}")
            return False

        plan_data = {
            "name": plan.name,
            "duration_days": plan.duration_days,
            "price": str(plan.price),
        }

        delivery = await PaymentFulfillmentService(session).provision_subscription_once(
            payment_id, user_id, plan
        )
        payment = delivery.payment
        key = delivery.key
        should_notify_user = force_notify or delivery.just_processed
        should_notify_admins = force_admin_notify or delivery.just_processed

        if payment:
            payment_amount = str(payment.amount)
            payment_currency = payment.currency or "RUB"
            payment_provider = payment.provider or "—"

        await session.commit()

        if key:
            key_data = {
                "id": key.id,
                "access_url": key.access_url,
            }

    if not should_notify_user and not should_notify_admins:
        return True

    user_info = {"lang": "ru"}

    async with AsyncSessionFactory() as session:
        settings = await BotSettingsService(session).get_all()
        user = await UserService(session).get_by_id(user_id)
        user_lang = user.language if user and user.language else None
        user_info["lang"] = get_lang(settings, user_lang)

    lang = user_info["lang"]
    plan_name = escape_html(plan_data["name"])
    plan_days = plan_data["duration_days"]
    plan_price = plan_data["price"]

    if key_data and key_data["access_url"]:
        success_msg = settings.get("payment_success_message") or t(
            "payment_success", lang
        )
        text = f"{success_msg}\n\n" + t(
            "subscription_url",
            lang,
            url=escape_html(key_data["access_url"]),
            days=plan_days,
        )
    else:
        maintenance_enabled = settings.get("maintenance_mode", "0") == "1"
        if maintenance_enabled:
            maintenance_msg = (
                settings.get("maintenance_message")
                or "⛔️ Ведутся технические работы. Напишите через час."
            )
            text = maintenance_msg
        else:
            text = "✅ Оплата прошла успешно! Ваш VPN-ключ готовится. Проверьте раздел «Мои ключи» через пару минут."

    if should_notify_user:
        try:
            reply_markup = None
            if key_data and key_data["access_url"]:
                reply_markup = subscription_link_kb(
                    key_data["access_url"],
                    lang=lang,
                )
            await bot.send_message(
                user_id,
                text,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
        except Exception as e:
            log.warning(f"Failed to notify user {user_id}: {e}")

    if should_notify_admins:
        await notify_admins_new_purchase(
            user_id=user_id,
            payment_id=payment_id,
            plan_name=plan_data["name"],
            amount=payment_amount or plan_price,
            currency=payment_currency,
            provider=str(payment_provider),
            plan_days=plan_days,
            key_issued=bool(key_data),
        )

    if should_notify_admins:
        try:
            from app.services.notification import notification_manager

            await notification_manager.broadcast(
                {
                    "type": "new_payment",
                    "data": {
                        "payment_id": payment_id,
                        "user_id": user_id,
                        "amount": payment_amount or plan_price,
                        "currency": payment_currency,
                    },
                }
            )
        except Exception as e:
            log.warning(f"[bot] WebSocket broadcast failed: {e}")
    return True


# ── Balance ───────────────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("pay:balance:"))
async def handle_balance_payment(callback: CallbackQuery, bot: Bot) -> None:
    plan_id = int(callback.data.split(":")[2])

    async with AsyncSessionFactory() as session:
        plan = await PlanService(session).get_by_id(plan_id)
        lang = await _get_user_lang(callback.from_user.id, session)
        if not plan or not plan.is_active:
            await callback.answer(t("no_plans", lang), show_alert=True)
            return

        user = await UserService(session).get_by_id(callback.from_user.id)
        balance = float(user.balance or 0) if user else 0.0

        if balance < float(plan.price):
            await callback.answer(
                f"❌ {'Недостаточно средств' if lang == 'ru' else 'Insufficient funds'}. {balance:.2f} ₽ / {plan.price} ₽",
                show_alert=True,
            )
            return

    await callback.answer("⏳", show_alert=False)

    async with AsyncSessionFactory() as session:
        plan = await PlanService(session).get_by_id(plan_id)
        if not plan or not plan.is_active:
            await bot.send_message(callback.from_user.id, "❌ Тариф недоступен")
            return

        updated = await UserService(session).deduct_balance(
            callback.from_user.id, plan.price
        )
        if not updated:
            await bot.send_message(callback.from_user.id, "❌ Ошибка списания средств")
            return

        payment = await PaymentService(session).create_pending(
            user_id=callback.from_user.id,
            plan=plan,
            provider=PaymentProvider.BALANCE,
        )
        confirmation = await PaymentService(session).confirm_once(
            payment.id, f"balance_{payment.id}"
        )
        if not confirmation.payment:
            await session.rollback()
            await bot.send_message(callback.from_user.id, "❌ Ошибка оплаты")
            return

        delivery = await PaymentFulfillmentService(session).provision_subscription_once(
            payment.id, callback.from_user.id, plan
        )
        if not delivery.key:
            await UserService(session).add_balance(callback.from_user.id, plan.price)
            await PaymentService(session).fail(payment.id)
            await session.commit()
            await bot.send_message(
                callback.from_user.id,
                "❌ Не удалось создать ключ. Баланс возвращён, попробуйте позже.",
            )
            return

        payment_id = payment.id
        plan_id_saved = plan.id
        await session.commit()

    await _provision_and_notify(
        callback.from_user.id, payment_id, plan_id_saved, bot, force_notify=True
    )


# ── YooKassa ──────────────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("pay:yookassa:"))
async def handle_yookassa_payment(callback: CallbackQuery, bot: Bot) -> None:
    plan_id = int(callback.data.split(":")[2])

    async with AsyncSessionFactory() as session:
        plan = await PlanService(session).get_by_id(plan_id)
        lang = await _get_user_lang(callback.from_user.id, session)
        if not plan or not plan.is_active:
            await _safe_callback_answer(callback, t("no_plans", lang), show_alert=True)
            return

        await _safe_callback_answer(callback, "⏳", show_alert=False)

        try:
            from app.services.yookassa import YookassaService

            yk = await YookassaService.create()

            payment = await PaymentService(session).create_pending(
                user_id=callback.from_user.id,
                plan=plan,
                provider=PaymentProvider.YOOKASSA,
            )
            await session.flush()
            payment_id = payment.id

            me = await bot.get_me()
            return_url = f"https://t.me/{me.username}"

            yk_payment = await yk.create_payment(
                amount=plan.price,
                description=f"Подписка на {plan.name}",
                return_url=return_url,
                metadata={"payment_id": str(payment.id), "plan_id": str(plan.id)},
            )
            payment.external_id = yk_payment.id
            await session.commit()

            confirm_url = yk_payment.confirmation.confirmation_url
            builder = InlineKeyboardBuilder()
            builder.row(
                InlineKeyboardButton(text=t("payment_go", lang), url=confirm_url)
            )
            builder.row(
                InlineKeyboardButton(
                    text=t("payment_check", lang),
                    callback_data=f"yk:check:{payment_id}:{plan.id}",
                )
            )
            builder.row(
                InlineKeyboardButton(text=t("back", lang), callback_data="back_main")
            )

            try:
                from app.bot.utils.media import edit_with_photo

                await edit_with_photo(
                    callback,
                    f"💳 <b>{t('pay_card', lang)}</b>\n\n"
                    f"{plan.name} — {plan.price} ₽\n\n"
                    f"{'После оплаты нажмите «Проверить оплату».' if lang == 'ru' else 'After payment press Check payment.'}",
                    reply_markup=builder.as_markup(),
                )
            except Exception:
                pass
        except Exception as e:
            log.error(f"Yookassa error for user {callback.from_user.id}: {e}")
            async with AsyncSessionFactory() as s2:
                kb = await _get_menu_kb(
                    s2,
                    lang=lang,
                    user_id=callback.from_user.id,
                    is_admin=_is_admin(callback.from_user.id),
                )
            try:
                from app.bot.utils.media import edit_with_photo

                await edit_with_photo(
                    callback, t("payment_error", lang), reply_markup=kb
                )
            except Exception:
                pass


@router.callback_query(F.data.startswith("yk:check:"))
async def handle_yookassa_check(callback: CallbackQuery, bot: Bot) -> None:
    parts = callback.data.split(":")
    payment_id = int(parts[2])
    plan_id = int(parts[3])

    async with AsyncSessionFactory() as session:
        lang = await _get_user_lang(callback.from_user.id, session)
        payment = await PaymentService(session).get_by_id(payment_id)
        if not payment or payment.user_id != callback.from_user.id:
            await _safe_callback_answer(callback, "❌", show_alert=True)
            return
        if not payment.external_id:
            await _safe_callback_answer(
                callback, t("payment_error", lang), show_alert=True
            )
            return

        if payment.status == PaymentStatus.SUCCEEDED.value and payment.vpn_key_id:
            await _safe_callback_answer(
                callback, _payment_already_confirmed_text(lang), show_alert=True
            )
            return

        try:
            from app.services.yookassa import YookassaService

            yk = await YookassaService.create()
            yk_payment = await yk.get_payment(payment.external_id)
            if yk_payment.status == "succeeded":
                plan = await PlanService(session).get_by_id(plan_id)
                if not plan:
                    await _safe_callback_answer(
                        callback, t("payment_error", lang), show_alert=True
                    )
                    return

                confirmation = await PaymentService(session).confirm_once(
                    payment_id, yk_payment.id
                )
                delivery = await PaymentFulfillmentService(
                    session
                ).provision_subscription_once(payment_id, callback.from_user.id, plan)
                await session.commit()
                await _safe_callback_answer(
                    callback, t("payment_success", lang), show_alert=True
                )
                should_notify_user = (
                    confirmation.just_confirmed or delivery.just_processed
                )
                if should_notify_user:
                    await _provision_and_notify(
                        callback.from_user.id,
                        payment_id,
                        plan_id,
                        bot,
                        force_notify=True,
                        force_admin_notify=delivery.just_processed,
                    )
            elif yk_payment.status == "canceled":
                payment.status = PaymentStatus.FAILED.value
                await session.commit()
                await _safe_callback_answer(
                    callback, t("payment_failed", lang), show_alert=True
                )
            else:
                await _safe_callback_answer(
                    callback, t("payment_pending", lang), show_alert=True
                )
        except Exception as e:
            log.error(f"YooKassa check error: {e}")
            await _safe_callback_answer(
                callback, t("payment_error", lang), show_alert=True
            )


# ── СБП (ЮКасса) ─────────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("pay:sbp:"))
async def handle_sbp_payment(callback: CallbackQuery, bot: Bot) -> None:
    plan_id = int(callback.data.split(":")[2])

    async with AsyncSessionFactory() as session:
        plan = await PlanService(session).get_by_id(plan_id)
        lang = await _get_user_lang(callback.from_user.id, session)
        if not plan or not plan.is_active:
            await _safe_callback_answer(callback, t("no_plans", lang), show_alert=True)
            return

        await _safe_callback_answer(callback, "⏳", show_alert=False)

        try:
            from app.services.yookassa import YookassaService

            yk = await YookassaService.create()

            payment = await PaymentService(session).create_pending(
                user_id=callback.from_user.id,
                plan=plan,
                provider=PaymentProvider.YOOKASSA_SBP,
            )
            await session.flush()
            payment_id = payment.id

            me = await bot.get_me()
            return_url = f"https://t.me/{me.username}"

            yk_payment = await yk.create_sbp_payment(
                amount=plan.price,
                description=f"Подписка на {plan.name}",
                return_url=return_url,
                metadata={"payment_id": str(payment.id), "plan_id": str(plan.id)},
            )
            payment.external_id = yk_payment.id
            await session.commit()

            confirm_url = yk_payment.confirmation.confirmation_url
            sbp_title = {
                "ru": "🏦 Оплата через СБП",
                "en": "🏦 SBP Payment",
                "fa": "🏦 پرداخت SBP",
            }
            sbp_hint = {
                "ru": "После оплаты нажмите «Проверить оплату».",
                "en": "After payment press Check payment.",
                "fa": "پس از پرداخت، بررسی پرداخت را فشار دهید.",
            }
            builder = InlineKeyboardBuilder()
            builder.row(
                InlineKeyboardButton(text=t("payment_go", lang), url=confirm_url)
            )
            builder.row(
                InlineKeyboardButton(
                    text=t("payment_check", lang),
                    callback_data=f"yk:check:{payment_id}:{plan.id}",
                )
            )
            builder.row(
                InlineKeyboardButton(text=t("back", lang), callback_data="back_main")
            )

            from app.bot.utils.media import edit_with_photo

            await edit_with_photo(
                callback,
                f"🏦 <b>{sbp_title.get(lang, sbp_title['ru'])}</b>\n\n"
                f"{plan.name} — {plan.price} ₽\n\n"
                f"{sbp_hint.get(lang, sbp_hint['ru'])}",
                reply_markup=builder.as_markup(),
            )
        except Exception as e:
            log.error(f"SBP error for user {callback.from_user.id}: {e}")
            async with AsyncSessionFactory() as s2:
                kb = await _get_menu_kb(
                    s2,
                    lang=lang,
                    user_id=callback.from_user.id,
                    is_admin=_is_admin(callback.from_user.id),
                )
            from app.bot.utils.media import edit_with_photo

            await edit_with_photo(callback, t("payment_error", lang), reply_markup=kb)


# ── Telegram Stars ────────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("pay:stars:"))
async def handle_stars_payment(callback: CallbackQuery, bot: Bot) -> None:
    plan_id = int(callback.data.split(":")[2])

    async with AsyncSessionFactory() as session:
        plan = await PlanService(session).get_by_id(plan_id)
        lang = await _get_user_lang(callback.from_user.id, session)
        if not plan or not plan.is_active:
            await callback.answer(t("no_plans", lang), show_alert=True)
            return

        stars = TelegramStarsService.rub_to_stars(
            float(plan.price), rate=await TelegramStarsService.get_rate(session)
        )
        payment = await PaymentService(session).create_pending(
            user_id=callback.from_user.id,
            plan=plan,
            provider=PaymentProvider.TELEGRAM_STARS,
        )
        await session.commit()
        payment_id = payment.id

    ok = await TelegramStarsService(bot).send_invoice(
        chat_id=callback.from_user.id,
        title=f"Подписка на {plan.name}",
        description=f"{plan.duration_days} {'дней' if lang == 'ru' else 'days'}",
        payload=f"stars:{payment_id}:{plan_id}",
        stars_amount=stars,
    )

    try:
        if ok:
            from app.bot.utils.media import edit_with_photo

            await edit_with_photo(
                callback,
                t("pay_stars", lang, stars=stars),
                reply_markup=back_kb(lang),
            )
        else:
            async with AsyncSessionFactory() as s2:
                kb = await _get_menu_kb(
                    s2,
                    lang=lang,
                    user_id=callback.from_user.id,
                    is_admin=_is_admin(callback.from_user.id),
                )
            from app.bot.utils.media import edit_with_photo

            await edit_with_photo(callback, t("payment_error", lang), reply_markup=kb)
    except Exception:
        pass

    await callback.answer()


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery) -> None:
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment(message: Message, bot: Bot) -> None:
    """Единый обработчик всех Stars-платежей: подписки и пополнение баланса."""
    payload = message.successful_payment.invoice_payload
    charge_id = message.successful_payment.telegram_payment_charge_id

    # ── Пополнение баланса через Stars ───────────────────────────────────────
    if payload.startswith("topup_stars:"):
        try:
            _, payment_id_str, _amount_str = payload.split(":")
            payment_id = int(payment_id_str)
        except (ValueError, AttributeError):
            log.error(f"Invalid topup_stars payload: {payload}")
            return
        await _topup_confirm_balance(payment_id, charge_id, bot)
        return

    # ── Продление подписки через Stars ────────────────────────────────────────
    if payload.startswith("extend_stars:"):
        try:
            _, payment_id_str, plan_id_str, key_id_str = payload.split(":")
            payment_id = int(payment_id_str)
            plan_id = int(plan_id_str)
            key_id = int(key_id_str)
        except (ValueError, AttributeError):
            log.error(f"Invalid extend_stars payload: {payload}")
            return
        extended_key = None
        plan_days = 0
        async with AsyncSessionFactory() as session:
            payment = await PaymentService(session).get_by_id(payment_id)
            plan = await PlanService(session).get_by_id(plan_id)
            if payment and plan and payment.user_id == message.from_user.id:
                plan_days = plan.duration_days
                await PaymentService(session).confirm_once(payment_id, charge_id)
                result = await PaymentFulfillmentService(
                    session
                ).extend_subscription_once(
                    payment_id, message.from_user.id, key_id, plan
                )
                extended_key = result.key
                await session.commit()
        if extended_key:
            exp = (
                extended_key.expires_at.strftime("%d.%m.%Y")
                if extended_key.expires_at
                else "—"
            )
            try:
                await bot.send_message(
                    message.from_user.id,
                    f"✅ <b>Подписка продлена!</b>\n\nДо: <b>{exp}</b>\n+{plan_days} дней",
                    parse_mode="HTML",
                )
            except Exception as e:
                log.warning(f"Failed to notify extend user: {e}")
        return

    # ── Оплата подписки через Stars ───────────────────────────────────────────
    try:
        _, payment_id_str, plan_id_str = payload.split(":")
        payment_id = int(payment_id_str)
        plan_id = int(plan_id_str)
    except (ValueError, AttributeError):
        log.error(f"Invalid Stars payment payload: {payload}")
        return

    async with AsyncSessionFactory() as session:
        payment = await PaymentService(session).get_by_id(payment_id)
        plan = await PlanService(session).get_by_id(plan_id)
        if not payment or not plan or payment.user_id != message.from_user.id:
            return
        await PaymentService(session).confirm_once(payment_id, charge_id)
        await PaymentFulfillmentService(session).provision_subscription_once(
            payment_id, message.from_user.id, plan
        )
        await session.commit()

    await _provision_and_notify(
        message.from_user.id,
        payment_id,
        plan_id,
        bot,
        force_notify=True,
        force_admin_notify=True,
    )


# ── CryptoBot ─────────────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("pay:crypto:"))
async def handle_crypto_payment(callback: CallbackQuery, bot: Bot) -> None:
    plan_id = int(callback.data.split(":")[2])

    async with AsyncSessionFactory() as session:
        plan = await PlanService(session).get_by_id(plan_id)
        settings = await BotSettingsService(session).get_all()
        lang = await _get_user_lang(callback.from_user.id, session)

        if not plan or not plan.is_active:
            await callback.answer(t("no_plans", lang), show_alert=True)
            return

        crypto = CryptoBotService.from_settings(settings)
        if not crypto:
            await callback.answer(t("payment_error", lang), show_alert=True)
            return

        try:
            usdt_amount = await crypto.rub_to_usdt(float(plan.price))
            payment = await PaymentService(session).create_pending(
                user_id=callback.from_user.id,
                plan=plan,
                provider=PaymentProvider.CRYPTOBOT,
            )
            await session.flush()
            payment_id = payment.id

            invoice = await crypto.create_invoice(
                amount=usdt_amount,
                currency="USDT",
                description=f"Подписка на {plan.name}",
                payload=f"crypto:{payment_id}:{plan_id}",
            )

            if not invoice:
                await session.rollback()
                await callback.answer(t("payment_error", lang), show_alert=True)
                return

            payment.external_id = str(invoice["invoice_id"])
            await session.commit()

            pay_url = invoice.get("pay_url", "")
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text=t("payment_go", lang), url=pay_url))
            builder.row(
                InlineKeyboardButton(
                    text=t("payment_check", lang),
                    callback_data=f"crypto:check:{payment_id}:{plan_id}",
                )
            )
            builder.row(
                InlineKeyboardButton(text=t("back", lang), callback_data="back_main")
            )

            from app.bot.utils.media import edit_with_photo

            await edit_with_photo(
                callback,
                f"₿ <b>{t('pay_crypto', lang)}</b>\n\n"
                f"{plan.name} — {plan.price} ₽ (~{usdt_amount} USDT)\n\n"
                f"{t('payment_check', lang)}.",
                reply_markup=builder.as_markup(),
            )
        except Exception as e:
            log.error(f"CryptoBot error for user {callback.from_user.id}: {e}")
            async with AsyncSessionFactory() as s2:
                kb = await _get_menu_kb(
                    s2,
                    lang=lang,
                    user_id=callback.from_user.id,
                    is_admin=_is_admin(callback.from_user.id),
                )
            from app.bot.utils.media import edit_with_photo

            await edit_with_photo(callback, t("payment_error", lang), reply_markup=kb)

    await callback.answer()


@router.callback_query(F.data.startswith("crypto:check:"))
async def handle_crypto_check(callback: CallbackQuery, bot: Bot) -> None:
    parts = callback.data.split(":")
    payment_id = int(parts[2])
    plan_id = int(parts[3])

    external_id = None

    async with AsyncSessionFactory() as session:
        lang = await _get_user_lang(callback.from_user.id, session)
        payment = await PaymentService(session).get_by_id(payment_id)
        if not payment or payment.user_id != callback.from_user.id:
            await callback.answer("❌", show_alert=True)
            return

        if payment.status == PaymentStatus.SUCCEEDED.value and payment.vpn_key_id:
            await callback.answer(
                _payment_already_confirmed_text(lang), show_alert=True
            )
            return

        settings = await BotSettingsService(session).get_all()
        crypto = CryptoBotService.from_settings(settings)
        if not crypto or not payment.external_id:
            await callback.answer(t("payment_error", lang), show_alert=True)
            return

        external_id = payment.external_id

    try:
        invoice = await crypto.get_invoice(int(external_id))
        if invoice and invoice.get("status") == "paid":
            async with AsyncSessionFactory() as session:
                payment = await PaymentService(session).get_by_id(payment_id)
                plan = await PlanService(session).get_by_id(plan_id)
                if not payment or not plan or payment.user_id != callback.from_user.id:
                    await callback.answer(t("payment_error", lang), show_alert=True)
                    return
                confirmation = await PaymentService(session).confirm_once(
                    payment_id, str(invoice.get("invoice_id") or external_id)
                )
                delivery = await PaymentFulfillmentService(
                    session
                ).provision_subscription_once(payment_id, callback.from_user.id, plan)
                await session.commit()
            await callback.answer(t("payment_success", lang), show_alert=True)
            should_notify_user = confirmation.just_confirmed or delivery.just_processed
            if should_notify_user:
                await _provision_and_notify(
                    callback.from_user.id,
                    payment_id,
                    plan_id,
                    bot,
                    force_notify=True,
                    force_admin_notify=delivery.just_processed,
                )
        else:
            await callback.answer(t("payment_pending", lang), show_alert=True)
    except Exception as e:
        log.error(f"CryptoBot check error: {e}")
        await callback.answer(t("payment_error", lang), show_alert=True)


# ── Пополнение баланса ────────────────────────────────────────────────────────


async def _topup_confirm_balance(payment_id: int, external_id: str, bot: Bot) -> bool:
    """Confirm a top-up exactly once, credit balance, and notify the user."""
    amount = 0
    balance = 0.0
    lang = "ru"
    photo = None
    user_id = 0
    should_notify = False

    async with AsyncSessionFactory() as session:
        result = await PaymentFulfillmentService(session).confirm_topup_and_credit_once(
            payment_id, external_id
        )
        payment = result.payment
        if not payment:
            await session.rollback()
            return False

        user_id = payment.user_id
        amount = payment.amount
        should_notify = result.just_processed
        settings = await BotSettingsService(session).get_all()
        u = await UserService(session).get_by_id(user_id)
        user_lang = u.language if u and u.language else None
        lang = get_lang(settings, user_lang)
        photo = await BotSettingsService(session).get("photo_status") or None
        balance = float(
            result.balance
            if result.balance is not None
            else (u.balance if u else 0) or 0
        )
        await session.commit()

    if not should_notify:
        return True

    text = t("topup_success", lang, amount=amount, balance=balance)
    try:
        if photo:
            await bot.send_photo(
                user_id,
                photo=resolve_photo_input(photo),
                caption=text,
                parse_mode="HTML",
            )
        else:
            await bot.send_message(user_id, text, parse_mode="HTML")
    except Exception as e:
        log.warning(f"Failed to notify topup user {user_id}: {e}")
    try:
        await notify_admins_balance_topup(
            user_id=user_id,
            payment_id=payment_id,
            amount=str(amount),
            balance=f"{balance:.2f}",
            currency=str(result.payment.currency or "RUB"),
            provider=str(result.payment.provider or "topup"),
        )
    except Exception as e:
        log.warning(f"Failed to notify admins about topup {payment_id}: {e}")
    return True


@router.callback_query(F.data.startswith("topup:pay:yookassa:"))
async def topup_yookassa(callback: CallbackQuery, bot: Bot) -> None:
    from decimal import Decimal

    amount = Decimal(callback.data.split(":")[3])

    async with AsyncSessionFactory() as session:
        lang = await _get_user_lang(callback.from_user.id, session)
        try:
            from app.services.yookassa import YookassaService

            yk = await YookassaService.create()
            me = await bot.get_me()
            return_url = f"https://t.me/{me.username}"

            payment = await PaymentService(session).create_topup_pending(
                user_id=callback.from_user.id,
                amount=amount,
                provider=PaymentProvider.YOOKASSA,
            )
            await session.flush()
            payment_id = payment.id

            yk_payment = await yk.create_payment(
                amount=amount,
                description="Пополнение баланса",
                return_url=return_url,
                metadata={"payment_id": str(payment.id)},
            )
            payment.external_id = yk_payment.id
            await session.commit()

            confirm_url = yk_payment.confirmation.confirmation_url
            builder = InlineKeyboardBuilder()
            builder.row(
                InlineKeyboardButton(text=t("payment_go", lang), url=confirm_url)
            )
            builder.row(
                InlineKeyboardButton(
                    text=t("payment_check", lang),
                    callback_data=f"topup:check:yookassa:{yk_payment.id}:{amount}:{payment_id}",
                )
            )
            builder.row(
                InlineKeyboardButton(text=t("back", lang), callback_data="topup:menu")
            )

            from app.bot.utils.media import edit_with_photo

            await edit_with_photo(
                callback,
                f"💳 <b>{'Пополнение карточкой' if lang == 'ru' else 'Card top up'}</b>\n\n"
                f"{amount} ₽\n\n"
                f"{'После оплаты нажмите «Проверить».' if lang == 'ru' else 'After payment press Check.'}",
                reply_markup=builder.as_markup(),
            )
        except Exception as e:
            log.error(f"Topup YooKassa error: {e}")
            async with AsyncSessionFactory() as s2:
                kb = await _get_menu_kb(
                    s2,
                    lang=lang,
                    user_id=callback.from_user.id,
                    is_admin=_is_admin(callback.from_user.id),
                )
            from app.bot.utils.media import edit_with_photo

            await edit_with_photo(callback, t("payment_error", lang), reply_markup=kb)

    await callback.answer()


@router.callback_query(F.data.startswith("topup:check:yookassa:"))
async def topup_check_yookassa(callback: CallbackQuery, bot: Bot) -> None:
    parts = callback.data.split(":")
    payment_id = int(parts[5]) if len(parts) > 5 else 0

    async with AsyncSessionFactory() as session:
        lang = await _get_user_lang(callback.from_user.id, session)
        if payment_id:
            existing = await PaymentService(session).get_by_id(payment_id)
            if not existing or existing.user_id != callback.from_user.id:
                await callback.answer(t("payment_error", lang), show_alert=True)
                return
            if existing.status == PaymentStatus.SUCCEEDED.value:
                await callback.answer(
                    f"✅ {'Уже зачислено!' if lang == 'ru' else 'Already credited!'}",
                    show_alert=True,
                )
                return
            if not existing.external_id:
                await callback.answer(t("payment_error", lang), show_alert=True)
                return

        try:
            from app.services.yookassa import YookassaService

            yk = await YookassaService.create()
            yk_payment = await yk.get_payment(existing.external_id)
            if yk_payment.status == "succeeded":
                await _topup_confirm_balance(payment_id, yk_payment.id, bot)
                await callback.answer(
                    f"✅ {'Баланс пополнен!' if lang == 'ru' else 'Balance topped up!'}",
                    show_alert=True,
                )
            else:
                await callback.answer(t("payment_pending", lang), show_alert=True)
        except Exception as e:
            log.error(f"Topup YooKassa check error: {e}")
            await callback.answer(t("payment_error", lang), show_alert=True)


@router.callback_query(F.data.startswith("topup:pay:sbp:"))
async def topup_sbp(callback: CallbackQuery, bot: Bot) -> None:
    from decimal import Decimal

    amount = Decimal(callback.data.split(":")[3])

    async with AsyncSessionFactory() as session:
        lang = await _get_user_lang(callback.from_user.id, session)
        try:
            from app.services.yookassa import YookassaService

            yk = await YookassaService.create()
            me = await bot.get_me()
            return_url = f"https://t.me/{me.username}"

            payment = await PaymentService(session).create_topup_pending(
                user_id=callback.from_user.id,
                amount=amount,
                provider=PaymentProvider.YOOKASSA_SBP,
            )
            await session.flush()
            payment_id = payment.id

            yk_payment = await yk.create_sbp_payment(
                amount=amount,
                description="Пополнение баланса через СБП",
                return_url=return_url,
                metadata={"payment_id": str(payment.id)},
            )
            payment.external_id = yk_payment.id
            await session.commit()

            confirm_url = yk_payment.confirmation.confirmation_url
            builder = InlineKeyboardBuilder()
            builder.row(
                InlineKeyboardButton(text=t("payment_go", lang), url=confirm_url)
            )
            builder.row(
                InlineKeyboardButton(
                    text=t("payment_check", lang),
                    callback_data=f"topup:check:yookassa:{yk_payment.id}:{amount}:{payment_id}",
                )
            )
            builder.row(
                InlineKeyboardButton(text=t("back", lang), callback_data="topup:menu")
            )

            from app.bot.utils.media import edit_with_photo

            await edit_with_photo(
                callback,
                f"🏦 <b>{'Пополнение СБП' if lang == 'ru' else 'SBP top up'}</b>\n\n"
                f"{amount} ₽\n\n"
                f"{'После оплаты нажмите «Проверить».' if lang == 'ru' else 'After payment press Check.'}",
                reply_markup=builder.as_markup(),
            )
        except Exception as e:
            log.error(f"Topup SBP error: {e}")
            async with AsyncSessionFactory() as s2:
                kb = await _get_menu_kb(
                    s2,
                    lang=lang,
                    user_id=callback.from_user.id,
                    is_admin=_is_admin(callback.from_user.id),
                )
            from app.bot.utils.media import edit_with_photo

            await edit_with_photo(callback, t("payment_error", lang), reply_markup=kb)

    await callback.answer()


@router.callback_query(F.data.startswith("topup:pay:crypto:"))
async def topup_crypto(callback: CallbackQuery, bot: Bot) -> None:
    from decimal import Decimal

    amount = Decimal(callback.data.split(":")[3])

    async with AsyncSessionFactory() as session:
        lang = await _get_user_lang(callback.from_user.id, session)
        settings = await BotSettingsService(session).get_all()
        crypto = CryptoBotService.from_settings(settings)
        if not crypto:
            await callback.answer(t("topup_error", lang), show_alert=True)
            return

        try:
            usdt_amount = await crypto.rub_to_usdt(float(amount))
            payment = await PaymentService(session).create_topup_pending(
                user_id=callback.from_user.id,
                amount=amount,
                provider=PaymentProvider.CRYPTOBOT,
            )
            await session.flush()
            payment_id = payment.id

            invoice = await crypto.create_invoice(
                amount=usdt_amount,
                currency="USDT",
                description="Пополнение баланса",
                payload=f"topup_crypto:{payment_id}:{amount}",
            )

            if not invoice:
                await session.rollback()
                await callback.answer(t("topup_error", lang), show_alert=True)
                return

            payment.external_id = str(invoice["invoice_id"])
            await session.commit()

            pay_url = invoice.get("pay_url", "")
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text=t("payment_go", lang), url=pay_url))
            builder.row(
                InlineKeyboardButton(
                    text=t("payment_check", lang),
                    callback_data=f"topup:check:crypto:{invoice['invoice_id']}:{amount}:{payment_id}",
                )
            )
            builder.row(
                InlineKeyboardButton(text=t("back", lang), callback_data="topup:menu")
            )

            from app.bot.utils.media import edit_with_photo

            await edit_with_photo(
                callback,
                f"₿ <b>{'Пополнение криптой' if lang == 'ru' else 'Crypto top up'}</b>\n\n"
                f"{amount} ₽ (~{usdt_amount} USDT)\n\n"
                f"{'После оплаты нажмите «Проверить».' if lang == 'ru' else 'After payment press Check.'}",
                reply_markup=builder.as_markup(),
            )
        except Exception as e:
            log.error(f"Topup crypto error: {e}")
            await callback.answer(t("topup_error", lang), show_alert=True)

    await callback.answer()


@router.callback_query(F.data.startswith("topup:check:crypto:"))
async def topup_check_crypto(callback: CallbackQuery, bot: Bot) -> None:
    parts = callback.data.split(":")
    payment_id = int(parts[5]) if len(parts) > 5 else 0

    crypto = None
    lang = "ru"
    external_id = None

    async with AsyncSessionFactory() as session:
        lang = await _get_user_lang(callback.from_user.id, session)
        if payment_id:
            existing = await PaymentService(session).get_by_id(payment_id)
            if not existing or existing.user_id != callback.from_user.id:
                await callback.answer(t("topup_error", lang), show_alert=True)
                return
            if existing.status == PaymentStatus.SUCCEEDED.value:
                await callback.answer(
                    f"✅ {'Уже зачислено!' if lang == 'ru' else 'Already credited!'}",
                    show_alert=True,
                )
                return
            if not existing.external_id:
                await callback.answer(t("topup_error", lang), show_alert=True)
                return
            external_id = existing.external_id
        settings = await BotSettingsService(session).get_all()
        crypto = CryptoBotService.from_settings(settings)
        if not crypto:
            await callback.answer(t("topup_error", lang), show_alert=True)
            return

    try:
        invoice = await crypto.get_invoice(int(external_id))
        if invoice and invoice.get("status") == "paid":
            await _topup_confirm_balance(
                payment_id, str(invoice.get("invoice_id") or external_id), bot
            )
            await callback.answer(
                f"✅ {'Баланс пополнен!' if lang == 'ru' else 'Balance topped up!'}",
                show_alert=True,
            )
        else:
            await callback.answer(t("payment_pending", lang), show_alert=True)
    except Exception as e:
        log.error(f"Topup crypto check error: {e}")
        await callback.answer(t("topup_error", lang), show_alert=True)


# ── Platega ───────────────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("pay:platega:"))
async def handle_platega_payment(callback: CallbackQuery, bot: Bot) -> None:
    plan_id = int(callback.data.split(":")[2])

    async with AsyncSessionFactory() as session:
        plan = await PlanService(session).get_by_id(plan_id)
        settings = await BotSettingsService(session).get_all()
        lang = await _get_user_lang(callback.from_user.id, session)
        if not plan or not plan.is_active:
            await callback.answer(t("no_plans", lang), show_alert=True)
            return

        platega = PlategaService.from_settings(settings)
        if not platega:
            await callback.answer(t("payment_error", lang), show_alert=True)
            return

        try:
            payment = await PaymentService(session).create_pending(
                user_id=callback.from_user.id,
                plan=plan,
                provider=PaymentProvider.PLATEGA,
            )
            await session.flush()
            payment_id = payment.id

            me = await bot.get_me()
            return_url = f"https://t.me/{me.username}"
            transaction = await platega.create_transaction(
                amount=float(plan.price),
                currency="RUB",
                description=f"Подписка на {plan.name}",
                return_url=return_url,
                failed_url=return_url,
                payload_data=f"pl_{payment_id}_{plan_id}",
                user_telegram_id=str(callback.from_user.id),
                user_id=str(callback.from_user.id),
            )
            if not transaction.get("ok") or not transaction.get("url"):
                await session.rollback()
                await callback.answer(t("payment_error", lang), show_alert=True)
                return

            payment.external_id = str(transaction.get("transaction_id") or "")
            await session.commit()

            builder = InlineKeyboardBuilder()
            builder.row(
                InlineKeyboardButton(text=t("payment_go", lang), url=transaction["url"])
            )
            builder.row(
                InlineKeyboardButton(
                    text=t("payment_check", lang),
                    callback_data=f"platega:check:{payment_id}:{plan.id}",
                )
            )
            builder.row(
                InlineKeyboardButton(text=t("back", lang), callback_data="back_main")
            )

            from app.bot.utils.media import edit_with_photo

            await edit_with_photo(
                callback,
                f"🟦 <b>Platega</b>\n\n"
                f"{plan.name} — {plan.price} ₽\n\n"
                f"{'После оплаты нажмите «Проверить оплату».' if lang == 'ru' else 'After payment press Check payment.'}",
                reply_markup=builder.as_markup(),
            )
        except Exception as e:
            log.error(f"Platega error for user {callback.from_user.id}: {e}")
            async with AsyncSessionFactory() as s2:
                kb = await _get_menu_kb(
                    s2,
                    lang=lang,
                    user_id=callback.from_user.id,
                    is_admin=_is_admin(callback.from_user.id),
                )
            from app.bot.utils.media import edit_with_photo

            await edit_with_photo(callback, t("payment_error", lang), reply_markup=kb)

    await callback.answer()


@router.callback_query(F.data.startswith("platega:check:"))
async def handle_platega_check(callback: CallbackQuery, bot: Bot) -> None:
    parts = callback.data.split(":")
    payment_id = int(parts[2])
    plan_id = int(parts[3])

    async with AsyncSessionFactory() as session:
        lang = await _get_user_lang(callback.from_user.id, session)
        payment = await PaymentService(session).get_by_id(payment_id)
        if not payment or payment.user_id != callback.from_user.id:
            await callback.answer("❌", show_alert=True)
            return
        if payment.status == PaymentStatus.SUCCEEDED.value and payment.vpn_key_id:
            await callback.answer(
                _payment_already_confirmed_text(lang), show_alert=True
            )
            return

        settings = await BotSettingsService(session).get_all()
        platega = PlategaService.from_settings(settings)
        if not platega or not payment.external_id:
            await callback.answer(t("payment_error", lang), show_alert=True)
            return

    try:
        transaction = await platega.get_transaction_status(payment.external_id)
        if transaction.get("ok") and PlategaService.is_success_status(
            transaction.get("status", "")
        ):
            async with AsyncSessionFactory() as session:
                payment = await PaymentService(session).get_by_id(payment_id)
                plan = await PlanService(session).get_by_id(plan_id)
                if not payment or not plan or payment.user_id != callback.from_user.id:
                    await callback.answer(t("payment_error", lang), show_alert=True)
                    return
                confirmation = await PaymentService(session).confirm_once(
                    payment_id,
                    str(transaction.get("transaction_id") or payment.external_id),
                )
                delivery = await PaymentFulfillmentService(
                    session
                ).provision_subscription_once(payment_id, callback.from_user.id, plan)
                await session.commit()
            await callback.answer(t("payment_success", lang), show_alert=True)
            should_notify_user = confirmation.just_confirmed or delivery.just_processed
            if should_notify_user:
                await _provision_and_notify(
                    callback.from_user.id,
                    payment_id,
                    plan_id,
                    bot,
                    force_notify=True,
                    force_admin_notify=delivery.just_processed,
                )
        else:
            await callback.answer(t("payment_pending", lang), show_alert=True)
    except Exception as e:
        log.error(f"Platega check error: {e}")
        await callback.answer(t("payment_error", lang), show_alert=True)


# ── FreeKassa ─────────────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("pay:freekassa:"))
async def handle_freekassa_payment(callback: CallbackQuery, bot: Bot) -> None:
    plan_id = int(callback.data.split(":")[2])

    async with AsyncSessionFactory() as session:
        plan = await PlanService(session).get_by_id(plan_id)
        lang = await _get_user_lang(callback.from_user.id, session)
        if not plan or not plan.is_active:
            await callback.answer(t("no_plans", lang), show_alert=True)
            return

        settings = await BotSettingsService(session).get_all()
        fk = FreeKassaService.from_settings(settings)
        if not fk:
            await callback.answer(t("payment_error", lang), show_alert=True)
            return

        try:
            payment = await PaymentService(session).create_pending(
                user_id=callback.from_user.id,
                plan=plan,
                provider=PaymentProvider.FREEKASSA,
            )
            await session.flush()
            payment_id = payment.id

            order_id = f"fk_{payment_id}_{plan_id}"
            pay_url = fk.create_payment_url(
                order_id=order_id,
                amount=float(plan.price),
                currency="RUB",
                lang="ru",
            )

            payment.external_id = order_id
            await session.commit()

            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text=t("payment_go", lang), url=pay_url))
            builder.row(
                InlineKeyboardButton(
                    text=t("payment_check", lang),
                    callback_data=f"freekassa:check:{payment_id}:{plan_id}",
                )
            )
            builder.row(
                InlineKeyboardButton(text=t("back", lang), callback_data="back_main")
            )

            from app.bot.utils.media import edit_with_photo

            await edit_with_photo(
                callback,
                f"🟢 <b>{'Оплата через FreeKassa' if lang == 'ru' else 'FreeKassa Payment'}</b>\n\n"
                f"{plan.name} — {plan.price} ₽\n\n"
                f"{'После оплаты нажмите «Проверить оплату».' if lang == 'ru' else 'After payment press Check payment.'}",
                reply_markup=builder.as_markup(),
            )
        except Exception as e:
            log.error(f"FreeKassa error for user {callback.from_user.id}: {e}")
            async with AsyncSessionFactory() as s2:
                kb = await _get_menu_kb(
                    s2,
                    lang=lang,
                    user_id=callback.from_user.id,
                    is_admin=_is_admin(callback.from_user.id),
                )
            from app.bot.utils.media import edit_with_photo

            await edit_with_photo(callback, t("payment_error", lang), reply_markup=kb)

    await callback.answer()


@router.callback_query(F.data.startswith("freekassa:check:"))
async def handle_freekassa_check(callback: CallbackQuery, bot: Bot) -> None:
    parts = callback.data.split(":")
    payment_id = int(parts[2])
    plan_id = int(parts[3])

    async with AsyncSessionFactory() as session:
        lang = await _get_user_lang(callback.from_user.id, session)
        payment = await PaymentService(session).get_by_id(payment_id)
        if not payment or payment.user_id != callback.from_user.id:
            await callback.answer("❌", show_alert=True)
            return

        if payment.status == PaymentStatus.SUCCEEDED.value and payment.vpn_key_id:
            await callback.answer(
                _payment_already_confirmed_text(lang), show_alert=True
            )
            return

        if not payment.external_id:
            await callback.answer(t("payment_error", lang), show_alert=True)
            return

        settings = await BotSettingsService(session).get_all()
        fk = FreeKassaService.from_settings(settings)
        if not fk:
            await callback.answer(t("payment_error", lang), show_alert=True)
            return

    try:
        result = await fk.get_orders(payment.external_id)
        if result and result.get("orders"):
            order = result["orders"][0]
            if order.get("orderStatus") == 1:
                async with AsyncSessionFactory() as sess2:
                    payment = await PaymentService(sess2).get_by_id(payment_id)
                    plan = await PlanService(sess2).get_by_id(plan_id)
                    if (
                        not payment
                        or not plan
                        or payment.user_id != callback.from_user.id
                    ):
                        await callback.answer(t("payment_error", lang), show_alert=True)
                        return
                    confirmation = await PaymentService(sess2).confirm_once(
                        payment_id, payment.external_id or f"fk_{payment_id}"
                    )
                    delivery = await PaymentFulfillmentService(
                        sess2
                    ).provision_subscription_once(
                        payment_id, callback.from_user.id, plan
                    )
                    await sess2.commit()
                await callback.answer(t("payment_success", lang), show_alert=True)
                should_notify_user = (
                    confirmation.just_confirmed or delivery.just_processed
                )
                if should_notify_user:
                    await _provision_and_notify(
                        callback.from_user.id,
                        payment_id,
                        plan_id,
                        bot,
                        force_notify=True,
                        force_admin_notify=delivery.just_processed,
                    )
            else:
                await callback.answer(t("payment_pending", lang), show_alert=True)
        else:
            await callback.answer(t("payment_pending", lang), show_alert=True)
    except Exception as e:
        log.error(f"FreeKassa check error: {e}")
        await callback.answer(t("payment_error", lang), show_alert=True)


# ── Пополнение баланса через FreeKassa ────────────────────────────────────────


@router.callback_query(F.data.startswith("topup:pay:freekassa:"))
async def topup_freekassa(callback: CallbackQuery, bot: Bot) -> None:
    from decimal import Decimal

    amount = Decimal(callback.data.split(":")[3])

    async with AsyncSessionFactory() as session:
        lang = await _get_user_lang(callback.from_user.id, session)
        settings = await BotSettingsService(session).get_all()
        fk = FreeKassaService.from_settings(settings)
        if not fk:
            await callback.answer(t("topup_error", lang), show_alert=True)
            return

        try:
            payment = await PaymentService(session).create_topup_pending(
                user_id=callback.from_user.id,
                amount=amount,
                provider=PaymentProvider.FREEKASSA,
            )
            await session.flush()
            payment_id = payment.id

            order_id = f"fk_topup_{payment_id}"
            pay_url = fk.create_payment_url(
                order_id=order_id,
                amount=float(amount),
                currency="RUB",
                lang="ru",
            )

            payment.external_id = order_id
            await session.commit()

            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text=t("payment_go", lang), url=pay_url))
            builder.row(
                InlineKeyboardButton(
                    text=t("payment_check", lang),
                    callback_data=f"topup:check:freekassa:{payment_id}:{amount}",
                )
            )
            builder.row(
                InlineKeyboardButton(text=t("back", lang), callback_data="topup:menu")
            )

            from app.bot.utils.media import edit_with_photo

            await edit_with_photo(
                callback,
                f"🟢 <b>{'Пополнение через FreeKassa' if lang == 'ru' else 'FreeKassa top up'}</b>\n\n"
                f"{amount} ₽\n\n"
                f"{'После оплаты нажмите «Проверить».' if lang == 'ru' else 'After payment press Check.'}",
                reply_markup=builder.as_markup(),
            )
        except Exception as e:
            log.error(f"Topup FreeKassa error: {e}")
            await callback.answer(t("topup_error", lang), show_alert=True)

    await callback.answer()


@router.callback_query(F.data.startswith("topup:check:freekassa:"))
async def topup_check_freekassa(callback: CallbackQuery, bot: Bot) -> None:
    parts = callback.data.split(":")
    payment_id = int(parts[3])
    _amount_str = parts[4]

    async with AsyncSessionFactory() as session:
        lang = await _get_user_lang(callback.from_user.id, session)
        existing = await PaymentService(session).get_by_id(payment_id)
        if not existing or existing.user_id != callback.from_user.id:
            await callback.answer(t("topup_error", lang), show_alert=True)
            return
        if existing.status == PaymentStatus.SUCCEEDED.value:
            await callback.answer(
                f"✅ {'Уже зачислено!' if lang == 'ru' else 'Already credited!'}",
                show_alert=True,
            )
            return

        settings = await BotSettingsService(session).get_all()
        fk = FreeKassaService.from_settings(settings)
        if not fk:
            await callback.answer(t("topup_error", lang), show_alert=True)
            return

    try:
        result = await fk.get_orders(existing.external_id or f"fk_topup_{payment_id}")
        if result and result.get("orders"):
            order = result["orders"][0]
            if order.get("orderStatus") == 1:
                await _topup_confirm_balance(
                    payment_id, existing.external_id or f"fk_topup_{payment_id}", bot
                )
                await callback.answer(
                    f"✅ {'Баланс пополнен!' if lang == 'ru' else 'Balance topped up!'}",
                    show_alert=True,
                )
            else:
                await callback.answer(t("payment_pending", lang), show_alert=True)
        else:
            await callback.answer(t("payment_pending", lang), show_alert=True)
    except Exception as e:
        log.error(f"Topup FreeKassa check error: {e}")
        await callback.answer(t("topup_error", lang), show_alert=True)


@router.callback_query(F.data.startswith("topup:pay:platega:"))
async def topup_platega(callback: CallbackQuery, bot: Bot) -> None:
    from decimal import Decimal

    amount = Decimal(callback.data.split(":")[3])

    async with AsyncSessionFactory() as session:
        lang = await _get_user_lang(callback.from_user.id, session)
        settings = await BotSettingsService(session).get_all()
        platega = PlategaService.from_settings(settings)
        if not platega:
            await callback.answer(t("topup_error", lang), show_alert=True)
            return

        try:
            payment = await PaymentService(session).create_topup_pending(
                user_id=callback.from_user.id,
                amount=amount,
                provider=PaymentProvider.PLATEGA,
            )
            await session.flush()
            payment_id = payment.id

            me = await bot.get_me()
            return_url = f"https://t.me/{me.username}"
            transaction = await platega.create_transaction(
                amount=float(amount),
                currency="RUB",
                description="Пополнение баланса",
                return_url=return_url,
                failed_url=return_url,
                payload_data=f"pl_topup_{payment_id}",
                user_telegram_id=str(callback.from_user.id),
                user_id=str(callback.from_user.id),
            )
            if not transaction.get("ok") or not transaction.get("url"):
                await session.rollback()
                await callback.answer(t("topup_error", lang), show_alert=True)
                return

            payment.external_id = str(transaction.get("transaction_id") or "")
            await session.commit()

            builder = InlineKeyboardBuilder()
            builder.row(
                InlineKeyboardButton(text=t("payment_go", lang), url=transaction["url"])
            )
            builder.row(
                InlineKeyboardButton(
                    text=t("payment_check", lang),
                    callback_data=f"topup:check:platega:{payment_id}:{amount}",
                )
            )
            builder.row(
                InlineKeyboardButton(text=t("back", lang), callback_data="topup:menu")
            )

            from app.bot.utils.media import edit_with_photo

            await edit_with_photo(
                callback,
                f"🟦 <b>{'Пополнение через Platega' if lang == 'ru' else 'Platega top up'}</b>\n\n"
                f"{amount} ₽\n\n"
                f"{'После оплаты нажмите «Проверить».' if lang == 'ru' else 'After payment press Check.'}",
                reply_markup=builder.as_markup(),
            )
        except Exception as e:
            log.error(f"Topup Platega error: {e}")
            await callback.answer(t("topup_error", lang), show_alert=True)

    await callback.answer()


@router.callback_query(F.data.startswith("topup:check:platega:"))
async def topup_check_platega(callback: CallbackQuery, bot: Bot) -> None:
    parts = callback.data.split(":")
    payment_id = int(parts[3])

    async with AsyncSessionFactory() as session:
        lang = await _get_user_lang(callback.from_user.id, session)
        existing = await PaymentService(session).get_by_id(payment_id)
        if not existing or existing.user_id != callback.from_user.id:
            await callback.answer(t("topup_error", lang), show_alert=True)
            return
        if existing.status == PaymentStatus.SUCCEEDED.value:
            await callback.answer(
                f"✅ {'Уже зачислено!' if lang == 'ru' else 'Already credited!'}",
                show_alert=True,
            )
            return

        settings = await BotSettingsService(session).get_all()
        platega = PlategaService.from_settings(settings)
        if not platega or not existing.external_id:
            await callback.answer(t("topup_error", lang), show_alert=True)
            return

    try:
        transaction = await platega.get_transaction_status(existing.external_id)
        if transaction.get("ok") and PlategaService.is_success_status(
            transaction.get("status", "")
        ):
            await _topup_confirm_balance(
                payment_id,
                str(transaction.get("transaction_id") or existing.external_id),
                bot,
            )
            await callback.answer(
                f"✅ {'Баланс пополнен!' if lang == 'ru' else 'Balance topped up!'}",
                show_alert=True,
            )
        else:
            await callback.answer(t("payment_pending", lang), show_alert=True)
    except Exception as e:
        log.error(f"Topup Platega check error: {e}")
        await callback.answer(t("topup_error", lang), show_alert=True)


# ── Fallback ──────────────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("pay:"))
async def handle_payment_fallback(callback: CallbackQuery, bot: Bot) -> None:
    """Fallback for unhandled payment callbacks - shows helpful error."""
    user_id = callback.from_user.id

    async with AsyncSessionFactory() as session:
        lang = await _get_user_lang(user_id, session)

    error_messages = {
        "ru": "❌ Оплата недоступна. Попробуйте позже или выберите другой способ.",
        "en": "❌ Payment unavailable. Try later or choose another method.",
        "fa": "❌ پرداخت در دسترس نیست. بعداً امتحان کنید.",
    }

    error_msg = error_messages.get(lang, error_messages["ru"])

    log.warning(f"[payment_fallback] user={user_id} data={callback.data}")

    try:
        await callback.answer(error_msg, show_alert=True)
    except Exception:
        try:
            await bot.send_message(user_id, error_msg)
        except Exception as e:
            log.error(f"[payment_fallback] failed to notify user {user_id}: {e}")
