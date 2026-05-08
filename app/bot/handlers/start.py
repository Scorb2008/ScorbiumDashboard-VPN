import secrets
from decimal import Decimal
from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


async def _safe_answer(callback: CallbackQuery) -> None:
    """Safely answer callback query, ignoring timeout errors."""
    try:
        await callback.answer()
    except Exception:
        pass


from app.bot.keyboards.main import back_kb
from app.bot.utils.menu import get_main_menu_kb as _get_menu_kb
from app.bot.handlers.admin import _is_admin
from app.core.database import AsyncSessionFactory
from app.schemas.user import UserCreate
from app.services.user import UserService
from app.services.referral import ReferralService
from app.services.promo import PromoService
from app.services.bot_settings import BotSettingsService
from app.services.support import SupportService
from app.services.telegram_notify import TelegramNotifyService
from app.services.i18n import t, get_lang
from app.core.config import config
from app.utils.html_utils import escape_html, truncate

router = Router()


class PromoState(StatesGroup):
    waiting_code = State()


class SupportState(StatesGroup):
    waiting_subject = State()
    waiting_message = State()
    replying_ticket = State()


class TopupState(StatesGroup):
    waiting_amount = State()


async def _get_lang_from_session(user_id: int, session) -> str:
    user = await UserService(session).get_by_id(user_id)
    settings = await BotSettingsService(session).get_all()
    user_lang = user.language if user and user.language else None
    return get_lang(settings, user_lang)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    args = message.text.split(maxsplit=1)
    ref_code = args[1].strip() if len(args) > 1 else None

    async with AsyncSessionFactory() as session:
        svc = UserService(session)
        user, created = await svc.get_or_create(
            UserCreate(
                id=message.from_user.id,
                username=message.from_user.username,
                full_name=message.from_user.full_name,
            )
        )

        if not user.referral_code:
            user.referral_code = secrets.token_urlsafe(6).upper()

        if created and ref_code and ref_code != user.referral_code:
            referrer = await svc.get_by_referral_code(ref_code)
            if referrer and referrer.id != user.id:
                ref_svc = ReferralService(session)
                settings_svc = BotSettingsService(session)
                bonus_type = await settings_svc.get("referral_bonus_type") or "days"
                bonus_value_str = await settings_svc.get("referral_bonus_value") or "3"
                from decimal import Decimal

                bonus_value = Decimal(bonus_value_str)
                bonus_days = int(bonus_value) if bonus_type == "days" else 0
                await ref_svc.create(
                    referrer_id=referrer.id,
                    referred_id=user.id,
                    bonus_days=bonus_days,
                    bonus_type=bonus_type,
                    bonus_value=bonus_value,
                )

        await session.commit()

        if created:
            from app.services.notification import notification_manager
            await notification_manager.broadcast({
                "type": "new_user",
                "data": {
                    "user_id": user.id,
                    "full_name": message.from_user.full_name or "",
                    "username": message.from_user.username or "",
                },
            })

        settings = await BotSettingsService(session).get_all()
        welcome_tpl = settings.get("welcome_message")
        user_lang = user.language if user and user.language else None
        lang = get_lang(settings, user_lang)
        kb = await _get_menu_kb(
            session,
            lang=lang,
            user_id=message.from_user.id,
            is_admin=_is_admin(message.from_user.id),
        )
        photo = settings.get("photo_welcome")

    if welcome_tpl:
        welcome = welcome_tpl.format(name=message.from_user.first_name)
        if not created:
            welcome = t("welcome_back", lang, name=message.from_user.first_name)
    else:
        welcome = t(
            "welcome" if created else "welcome_back",
            lang,
            name=message.from_user.first_name,
        )

    from app.bot.utils.media import answer_with_photo

    await answer_with_photo(message, welcome, reply_markup=kb, photo=photo or None)


