from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def _copy_button_text(lang: str) -> str:
    return {
        "ru": "Скопировать подписку 🔑",
        "en": "Copy subscription 🔑",
        "fa": "کپی اشتراک 🔑",
    }.get(lang, "Copy subscription 🔑")


def _open_button_text(lang: str) -> str:
    return {
        "ru": "Перейти к подписке ↗️",
        "en": "Open subscription ↗️",
        "fa": "رفتن به اشتراک ↗️",
    }.get(lang, "Open subscription ↗️")


def subscription_link_kb(
    access_url: str,
    *,
    lang: str = "ru",
    include_connect: bool = False,
    connect_callback_data: str = "connect:menu",
    connect_text: str | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=_copy_button_text(lang),
            copy_text=CopyTextButton(text=access_url),
        )
    )
    builder.row(InlineKeyboardButton(text=_open_button_text(lang), url=access_url))
    if include_connect and connect_text:
        builder.row(
            InlineKeyboardButton(
                text=connect_text,
                callback_data=connect_callback_data,
            )
        )
    return builder.as_markup()
