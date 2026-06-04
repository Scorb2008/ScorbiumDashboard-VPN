"""
Главное меню бота — строится динамически из keyboard_layout в bot_settings.
"""

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from app.bot.keyboards.builder import btn

_DEFAULT_LAYOUT = [
    [{"id": "my_keys", "label": "🔑 Мои подписки", "callback": "my_keys"}],
    [{"id": "buy", "label": "💳 Купить", "callback": "buy"}],
    [
        {"id": "balance", "label": "💰 Баланс", "callback": "balance"},
        {"id": "promo", "label": "🎁 Промокод", "callback": "enter_promo"},
    ],
    [
        {"id": "connect", "label": "📲 Как подключить", "callback": "connect:menu"},
        {"id": "about", "label": "ℹ️ О проекте", "callback": "about"},
    ],
    [
        {"id": "profile", "label": "👤 Профиль", "callback": "profile"},
        {"id": "servers", "label": "🌐 Серверы", "callback": "servers"},
    ],
    [{"id": "top_referrers", "label": "🏆 Топ рефереров", "callback": "top_referrers"}],
    [{"id": "support", "label": "💬 Поддержка", "callback": "support"}],
    [{"id": "cabinet", "label": "📱 Кабинет", "web_app": ""}],
    [{"id": "admin_menu", "label": "ℹ️ Админ меню", "callback": "admin:panel"}],
    [{"id": "admin_panel", "label": "⚙️ Админ панель", "url": ""}],
]


def main_menu_kb(
    support_url: str = "",
    layout: list = None,
    styles: dict = None,
    emojis: dict = None,
    **kwargs,
) -> InlineKeyboardMarkup:
    if layout is None:
        layout = _DEFAULT_LAYOUT
    if styles is None:
        styles = {}
    if emojis is None:
        emojis = {}

    builder = InlineKeyboardBuilder()

    for row in layout:
        if not row:
            continue
        row_btns = []
        for b in row:
            bid = b.get("id", "")
            label = b.get("label", "")
            callback = b.get("callback", bid)
            url = b.get("url", "")
            web_app_url = b.get("web_app", "")
            style = styles.get(bid) or None
            emoji_id = emojis.get(bid) or None

            if web_app_url:
                row_btns.append(
                    btn(label, web_app=web_app_url, style=style, emoji_id=emoji_id)
                )
            elif url:
                row_btns.append(btn(label, url=url, style=style, emoji_id=emoji_id))
            elif bid == "support" and support_url:
                row_btns.append(
                    btn(label, url=support_url, style=style, emoji_id=emoji_id)
                )
            else:
                row_btns.append(
                    btn(label, callback_data=callback, style=style, emoji_id=emoji_id)
                )

        if len(row_btns) == 1:
            builder.row(row_btns[0])
        else:
            builder.row(*row_btns)

    return builder.as_markup()


def back_kb(lang: str = "ru") -> InlineKeyboardMarkup:
    labels = {"ru": "◀️ Главное меню", "en": "◀️ Main menu", "fa": "◀️ منوی اصلی"}
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=labels.get(lang, "◀️ Главное меню"), callback_data="back_main"
        )
    )
    return builder.as_markup()