@router.callback_query(F.data == "back_main")
async def back_to_main(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with AsyncSessionFactory() as session:
        lang = await _get_lang_from_session(callback.from_user.id, session)
        kb = await _get_menu_kb(
            session,
            lang=lang,
            user_id=callback.from_user.id,
            is_admin=_is_admin(callback.from_user.id),
        )
        photo = await BotSettingsService(session).get("photo_welcome")
    from app.bot.utils.media import edit_with_photo

    await edit_with_photo(
        callback, t("main_menu", lang), reply_markup=kb, photo=photo or None
    )
    await _safe_answer(callback)


@router.callback_query(F.data == "balance")
async def show_balance(callback: CallbackQuery) -> None:
    async with AsyncSessionFactory() as session:
        user = await UserService(session).get_by_id(callback.from_user.id)
        ref_count = await ReferralService(session).count_referrals(
            callback.from_user.id
        )
        settings = await BotSettingsService(session).get_all()
        balance = float(user.balance or 0) if user else 0.0
        referral_code = user.referral_code if user else None
        autorenew = bool(user.autorenew) if user else False
        photo = settings.get("photo_balance") or None
        user_lang = user.language if user and user.language else None
        lang = get_lang(settings, user_lang)

    bot_username = await _get_bot_username()
    ref_link = (
        f"https://t.me/{bot_username}?start={referral_code}"
        if referral_code and bot_username
        else "—"
    )

    bonus_type = settings.get("referral_bonus_type", "days")
    bonus_value = settings.get("referral_bonus_value", "3")
    bonus_labels = {
        "days": f"+{bonus_value} {'дней' if lang == 'ru' else ('روز' if lang == 'fa' else 'days')}",
        "balance": f"+{bonus_value} ₽",
        "percent": f"{bonus_value}%",
    }
    bonus_text = bonus_labels.get(bonus_type, f"+{bonus_value}")

    autorenew_line = t("autorenew_on", lang) if autorenew else t("autorenew_off", lang)

    text = (
        t("balance_title", lang, balance=balance)
        + "\n\n"
        + t("referrals_count", lang, count=ref_count)
        + "\n"
        + t("referral_bonus", lang, bonus=bonus_text)
        + "\n\n"
        + autorenew_line
        + "\n\n"
        + t("referral_link", lang, link=ref_link)
    )

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=t("btn_topup", lang), callback_data="topup:menu")
    )
    if autorenew:
        builder.row(
            InlineKeyboardButton(
                text=t("btn_autorenew_off", lang), callback_data="autorenew:off"
            )
        )
    else:
        builder.row(
            InlineKeyboardButton(
                text=t("btn_autorenew_on", lang), callback_data="autorenew:on"
            )
        )
    builder.row(
        InlineKeyboardButton(text=t("back_main", lang), callback_data="back_main")
    )

    from app.bot.utils.media import edit_with_photo

    await edit_with_photo(callback, text, reply_markup=builder.as_markup(), photo=photo)
    await _safe_answer(callback)


# ── Автосписание ──────────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("autorenew:"))
async def toggle_autorenew(callback: CallbackQuery) -> None:
    action = callback.data.split(":")[1]
    enabled = action == "on"

    async with AsyncSessionFactory() as session:
        user = await UserService(session).set_autorenew(callback.from_user.id, enabled)
        await session.commit()
        lang = await _get_lang_from_session(callback.from_user.id, session)

    msg = t("autorenew_enabled", lang) if enabled else t("autorenew_disabled", lang)
    await callback.answer(msg[:200], show_alert=True)
    # Обновляем экран баланса
    await show_balance(callback)


# ── Пополнение баланса ────────────────────────────────────────────────────────

_TOPUP_AMOUNTS = [100, 200, 500, 1000, 2000, 5000]


