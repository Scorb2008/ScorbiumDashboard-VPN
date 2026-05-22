"""Handler for language selection."""

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.core.database import AsyncSessionFactory
from app.services.user import UserService
from app.services.bot_settings import BotSettingsService
from app.services.i18n import t, get_lang, STRINGS

router = Router()

_LANG_LABELS = {
    "ru": "🇷🇺 Русский",
    "en": "🇬🇧 English",
    "fa": "🇮🇷 فارسی",
}


def language_kb(current_lang: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for code, label in _LANG_LABELS.items():
        check = "✅ " if code == current_lang else ""
        builder.row(
            InlineKeyboardButton(
                text=f"{check}{label}",
                callback_data=f"set_lang:{code}",
            )
        )
    builder.row(
        InlineKeyboardButton(text="◀️ Назад / Back / بازگشت", callback_data="back_main")
    )
    return builder.as_markup()


@router.callback_query(F.data == "language")
async def show_language(callback: CallbackQuery) -> None:
    async with AsyncSessionFactory() as session:
        user = await UserService(session).get_by_id(callback.from_user.id)
        settings = await BotSettingsService(session).get_all()

    user_lang = user.language if user and user.language else None
    lang = get_lang(settings, user_lang)
    photo = settings.get("photo_language") or None

    from app.bot.utils.media import edit_with_photo

    await edit_with_photo(
        callback,
        t("choose_language", lang),
        reply_markup=language_kb(lang),
        photo=photo,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("set_lang:"))
async def set_language(callback: CallbackQuery) -> None:
    new_lang = callback.data.split(":")[1]
    if new_lang not in STRINGS:
        await callback.answer("Unknown language", show_alert=True)
        return

    async with AsyncSessionFactory() as session:
        svc = UserService(session)
        user = await svc.get_by_id(callback.from_user.id)
        if user:
            user.language = new_lang
            await session.commit()

    from app.bot.utils.media import edit_with_photo

    await edit_with_photo(
        callback, t("language_set", new_lang), reply_markup=language_kb(new_lang)
    )
    await callback.answer(t("language_set", new_lang))
