"""
Профиль пользователя + дополнительные фичи:
- /profile — полная информация о пользователе
- /id — быстро узнать свой Telegram ID
- Уведомление за 3 дня до истечения подписки (вызывается из vpn_tasks)
"""

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.core.database import AsyncSessionFactory
from app.services.user import UserService
from app.services.vpn_key import VpnKeyService
from app.services.payment import PaymentService
from app.services.referral import ReferralService
from app.services.bot_settings import BotSettingsService
from app.services.i18n import t, get_lang

router = Router()


async def _build_profile_text(user_id: int, lang: str = "ru") -> tuple[str, object]:
    """Собирает текст профиля и клавиатуру."""
    async with AsyncSessionFactory() as session:
        user = await UserService(session).get_by_id(user_id)
        if not user:
            return t("profile_not_found", lang), None

        keys = await VpnKeyService(session).get_all_for_user(user_id)
        payments = await PaymentService(session).get_all(user_id=user_id, limit=5)
        ref_count = await ReferralService(session).count_referrals(user_id)
        settings = await BotSettingsService(session).get_all()

        active_keys = [
            k
            for k in keys
            if str(k.status.value if hasattr(k.status, "value") else k.status)
            == "active"
        ]
        expired_keys = [
            k
            for k in keys
            if str(k.status.value if hasattr(k.status, "value") else k.status)
            != "active"
        ]

        # Nearest expiry
        nearest_exp = None
        for k in active_keys:
            if k.expires_at:
                if nearest_exp is None or k.expires_at < nearest_exp:
                    nearest_exp = k.expires_at

        balance = float(user.balance or 0)
        uname = f"@{user.username}" if user.username else "—"
        reg_date = user.created_at.strftime("%d.%m.%Y") if user.created_at else "—"

        # Referral link
        bot_username = settings.get("bot_username_cache", "")
        ref_link = (
            f"https://t.me/{bot_username}?start={user.referral_code}"
            if bot_username and user.referral_code
            else None
        )

        # Total spent
        from app.models.payment import PaymentStatus

        total_spent = sum(
            float(p.amount)
            for p in payments
            if str(p.status.value if hasattr(p.status, "value") else p.status)
            == PaymentStatus.SUCCEEDED.value
        )

    lines = [
        t("profile_title", lang) + "\n",
        t("profile_id", lang, id=user_id),
        t("profile_name", lang, name=user.full_name),
        t("profile_username", lang, username=uname),
        t("profile_reg", lang, date=reg_date),
        "",
        t("profile_balance", lang, balance=balance),
        t("profile_spent", lang, spent=total_spent),
        "",
        t("profile_active", lang, count=len(active_keys)),
        t("profile_archive", lang, count=len(expired_keys)),
    ]

    if nearest_exp:
        from datetime import datetime, timezone

        days_left = (nearest_exp - datetime.now(timezone.utc)).days
        exp_str = nearest_exp.strftime("%d.%m.%Y")
        if days_left <= 3:
            lines.append(t("profile_expiry_warn", lang, date=exp_str, days=days_left))
        else:
            lines.append(t("profile_expiry", lang, date=exp_str))

    lines += [
        "",
        t("profile_referrals", lang, count=ref_count),
    ]

    if ref_link:
        lines.append(t("profile_ref_link", lang, link=ref_link))

    if user.referral_code:
        lines.append(t("profile_ref_code", lang, code=user.referral_code))

    text = "\n".join(lines)

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=t("btn_my_subs", lang), callback_data="my_keys")
    )
    builder.row(
        InlineKeyboardButton(text=t("btn_balance", lang), callback_data="balance"),
        InlineKeyboardButton(text=t("btn_support", lang), callback_data="support"),
    )
    builder.row(
        InlineKeyboardButton(text=t("back_main", lang), callback_data="back_main")
    )

    return text, builder.as_markup()


@router.message(Command("profile", "me", "я"))
async def cmd_profile(message: Message) -> None:
    user_id = message.from_user.id
    async with AsyncSessionFactory() as session:
        _u = await UserService(session).get_by_id(user_id)
        _s = await BotSettingsService(session).get_all()
    lang = get_lang(_s, _u.language if _u and _u.language else None)
    text, kb = await _build_profile_text(user_id, lang)
    async with AsyncSessionFactory() as session:
        photo = await BotSettingsService(session).get("photo_profile")
    from app.bot.utils.media import answer_with_photo

    await answer_with_photo(message, text, reply_markup=kb, photo=photo or None)


@router.callback_query(F.data == "profile")
async def show_profile(callback: CallbackQuery) -> None:
    await callback.answer()
    user_id = callback.from_user.id
    async with AsyncSessionFactory() as session:
        _u = await UserService(session).get_by_id(user_id)
        _s = await BotSettingsService(session).get_all()
    lang = get_lang(_s, _u.language if _u and _u.language else None)
    text, kb = await _build_profile_text(user_id, lang)
    async with AsyncSessionFactory() as session:
        photo = await BotSettingsService(session).get("photo_profile")
    from app.bot.utils.media import edit_with_photo

    try:
        await edit_with_photo(callback, text, reply_markup=kb, photo=photo or None)
    except Exception:
        from app.bot.utils.media import answer_with_photo

        await answer_with_photo(
            callback.message, text, reply_markup=kb, photo=photo or None
        )


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    """Быстро узнать свой Telegram ID."""
    await message.answer(
        f"🆔 Ваш Telegram ID: <code>{message.from_user.id}</code>",
        parse_mode="HTML",
    )


@router.message(Command("keys", "подписки"))
async def cmd_keys(message: Message) -> None:
    """Быстрый просмотр активных подписок."""
    async with AsyncSessionFactory() as session:
        keys = await VpnKeyService(session).get_active_for_user(message.from_user.id)

    if not keys:
        await message.answer("📦 У вас нет активных подписок.")
        return

    lines = ["🔑 <b>Ваши активные подписки:</b>\n"]
    for k in keys:
        exp = k.expires_at.strftime("%d.%m.%Y") if k.expires_at else "—"
        lines.append(f"• <b>{k.name or f'Подписка #{k.id}'}</b> — до {exp}")
        if k.access_url:
            lines.append(f"  <code>{k.access_url}</code>")

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📦 Все подписки", callback_data="my_keys"))
    await message.answer(
        "\n".join(lines), reply_markup=builder.as_markup(), parse_mode="HTML"
    )