@router.callback_query(F.data == "topup:menu")
async def topup_menu(callback: CallbackQuery) -> None:
    async with AsyncSessionFactory() as session:
        lang = await _get_lang_from_session(callback.from_user.id, session)
        settings = await BotSettingsService(session).get_all()
        has_yookassa = bool(
            settings.get("yookassa_shop_id")
            or (config.yookassa and config.yookassa.yookassa_shop_id)
        )
        has_cryptobot = bool(settings.get("cryptobot_token", "").strip())

    builder = InlineKeyboardBuilder()
    # Быстрые суммы
    for amount in _TOPUP_AMOUNTS:
        builder.button(text=f"{amount} ₽", callback_data=f"topup:amount:{amount}")
    builder.adjust(3)
    builder.row(
        InlineKeyboardButton(text=t("topup_custom", lang), callback_data="topup:custom")
    )
    builder.row(InlineKeyboardButton(text=t("back", lang), callback_data="balance"))

    from app.bot.utils.media import edit_with_photo

    await edit_with_photo(
        callback, t("topup_title", lang), reply_markup=builder.as_markup()
    )
    await _safe_answer(callback)


@router.callback_query(F.data == "topup:custom")
async def topup_custom(callback: CallbackQuery, state: FSMContext) -> None:
    async with AsyncSessionFactory() as session:
        lang = await _get_lang_from_session(callback.from_user.id, session)
    await state.set_state(TopupState.waiting_amount)
    from app.bot.utils.media import edit_with_photo

    await edit_with_photo(
        callback, t("topup_enter_amount", lang), reply_markup=back_kb(lang)
    )
    await _safe_answer(callback)


@router.message(TopupState.waiting_amount)
async def topup_got_amount(message: Message, state: FSMContext) -> None:
    async with AsyncSessionFactory() as session:
        lang = await _get_lang_from_session(message.from_user.id, session)

    try:
        amount = Decimal(message.text.strip().replace(",", "."))
        if amount < 50 or amount > 100000:
            raise ValueError
    except (ValueError, Exception):
        await message.answer(t("topup_invalid_amount", lang))
        return

    await state.clear()
    await _show_topup_payment(message.from_user.id, amount, lang, message=message)


@router.callback_query(F.data.startswith("topup:amount:"))
async def topup_select_amount(callback: CallbackQuery) -> None:
    amount = Decimal(callback.data.split(":")[2])
    async with AsyncSessionFactory() as session:
        lang = await _get_lang_from_session(callback.from_user.id, session)
    try:
        await _safe_answer(callback)
    except Exception:
        pass
    await _show_topup_payment(callback.from_user.id, amount, lang, callback=callback)


async def _show_topup_payment(
    user_id: int,
    amount: Decimal,
    lang: str,
    message: Message = None,
    callback: CallbackQuery = None,
) -> None:
    """Показывает способы оплаты для пополнения баланса."""
    async with AsyncSessionFactory() as session:
        settings = await BotSettingsService(session).get_all()
        from app.services.telegram_stars import TelegramStarsService

        rate = await TelegramStarsService.get_rate(session)

    from app.core.config import config as _cfg

    _yk_env = _cfg.yookassa
    _yk_env_ok = bool(
        _yk_env and _yk_env.yookassa_shop_id and _yk_env.yookassa_secret_key
    )
    _yk_db_ok = bool(
        settings.get("yookassa_shop_id_override")
        and settings.get("yookassa_secret_key_override")
    )
    _yk_toggle = settings.get("ps_yookassa_enabled", "0") == "1"
    _yk_configured = _yk_env_ok or _yk_db_ok
    has_yookassa = _yk_toggle and _yk_configured

    _sbp_toggle = settings.get("ps_sbp_enabled", "0") == "1"
    has_sbp = _sbp_toggle and _yk_configured

    _cb_toggle = settings.get("ps_cryptobot_enabled", "0") == "1"
    has_cryptobot = _cb_toggle and bool(settings.get("cryptobot_token", "").strip())

    stars_amount = TelegramStarsService.rub_to_stars(float(amount), rate=rate)

    builder = InlineKeyboardBuilder()

    if has_yookassa:
        card_labels = {
            "ru": "💳 Банковская карта",
            "en": "💳 Bank card",
            "fa": "💳 کارت بانکی",
        }
        builder.row(
            InlineKeyboardButton(
                text=card_labels.get(lang, card_labels["ru"]),
                callback_data=f"topup:pay:yookassa:{amount}",
            )
        )
    _yk_db_ok = bool(
        settings.get("yookassa_shop_id_override")
        and settings.get("yookassa_secret_key_override")
    )
    _yk_toggle = settings.get("ps_yookassa_enabled", "0") == "1"
    _yk_configured = _yk_env_ok or _yk_db_ok
    has_yookassa = _yk_toggle and _yk_configured

    _sbp_toggle = settings.get("ps_sbp_enabled", "0") == "1"
    has_sbp = _sbp_toggle and _yk_configured

    _cb_toggle = settings.get("ps_cryptobot_enabled", "0") == "1"
    has_cryptobot = _cb_toggle and bool(settings.get("cryptobot_token", "").strip())

    stars_amount = TelegramStarsService.rub_to_stars(float(amount), rate=rate)

    from app.core.config import config as _cfg

    has_yookassa = bool(
        _cfg.yookassa
        and _cfg.yookassa.yookassa_shop_id
        and _cfg.yookassa.yookassa_secret_key
    )
    has_cryptobot = bool(settings.get("cryptobot_token", "").strip())

    builder = InlineKeyboardBuilder()

    if has_yookassa:
        card_labels = {
            "ru": "💳 Банковская карта",
            "en": "💳 Bank card",
            "fa": "💳 کارت بانکی",
        }
        builder.row(
            InlineKeyboardButton(
                text=card_labels.get(lang, card_labels["ru"]),
                callback_data=f"topup:pay:yookassa:{amount}",
            )
        )
    if has_sbp:
        sbp_labels = {"ru": "🏦 СБП", "en": "🏦 SBP", "fa": "🏦 SBP"}
        builder.row(
            InlineKeyboardButton(
                text=sbp_labels.get(lang, sbp_labels["ru"]),
                callback_data=f"topup:pay:sbp:{amount}",
            )
        )

    if has_cryptobot:
        crypto_labels = {
            "ru": "₿ Криптовалюта",
            "en": "₿ Cryptocurrency",
            "fa": "₿ ارز دیجیتال",
        }
        builder.row(
            InlineKeyboardButton(
                text=crypto_labels.get(lang, crypto_labels["ru"]),
                callback_data=f"topup:pay:crypto:{amount}",
            )
        )

    has_freekassa = settings.get("ps_freekassa_enabled", "0") == "1" and bool(
        settings.get("freekassa_shop_id", "").strip()
    )
    if has_freekassa:
        fk_labels = {
            "ru": "🟢 FreeKassa",
            "en": "🟢 FreeKassa",
            "fa": "🟢 FreeKassa",
        }
        builder.row(
            InlineKeyboardButton(
                text=fk_labels.get(lang, fk_labels["ru"]),
                callback_data=f"topup:pay:freekassa:{amount}",
            )
        )

    # Telegram Stars — всегда доступны
    stars_labels = {
        "ru": f"⭐ Telegram Stars ({stars_amount} ⭐)",
        "en": f"⭐ Telegram Stars ({stars_amount} ⭐)",
        "fa": f"⭐ Telegram Stars ({stars_amount} ⭐)",
    }
    builder.row(
        InlineKeyboardButton(
            text=stars_labels.get(lang, stars_labels["ru"]),
            callback_data=f"topup:pay:stars:{amount}",
        )
    )

    builder.row(InlineKeyboardButton(text=t("back", lang), callback_data="topup:menu"))

    amount_labels = {
        "ru": f"💰 Пополнение на <b>{amount} ₽</b>\n\nВыберите способ оплаты:",
        "en": f"💰 Top up <b>{amount} ₽</b>\n\nChoose payment method:",
        "fa": f"💰 شارژ <b>{amount} ₽</b>\n\nروش پرداخت را انتخاب کنید:",
    }
    text = amount_labels.get(lang, amount_labels["ru"])

    from app.bot.utils.media import edit_with_photo, answer_with_photo

    if callback:
        await edit_with_photo(callback, text, reply_markup=builder.as_markup())
    elif message:
        await answer_with_photo(message, text, reply_markup=builder.as_markup())


@router.callback_query(F.data == "enter_promo")
async def ask_promo(callback: CallbackQuery, state: FSMContext) -> None:
    async with AsyncSessionFactory() as session:
        lang = await _get_lang_from_session(callback.from_user.id, session)
    await state.set_state(PromoState.waiting_code)
    from app.bot.utils.media import edit_with_photo

    await edit_with_photo(callback, t("enter_promo", lang), reply_markup=back_kb(lang))
    await _safe_answer(callback)


@router.message(PromoState.waiting_code)
async def process_promo(message: Message, state: FSMContext) -> None:
    code = message.text.strip().upper()
    async with AsyncSessionFactory() as session:
        lang = await _get_lang_from_session(message.from_user.id, session)
        promo = await PromoService(session).apply(code, user_id=message.from_user.id)
        if promo:
            pt = str(promo.promo_type)
            if pt == "balance":
                await UserService(session).add_balance(
                    message.from_user.id, promo.value
                )
                result_text = t("promo_balance", lang, value=promo.value)
            elif pt == "days":
                result_text = t("promo_days", lang, value=int(promo.value))
            else:
                result_text = t("promo_discount", lang, value=promo.value)
            await session.commit()
        else:
            result_text = t("promo_invalid", lang)
        kb = await _get_menu_kb(
            session,
            lang=lang,
            user_id=message.from_user.id,
            is_admin=_is_admin(message.from_user.id),
        )

    await state.clear()
    await message.answer(result_text, reply_markup=kb, parse_mode="HTML")


# ── Support ───────────────────────────────────────────────────────────────────


@router.callback_query(F.data == "support")
async def support_start(callback: CallbackQuery, state: FSMContext) -> None:
    async with AsyncSessionFactory() as session:
        lang = await _get_lang_from_session(callback.from_user.id, session)
        tickets = await SupportService(session).get_for_user(callback.from_user.id)
        ticket_rows = [
            {
                "id": tk.id,
                "subject": tk.subject,
                "status": tk.status.value
                if hasattr(tk.status, "value")
                else str(tk.status),
            }
            for tk in tickets
        ]
        photo = await BotSettingsService(session).get("photo_support")

    builder = InlineKeyboardBuilder()
    if ticket_rows:
        for tk in ticket_rows[:5]:
            st_icon = {"open": "🔵", "in_progress": "🟡", "closed": "⚫"}.get(
                tk["status"], "❓"
            )
            builder.row(
                InlineKeyboardButton(
                    text=f"{st_icon} #{tk['id']} — {tk['subject'][:28]}",
                    callback_data=f"support:ticket:{tk['id']}",
                )
            )

    builder.row(
        InlineKeyboardButton(text=t("new_ticket", lang), callback_data="support:new")
    )
    builder.row(
        InlineKeyboardButton(text=t("back_main", lang), callback_data="back_main")
    )

    if ticket_rows:
        text = (
            t("support_title", lang)
            + "\n\n"
            + t("support_tickets", lang, count=len(ticket_rows))
        )
    else:
        text = t("support_title", lang) + "\n\n" + t("support_no_tickets", lang)

    from app.bot.utils.media import edit_with_photo

    await edit_with_photo(
        callback, text, reply_markup=builder.as_markup(), photo=photo or None
    )
    await _safe_answer(callback)


@router.callback_query(F.data == "support:new")
async def support_new(callback: CallbackQuery, state: FSMContext) -> None:
    async with AsyncSessionFactory() as session:
        lang = await _get_lang_from_session(callback.from_user.id, session)
    await state.set_state(SupportState.waiting_subject)
    from app.bot.utils.media import edit_with_photo

    await edit_with_photo(
        callback,
        t("ticket_subject", lang),
        reply_markup=back_kb(lang),
    )
    await _safe_answer(callback)


@router.callback_query(F.data.startswith("support:ticket:"))
async def support_open_ticket(callback: CallbackQuery, state: FSMContext) -> None:
    ticket_id = int(callback.data.split(":")[2])
    async with AsyncSessionFactory() as session:
        lang = await _get_lang_from_session(callback.from_user.id, session)
        ticket = await SupportService(session).get_by_id(ticket_id)
        if not ticket or ticket.user_id != callback.from_user.id:
            await callback.answer(t("ticket_not_found", lang), show_alert=True)
            return
        subject = ticket.subject
        st_val = (
            ticket.status.value
            if hasattr(ticket.status, "value")
            else str(ticket.status)
        )
        msgs = [
            {"is_admin": bool(m.is_admin), "text": m.text}
            for m in (ticket.messages[-5:] if ticket.messages else [])
        ]

    who_support = "🛡 " + (
        "Поддержка" if lang == "ru" else ("Support" if lang == "en" else "پشتیبانی")
    )
    who_user = "👤 " + ("Вы" if lang == "ru" else ("You" if lang == "en" else "شما"))

    text = f"💬 <b>#{ticket_id} — {subject}</b>\n\n"
    for m in msgs:
        who = who_support if m["is_admin"] else who_user
        text += f"<b>{who}:</b> {m['text'][:200]}\n\n"

    status_labels = {
        "ru": {
            "open": "🔵 Открыт",
            "in_progress": "🟡 В работе",
            "closed": "⚫ Закрыт",
        },
        "en": {
            "open": "🔵 Open",
            "in_progress": "🟡 In progress",
            "closed": "⚫ Closed",
        },
        "fa": {"open": "🔵 باز", "in_progress": "🟡 در حال بررسی", "closed": "⚫ بسته"},
    }
    status_label = status_labels.get(lang, status_labels["ru"]).get(st_val, st_val)
    text += f"{'Статус' if lang == 'ru' else ('Status' if lang == 'en' else 'وضعیت')}: {status_label}"

    builder = InlineKeyboardBuilder()
    if st_val != "closed":
        builder.row(
            InlineKeyboardButton(
                text=t("write_reply", lang), callback_data=f"support:reply:{ticket_id}"
            )
        )
        builder.row(
            InlineKeyboardButton(
                text=t("close_ticket", lang), callback_data=f"support:close:{ticket_id}"
            )
        )
    builder.row(InlineKeyboardButton(text=t("back", lang), callback_data="support"))

    await callback.message.edit_text(
        text, reply_markup=builder.as_markup(), parse_mode="HTML"
    )
    await _safe_answer(callback)


@router.callback_query(F.data.startswith("support:reply:"))
async def support_reply_start(callback: CallbackQuery, state: FSMContext) -> None:
    ticket_id = int(callback.data.split(":")[2])
    async with AsyncSessionFactory() as session:
        lang = await _get_lang_from_session(callback.from_user.id, session)
    await state.set_state(SupportState.replying_ticket)
    await state.update_data(ticket_id=ticket_id)
    reply_prompt = {
        "ru": f"✏️ Введите ваш ответ по тикету #{ticket_id}:",
        "en": f"✏️ Enter your reply for ticket #{ticket_id}:",
        "fa": f"✏️ پاسخ خود را برای تیکت #{ticket_id} وارد کنید:",
    }
    from app.bot.utils.media import edit_with_photo

    await edit_with_photo(
        callback,
        reply_prompt.get(lang, reply_prompt["ru"]),
        reply_markup=back_kb(lang),
    )
    await _safe_answer(callback)


@router.callback_query(F.data.startswith("support:close:"))
async def support_close_ticket(callback: CallbackQuery) -> None:
    ticket_id = int(callback.data.split(":")[2])
    async with AsyncSessionFactory() as session:
        lang = await _get_lang_from_session(callback.from_user.id, session)
        ticket = await SupportService(session).get_by_id(ticket_id)
        if not ticket or ticket.user_id != callback.from_user.id:
            await callback.answer(t("ticket_not_found", lang), show_alert=True)
            return
        from app.models.support import TicketStatus

        await SupportService(session).set_status(ticket_id, TicketStatus.CLOSED)
        await session.commit()
        kb = await _get_menu_kb(
            session,
            lang=lang,
            user_id=callback.from_user.id,
            is_admin=_is_admin(callback.from_user.id),
        )

    from app.bot.utils.media import edit_with_photo

    await edit_with_photo(
        callback,
        t("ticket_closed", lang, id=ticket_id),
        reply_markup=kb,
    )
    await _safe_answer(callback)


@router.message(SupportState.replying_ticket)
async def support_reply_message(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    text = message.text.strip()

    async with AsyncSessionFactory() as session:
        lang = await _get_lang_from_session(message.from_user.id, session)
        msg = await SupportService(session).add_message(
            ticket_id=ticket_id,
            sender_id=message.from_user.id,
            text=text,
            is_admin=False,
        )
        await session.commit()
        kb = await _get_menu_kb(
            session,
            lang=lang,
            user_id=message.from_user.id,
            is_admin=_is_admin(message.from_user.id),
        )

    await state.clear()

    if msg:
        await message.answer(
            t("ticket_reply_sent", lang, id=ticket_id),
            reply_markup=kb,
            parse_mode="HTML",
        )

        notify = TelegramNotifyService()
        uname = (
            f"@{message.from_user.username}"
            if message.from_user.username
            else f"<code>{message.from_user.id}</code>"
        )
        safe_text = escape_html(truncate(text, 300))
        for admin_id in config.telegram.telegram_admin_ids:
            await notify.send_message(
                admin_id,
                f"💬 <b>Ответ в тикете #{ticket_id}</b>\n\n👤 {uname}:\n{safe_text}",
            )
    else:
        await message.answer(t("ticket_not_found", lang), reply_markup=kb)


@router.message(SupportState.waiting_subject)
async def support_subject(message: Message, state: FSMContext) -> None:
    async with AsyncSessionFactory() as session:
        lang = await _get_lang_from_session(message.from_user.id, session)
    subject = message.text.strip()
    too_short = {
        "ru": "Тема слишком короткая. Введите ещё раз:",
        "en": "Subject too short. Try again:",
        "fa": "موضوع خیلی کوتاه است. دوباره وارد کنید:",
    }
    if len(subject) < 3:
        await message.answer(too_short.get(lang, too_short["ru"]))
        return
    await state.update_data(subject=subject)
    await state.set_state(SupportState.waiting_message)
    await message.answer(
        t("ticket_message", lang, subject=subject),
        reply_markup=back_kb(lang),
        parse_mode="HTML",
    )


@router.message(SupportState.waiting_message)
async def support_message(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    subject = data.get("subject", "—")
    text = message.text.strip()

    async with AsyncSessionFactory() as session:
        lang = await _get_lang_from_session(message.from_user.id, session)
        ticket = await SupportService(session).create_ticket(
            user_id=message.from_user.id,
            subject=subject,
            first_message=text,
        )
        await session.commit()
        ticket_id = ticket.id
        kb = await _get_menu_kb(
            session,
            lang=lang,
            user_id=message.from_user.id,
            is_admin=_is_admin(message.from_user.id),
        )

    await state.clear()
    await message.answer(
        t("ticket_created", lang, id=ticket_id, subject=escape_html(subject)),
        reply_markup=kb,
        parse_mode="HTML",
    )

    notify = TelegramNotifyService()
    uname = (
        f"@{message.from_user.username}"
        if message.from_user.username
        else f"<code>{message.from_user.id}</code>"
    )
    safe_subject = escape_html(subject)
    safe_text = escape_html(truncate(text, 300))
    from app.services.notification import notification_manager
    await notification_manager.broadcast({
        "type": "new_ticket",
        "data": {
            "ticket_id": ticket_id,
            "user_id": message.from_user.id,
            "subject": subject,
        },
    })
    for admin_id in config.telegram.telegram_admin_ids:
        await notify.send_message(
            admin_id,
            f"🆕 <b>Новый тикет #{ticket_id}</b>\n\n👤 {uname}\n📌 {safe_subject}\n\n💬 {safe_text}",
        )


async def _get_bot_username() -> str:
    try:
        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode

        bot = Bot(
            token=config.telegram.telegram_bot_token.get_secret_value(),
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        me = await bot.get_me()
        await bot.session.close()
        return me.username or ""
    except Exception:
        return ""
